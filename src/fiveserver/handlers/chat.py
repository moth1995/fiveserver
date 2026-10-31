"""PES5 and PES6 lobby, room, and private chat routing."""


from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Protocol

from ..database import Profile
from ..protocol.dispatcher import ALL_GAMES, SIX_GAMES, PacketContext, ServiceRole, packet_handler
from ..protocol.records import Pes5ChatPrefix, Pes6ChatPrefix
from ..protocol.packets import ChatRequest, decode_chat
from ..protocol.formats import ChatPrefixEncoder, active_formats
from .lobby_state import Lobby


MAX_CHAT_HISTORY = 50
CHAT_HISTORY_DAYS = 5
CHAT_PAYLOAD_TEXT = 126
PES5_CHAT_NAME = 16
PES6_CHAT_NAME = 48


class ChatPacketId(IntEnum):
    """Packet IDs used by PES5/PES6 chat."""
    SEND = 0x4400
    RECEIVE = 0x4402


class ChatKind:
    """Original two-byte chat routing values for lobby, private, room, match, and stadium chat."""
    LOBBY = b"\x00\x01"
    PRIVATE = b"\x00\x02"
    PES5_ROOM = b"\x01\x02"
    PES6_ROOM = b"\x01\x08"
    PES6_MATCH = b"\x01\x05"
    PES6_STADIUM = b"\x01\x07"


@dataclass(frozen=True)
class ChatMessage:
    """One chat-history entry with sender, optional recipient, and original special field."""
    sender: Profile
    text: bytes
    recipient_id: int | None
    special: bytes
    timestamp: datetime


class ChatContext(PacketContext, Protocol):
    """Connection state needed to route and replay chat."""
    lobbies: list[Lobby]
    lobby_id: int | None
    room_id: int
    selected_profile: Profile | None
    settings: dict[str, Any]


def encode_pes5_chat_prefix(kind: bytes, special: bytes, profile: Profile) -> bytes:
    """Encode a PES5 prefix without interpreting client-provided routing fields."""
    return Pes5ChatPrefix(kind, special, profile.id, profile.name.encode("utf-8")).pack()


def encode_pes6_chat_prefix(kind: bytes, special: bytes, profile: Profile) -> bytes:
    """Encode a PES6 prefix without interpreting client-provided routing fields."""
    return Pes6ChatPrefix(kind, special, profile.id, profile.name.encode("utf-8")).pack()


# TODO(0x4402): the pes5.exe parser (0x0083aa30) reads u8 kind | i32 special |
# i32 profile_id | name[16] | text[128]. The text field is fixed at 128 bytes, so
# the 2 trailing zero bytes are not a separate field: 126 bytes of text plus 2
# zeros fill it and keep it NUL-terminated. "special" is the colour override noted
# in TODO(0x4212) in lobbies.py.
def _format(kind: bytes, special: bytes, profile: Profile, message: bytes, *, encoder: ChatPrefixEncoder) -> bytes:
    return encoder(kind, special, profile) + message[:CHAT_PAYLOAD_TEXT] + bytes(2)


async def send_history(ctx: ChatContext, lobby: Lobby, *, encoder: ChatPrefixEncoder | None = None) -> None:
    """Replay recent lobby or private messages to a player entering a lobby."""
    profile = ctx.selected_profile
    if profile is None:
        return
    cutoff = datetime.now() - timedelta(days=CHAT_HISTORY_DAYS)
    async with lobby.lock:
        lobby.chat_history[:] = [item for item in lobby.chat_history if item.timestamp > cutoff]
        messages = tuple(lobby.chat_history)
    for item in messages:
        if item.recipient_id is not None and profile.id not in (item.sender.id, item.recipient_id):
            continue
        await ctx.reply(
            ChatPacketId.RECEIVE,
            _format(ChatKind.LOBBY, item.special, item.sender, item.text, encoder=encoder or active_formats(ctx).chat_prefix),
        )


async def broadcast_system(lobby: Lobby, text: str) -> None:
    """Append a SYSTEM message to history and send it to current lobby occupants."""
    profile = Profile(0, 0, 0, "SYSTEM")
    message = text.encode("utf-8")
    async with lobby.lock:
        lobby.chat_history.append(ChatMessage(profile, message, None, bytes(4), datetime.now()))
        del lobby.chat_history[:-MAX_CHAT_HISTORY]
        recipients = tuple(lobby.players.values())
    for recipient in recipients:
        data = _format(ChatKind.LOBBY, bytes(4), profile, message, encoder=active_formats(recipient).system_chat_prefix)
        await recipient.reply(ChatPacketId.RECEIVE, data)


def _filter_message(message: bytes, config: object) -> bytes:
    if not isinstance(config, dict):
        return message
    text = message.decode("utf-8")
    if any(word in text for word in config.get("bannedWords", [])):
        return ("[" + str(config.get("warningMessage", "")) + "]").encode("utf-8")
    return message


def _route_chat(
    ctx: ChatContext, lobby: Lobby, request: ChatRequest, *, room_kinds: tuple[bytes, ...]
) -> tuple[tuple[Any, ...], int | None] | None:
    if request.kind == ChatKind.LOBBY:
        return tuple(lobby.players.values()), None
    if request.kind == ChatKind.PRIVATE:
        recipient = next((peer for peer in lobby.players.values()
                          if peer.selected_profile and peer.selected_profile.id == request.recipient_id), None)
        if recipient is None:
            return (), request.recipient_id
        return ((recipient, ctx) if recipient is not ctx else (recipient,)), request.recipient_id
    if request.kind not in room_kinds:
        return None
    room = next((item for item in lobby.rooms.values() if item.id == ctx.room_id), None)
    return (tuple(room.players) if room is not None else ()), None


async def _chat(ctx: ChatContext, payload: bytes, *, room_kinds: tuple[bytes, ...]) -> None:
    if ctx.lobby_id is None or ctx.selected_profile is None:
        raise ValueError("Chat requires a selected lobby and profile")
    request = decode_chat(payload)
    lobby = ctx.lobbies[ctx.lobby_id]
    message = _filter_message(request.message, ctx.settings.get("Chat", {}))
    data = _format(request.kind, request.special, ctx.selected_profile, message, encoder=active_formats(ctx).chat_prefix)
    async with lobby.lock:
        route = _route_chat(ctx, lobby, request, room_kinds=room_kinds)
        if route is None:
            return
        recipients, recipient_id = route
        if request.kind in (ChatKind.LOBBY, ChatKind.PRIVATE) and recipients:
            lobby.chat_history.append(
                ChatMessage(ctx.selected_profile, message, recipient_id, request.special, datetime.now())
            )
            del lobby.chat_history[:-MAX_CHAT_HISTORY]
    for peer in recipients:
        await peer.reply(ChatPacketId.RECEIVE, data)


@packet_handler(ChatPacketId.SEND, games=ALL_GAMES, role=ServiceRole.MAIN)
async def chat_pes5(ctx: PacketContext, request: object) -> None:
    """Handle PES5 chat using the one-byte wire chat kind."""
    assert isinstance(request, bytes)
    await _chat(ctx, request, room_kinds=(ChatKind.PES5_ROOM,))  # type: ignore[arg-type]


@packet_handler(ChatPacketId.SEND, games=SIX_GAMES, role=ServiceRole.MAIN, override=True)
async def chat_pes6(ctx: PacketContext, request: object) -> None:
    """Handle PES6 chat using two-byte kinds and 48-byte profile names."""
    assert isinstance(request, bytes)
    await _chat(ctx, request, room_kinds=(ChatKind.PES6_ROOM, ChatKind.PES6_MATCH, ChatKind.PES6_STADIUM))  # type: ignore[arg-type]
