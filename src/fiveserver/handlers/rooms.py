"""Room creation and list packets for PES5 and PES6 game services."""


import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from ..database import Profile, ProfileStats, User, UserRepository
from ..protocol.dispatcher import ALL_GAMES, FIVE_GAMES, SIX_GAMES, PacketContext, ServiceRole, packet_handler
from ..protocol.formats import ChatPrefixEncoder, WireFormats, active_formats, decode_room_creation
from ..protocol.records import Pes5ChallengeRecord, Pes5RoomPeerNames
from ..protocol.packets import (
    RoomCreateRequest, TeamSelectRequest,
)
from .lobby_state import Lobby, LobbyPeer, encode_pes5_player, player_info
from .profile_info import encode_pes5_profile, format_profile_info
from .room_codecs import (
    DEFAULT_ROOM_PHASE, encode_pes5_room, participation_status,
)

if TYPE_CHECKING:
    from .matches import Match6, TeamSelection


# TODO(room status codes): VERIFIED in pes5.exe (texts from game text folder 512).
# 0x4311 create reply (result slot 0x1f, read by FUN_004a5a90; dialog chosen in
# FUN_0048adc0):
#   0          -> room created
#   0xFFFFFE0B -> 512:3 "Includes banned words."   (the only code with own text)
#   any other  -> 512:2 "Unable to create match room."  (our ROOM_NAME_TAKEN
#                 ffffff10 lands here, which is the right text)
# 0x4321 join reply, as handled by the room-list screen FUN_00489900 states 8 / 9
# (the guest waits under 512:5 "Awaiting opponent's reply. Please wait a moment."):
#   0          -> enters the room (state 10)
#   any other  -> state 0xc: 512:5 stays for ~1.5 s (timer FUN_0049e0b0(4, 0x5dc)),
#                 then 512:1 "Match cannot take place at present.", then back to
#                 the room list. The client groups fffffe06 / fe08 / fe09 /
#                 fdf8 / fdf9 / fdfc (/ fdfd in state 9) separately, but every
#                 branch ends in that same state 0xc, so all non-zero codes show
#                 the same text. ROOM_JOIN_ERROR (1) is therefore fine.
# 0x4326 cancel-challenge reply: 0, fffffe09, fffffe06, fffffdf8..fffffdfc and
#   fffffe00 close the wait dialog and return to the room list (state 2); any
#   other value is ignored and the client keeps waiting.
# PES5 never shows 512:9 "Incorrect password." (no code references 0x02000009);
# ROOM_PASSWORD_ERROR (fffffdda) and FORCED_CANCEL_STATUS are only sent by the
# PES6 handlers. Not traced (out of scope here): the PES6 texts for those.
# Chat send failure text is 512:10 "Chat message was not delivered."
ROOM_NAME_TAKEN = bytes.fromhex("ffffff10")
ROOM_JOIN_ERROR = bytes.fromhex("00000001")
ROOM_PASSWORD_ERROR = bytes.fromhex("fffffdda")
PES5_CHALLENGE_MIN_SIZE = 21
# TODO(0x4320 / 0x4322): verified against pes5.exe.
# 0x4320 (join request, builder 0x0083be90) is u32 room_id | password[16] | u8
# ping_grade. The grade comes from FUN_004a8540: the client pings each room owner
# itself (FUN_004a7210 asks the server for the owner's address with 0x4b00, then
# sends a UDP probe and stores the round trip), and maps it to 4 = under 100,
# 3 = under 250, 2 = 250 or more, 1 = not measured (values outside 1..4 become 1).
# 0x4322 (challenge to the owner, parser 0x0083bf20) is the full 0x4103 profile
# record without its status, 90 bytes: the 57 bytes we send, the 5 u16 triples
# (30 bytes), then u8 -> card +0x3f, u8 -> card +0x3e, u8 -> card +0x3c
# (language). So Pes5ChallengeRecord is: profile[57] + triples[30] (we send zeros),
# ping[1] = card +0x3f, reserved[2] = card +0x3e and the LANGUAGE byte (should be
# the challenger's language, see TODO(0x4103) in profile_info.py).
# The profile card (FUN_004abd10) shows +0x3f as a 4-frame icon: 2 -> frame 0,
# 3 -> 1, 4 -> 2, anything else -> frame 3. That is the connection grade the owner
# sees on the challenger's card, most likely the speed_icon texture (3 / 2 / 1
# chevrons); the frame-to-picture order is not verified (UI bins not decoded).
PES5_CHALLENGE_PING_OFFSET = 20
PES5_CHALLENGE_PROFILE_SIZE = 0x57
# TODO(0x4321): this 76-byte counting sequence (05 06 07 ... 50) is probe filler,
# not data; the original server called it the "ultimate packet". The pes5.exe
# parser (FUN_0083c240) reads only the first 52 bytes of the reply:
#   +0   u32 BE  status (non-zero = error; nothing else is read)
#   +4   16 B    opponent (room owner) name        -> protocolState+0x120F5
#   +20  32 B    opponent team bitmask             -> protocolState+0x12106
# The remaining 28 bytes are ignored. The 0x4321 case of FUN_00498f40 then copies
# the name to DAT_03b7c9bb and the mask to DAT_03b7cb7c: the same
# {name[16], team_mask[32]} record as 0x4330 (see the TODO in _accept_challenge)
# and 0x4a11. With the counting bytes the guest gets the name 05 06 .. 14 (control
# characters) and a mask made of 15 16 .. 34. FUN_00498c10 reads DAT_03b7cb7c to
# filter the opponent's side of the online team-select screen
# (BuildTeamSelectAllCategorySlots; bit layout in TODO(0x4101), profile_info.py).
# Fix: send bytes(4) + owner name padded to 16 + the owner's 0x4101 team mask (32).
# Not verified: whether the guest can browse the opponent's side in practice, and
# what UI shows the opponent name (FUN_00491b40 copies DAT_03b7c9bb to
# DAT_03b7c9cc; its getter FUN_004932b0 is used by FUN_004829e0 / FUN_00484a70,
# which were not decompiled).
PES5_CHALLENGE_ACCEPT_TAIL = bytes(range(5, 0x51))
PARTICIPATION_ROSTER_ERROR = bytes.fromhex("00000001")
PES5_READY_PLAYERS = 2
PES5_MATCH_PHASE = b"\x04"
ROOM_STATUS_SIZE = 4
DEFAULT_MATCH_TIME = 5
PREGAME_FIRST_PHASE = 2
PREGAME_LAST_PHASE = 6
MATCH_FINISHED_PHASE = 8
MATCH_SERIES_ENDING_PHASE = 10
FORCED_CANCEL_SECONDS = 10
FORCED_CANCEL_STATUS = bytes.fromhex("fffffdb6")
LOGGER = logging.getLogger(__name__)


class RoomPacketId(IntEnum):
    """Packet IDs for room creation, listing, challenges, participation, and match start."""
    LIST_REQUEST = 0x4300
    LIST_START = 0x4301
    LIST_ENTRY = 0x4302
    LIST_END = 0x4303
    UPDATE = 0x4306
    CREATE_REQUEST = 0x4310
    CREATE_REPLY = 0x4311
    JOIN_REQUEST = 0x4320
    JOIN_REPLY = 0x4321
    EXIT_REQUEST = 0x432A
    EXIT_REPLY = 0x432B
    REMOVED = 0x4305
    PLAYER_LEFT_ROOM = 0x4331
    CHALLENGE = 0x4322
    CHALLENGE_RESPONSE = 0x4323
    PEER_ENTERED = 0x4330
    PEER_LIST_START = 0x4346
    PEER_LIST_ENTRY = 0x4347
    PEER_LIST_END = 0x4348
    OWNER_REQUEST = 0x4349
    OWNER_REPLY = 0x434A
    RENAME_REQUEST = 0x434D
    RENAME_REPLY = 0x434E
    SETTINGS_RELAY = 0x4350
    MATCH_TIME_REQUEST = 0x4364
    MATCH_TIME_REPLY = 0x4365
    STUN_REQUEST = 0x4345
    SPECTATOR_REQUEST = 0x4366
    SPECTATOR_REPLY = 0x4367
    TEAM_SELECT_REQUEST = 0x4366
    SPECTATOR_PEERS = 0x4351
    SPECTATOR_PEERS_REPLY = 0x4352
    QUICK_SEARCH_REQUEST = 0x6020
    QUICK_SEARCH_REPLY = 0x6021
    PARTICIPATE_REQUEST = 0x4363
    PARTICIPATE_REPLY = 0x4364
    PARTICIPATION_UPDATE = 0x4365
    MATCH_START_REQUEST = 0x4360
    MATCH_START_REPLY = 0x4361
    MATCH_START_UPDATE = 0x4362
    READY_REQUEST = 0x436F
    READY_REPLY = 0x4370
    READY_UPDATE = 0x4371
    PHASE_UPDATE = 0x4344
    CANCEL_CHALLENGE_REQUEST = 0x4325
    CANCEL_CHALLENGE_REPLY = 0x4326
    CHALLENGE_CANCELLED = 0x4324
    FORCE_CANCEL_REQUEST = 0x4380
    FORCE_CANCEL_REPLY = 0x4381
    PLAYER_UPDATE = 0x4222


@dataclass
class Room:
    """Live room state shared by PES5 and PES6 handlers."""
    id: int
    name: bytes
    owner: "RoomContext"
    locked: bool = False
    password: bytes | None = None
    players: list["RoomContext"] = field(default_factory=list)
    match_time: int = DEFAULT_MATCH_TIME
    phase: int = DEFAULT_ROOM_PHASE
    challenger: "RoomContext | None" = None
    participating_players: list["RoomContext"] = field(default_factory=list)
    pes5_match: "PES5Match | None" = None
    ready_count: int = 0
    match_starter: "RoomContext | None" = None
    team_selection: "TeamSelection | None" = None
    match6: "Match6 | None" = None
    match_settings: bytes | None = None


@dataclass
class PES5Match:
    """PES5 score, team assignment, exit flags, and start time."""
    home_profile: Profile | None = None
    away_profile: Profile | None = None
    home_team_id: int | None = None
    away_team_id: int | None = None
    score_home: int = 0
    score_away: int = 0
    home_exit: int | None = None
    away_exit: int | None = None
    start_datetime: datetime | None = None


class RoomContext(PacketContext, Protocol):
    """Connection state required by room handlers."""
    lobbies: list[Lobby]
    lobby_id: int | None
    room_id: int
    repository: UserRepository
    settings: dict[str, object]
    user: User | None
    selected_profile: Profile | None
    no_lobby_chat: int
    ip1: bytes
    ip2: bytes
    udp_port1: int
    udp_port2: int
    spectator: bool
    cancelled_until: datetime | None
    needs_lobby_chat_replay: bool

    def schedule_chat_history(self, lobby: Lobby, *, encoder: ChatPrefixEncoder | None = None) -> None:
        """Schedule delayed lobby chat replay after leaving a room."""
        ...


async def _create(ctx: RoomContext, request: RoomCreateRequest) -> None:
    if ctx.lobby_id is None or ctx.selected_profile is None:
        raise ValueError("Room creation requires a lobby and profile")
    lobby = ctx.lobbies[ctx.lobby_id]
    name, locked, password = request.name, request.locked, request.password
    async with lobby.lock:
        if name in lobby.rooms:
            duplicate = True
            peers: tuple[LobbyPeer, ...] = ()
        else:
            duplicate = False
            lobby.room_ordinal += 1
            room = Room(lobby.room_ordinal, name, ctx, locked, password, [ctx])
            lobby.rooms[name] = room
            ctx.room_id = room.id
            peers = tuple(lobby.players.values())
    if duplicate:
        LOGGER.warning("Room create rejected (name exists): lobby=%s name=%s", lobby.name, name)
        await ctx.reply(RoomPacketId.CREATE_REPLY, ROOM_NAME_TAKEN)
        return
    LOGGER.info(
        "Room created: lobby=%s name=%s id=%d owner=%s game=%s",
        lobby.name, name.decode("utf-8", "replace"), room.id,
        ctx.selected_profile.name if ctx.selected_profile else "", ctx.game,
    )
    formats = active_formats(ctx)
    update = formats.room_update(room)
    stats = await ctx.repository.profile_stats(ctx.selected_profile.id) if formats.player_stats and ctx.settings.get("ShowStats", True) else ProfileStats()
    player = player_info(ctx, stats, encoder=formats.player_info)
    for peer in peers:
        await peer.reply(RoomPacketId.UPDATE, update)
        await peer.reply(RoomPacketId.PLAYER_UPDATE, player)
    await ctx.reply(RoomPacketId.CREATE_REPLY, bytes(ROOM_STATUS_SIZE))


@packet_handler(RoomPacketId.CREATE_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN)
async def create_room(ctx: RoomContext, request: bytes) -> None:
    """Create a room with the active decoder after duplicate-name validation."""
    await _create(ctx, decode_room_creation(ctx, request))


async def _list(ctx: RoomContext) -> None:
    if ctx.lobby_id is None:
        raise ValueError("Room listing requires a lobby")
    await ctx.reply(RoomPacketId.LIST_START, bytes(ROOM_STATUS_SIZE))
    lobby = ctx.lobbies[ctx.lobby_id]
    async with lobby.lock:
        rooms = tuple(lobby.rooms.values())
    for room in rooms:
        await ctx.reply(RoomPacketId.LIST_ENTRY, active_formats(ctx).room_list(room))
    await ctx.reply(RoomPacketId.LIST_END, bytes(ROOM_STATUS_SIZE))


@packet_handler(RoomPacketId.LIST_REQUEST, games=ALL_GAMES, role=(ServiceRole.NETWORK_MENU, ServiceRole.MAIN))
async def room_list(ctx: PacketContext, request: object) -> None:
    """Send room-list records using the active service's encoder."""
    await _list(ctx)  # type: ignore[arg-type]
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.needs_lobby_chat_replay and context.lobby_id is not None:
        context.needs_lobby_chat_replay = False
        context.schedule_chat_history(context.lobbies[context.lobby_id])


def _challenge_allowed(context: RoomContext, lobby: Lobby, room: Room | None) -> bool:
    owner_user = room.owner.user if room else None
    roster_settings = context.settings.get("Roster", {})
    compare_roster = isinstance(roster_settings, dict) and bool(roster_settings.get("compareHash", False))
    assert context.user is not None
    return (
        room is not None and owner_user is not None
        and context.user.game_version == owner_user.game_version
        and context.user.game_name == owner_user.game_name
        and (not compare_roster or not lobby.check_roster_hash
             or context.user.roster_hash == owner_user.roster_hash)
    )


def _log_challenge_rejection(context: RoomContext, room: Room | None, room_id: int) -> None:
    """Explain only the source-supported room/game/roster rejection reasons."""
    assert context.user is not None
    owner_user = room.owner.user if room is not None else None
    if room is None:
        LOGGER.warning("ERROR: Room (id=%d) does not exist.", room_id)
    elif owner_user is None:
        LOGGER.warning("ERROR: Room (id=%d) has no owner.", room_id)
    elif (context.user.game_version != owner_user.game_version
          or context.user.game_name != owner_user.game_name):
        LOGGER.info("INFO: Game versions differ: %s(%s,%s) != %s(%s,%s).",
                    context.user.username, context.user.game_version, context.user.game_name,
                    owner_user.username, owner_user.game_version, owner_user.game_name)
    else:
        LOGGER.info("INFO: Roster-hashes are different: %s != %s.",
                    context.user.username, owner_user.username)


async def _send_challenge_profile(context: RoomContext, room: Room, request: bytes) -> None:
    assert context.selected_profile is not None
    show_stats = bool(context.settings.get("ShowStats", True))
    stats = await context.repository.profile_stats(context.selected_profile.id) if show_stats else ProfileStats()
    profile = format_profile_info(context.selected_profile, stats, encoder=encode_pes5_profile, show_stats=show_stats)
    if len(profile) > PES5_CHALLENGE_PROFILE_SIZE:
        raise ValueError("Challenge profile exceeds wire field")
    ping = request[PES5_CHALLENGE_PING_OFFSET:PES5_CHALLENGE_PING_OFFSET + 1]
    payload = Pes5ChallengeRecord(profile, ping, bytes(2)).pack()
    await room.owner.reply(RoomPacketId.CHALLENGE, payload)


async def _announce_challenge_join(
    room: Room, context: RoomContext, peers: tuple[LobbyPeer, ...]
) -> None:
    update, player = encode_pes5_room(room, update=True), player_info(context, encoder=encode_pes5_player)
    for peer in peers:
        await peer.reply(RoomPacketId.UPDATE, update)
        await peer.reply(RoomPacketId.PLAYER_UPDATE, player)


@packet_handler(RoomPacketId.JOIN_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN)
async def challenge_room_pes5(ctx: PacketContext, request: object) -> None:
    """Validate game/roster compatibility and send a PES5 room challenge."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.lobby_id is None or context.user is None or context.selected_profile is None:
        raise ValueError("Room challenge requires lobby and profile")
    if len(request) < PES5_CHALLENGE_MIN_SIZE:
        raise ValueError("Short room challenge")
    room_id = struct.unpack_from("!i", request)[0]
    lobby = context.lobbies[context.lobby_id]
    async with lobby.lock:
        room = next((candidate for candidate in lobby.rooms.values() if candidate.id == room_id), None)
        valid = _challenge_allowed(context, lobby, room)
        if valid:
            assert room is not None
            room.players.append(context)
            context.room_id = room.id
            room.challenger = context
            peers = tuple(lobby.players.values())
        else:
            peers = ()
    if not valid:
        _log_challenge_rejection(context, room, room_id)
        await ctx.reply(RoomPacketId.JOIN_REPLY, ROOM_JOIN_ERROR)
        return
    assert room is not None
    LOGGER.info(
        "Room challenge sent: lobby=%s room=%s from=%s owner=%s",
        lobby.name, room.name.decode("utf-8", "replace"),
        context.selected_profile.name, room.owner.selected_profile.name if room.owner.selected_profile else "",
    )
    await _announce_challenge_join(room, context, peers)
    await _send_challenge_profile(context, room, request)


async def _accept_challenge(
    context: RoomContext, room: Room, challenger: RoomContext, lobby: Lobby, peers: tuple[LobbyPeer, ...]
) -> None:
    await challenger.reply(RoomPacketId.JOIN_REPLY, bytes(4) + PES5_CHALLENGE_ACCEPT_TAIL)
    if challenger.selected_profile is None:
        raise ValueError("Challenger has no profile")
    # TODO(0x4330): the second field is the opponent's team bitmask, not the room
    # name. The pes5.exe parser (FUN_0083c2e0) reads name[16] then team_mask[32]
    # (pre-filled with '0' bytes) into protocolState+0x120F5 and +0x12106; the
    # 0x4330 handler in FUN_00498f40 copies +0x12106 to DAT_03b7cb7c. That is the
    # mask FUN_00498c10 uses to filter the opponent's side on the online
    # team-select screen (BuildTeamSelectAllCategorySlots). Sending the room name
    # (as the original server did) therefore uses the name's text and zero padding
    # as bits, so with a short name most teams on that side come out blocked.
    # Fix: send the challenger's own 0x4101 team mask (32 bytes, same format; see
    # the TODO on PROFILE_SELECT_TAIL in profile_info.py) and rename
    # Pes5RoomPeerNames.room_name accordingly. Also check:
    #   - 0x4321 (JOIN_REPLY to the guest) is now confirmed to carry the same
    #     {name, mask} record; see TODO(0x4321) at PES5_CHALLENGE_ACCEPT_TAIL.
    #     0x4a11 also copies +0x12106 but is not implemented here.
    #   - 0x4331 (PLAYER_LEFT_ROOM) also uses Pes5RoomPeerNames; its client
    #     parser has not been checked yet.
    #   - in-game effect not confirmed: the guest may choose on their own machine,
    #     in which case this only changes what the host's screen shows.
    await context.reply(RoomPacketId.PEER_ENTERED, Pes5RoomPeerNames(
        challenger.selected_profile.name.encode("utf-8"), room.name,
    ).pack())
    # The source also broadcasts both players' room membership to the lobby.
    owner_update, challenger_update = player_info(context, encoder=encode_pes5_player), player_info(challenger, encoder=encode_pes5_player)
    for peer in peers:
        await peer.reply(RoomPacketId.PLAYER_UPDATE, owner_update)
        await peer.reply(RoomPacketId.PLAYER_UPDATE, challenger_update)
    challenger.needs_lobby_chat_replay = True


async def _reject_challenge(
    room: Room, challenger: RoomContext, peers: tuple[LobbyPeer, ...]
) -> None:
    update, player = encode_pes5_room(room, update=True), player_info(challenger, encoder=encode_pes5_player)
    for peer in peers:
        await peer.reply(RoomPacketId.UPDATE, update)
        await peer.reply(RoomPacketId.PLAYER_UPDATE, player)
    await challenger.reply(RoomPacketId.JOIN_REPLY, ROOM_JOIN_ERROR)


@packet_handler(RoomPacketId.CHALLENGE_RESPONSE, games=ALL_GAMES, role=ServiceRole.MAIN)
async def challenge_response_pes5(ctx: PacketContext, request: object) -> None:
    """Accept or reject a pending PES5 challenge and update the room."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if not request or context.lobby_id is None:
        raise ValueError("Invalid challenge response")
    lobby = context.lobbies[context.lobby_id]
    async with lobby.lock:
        room = next((candidate for candidate in lobby.rooms.values() if candidate.id == context.room_id), None)
        if room is None or room.owner is not context or room.challenger is None:
            raise ValueError("No pending challenge")
        challenger = room.challenger
        room.challenger = None
        accepted = request[0] == 1
        if not accepted:
            room.players.remove(challenger)
            challenger.room_id = 0
        peers = tuple(lobby.players.values())
    LOGGER.info(
        "Room challenge %s: lobby=%s room=%s challenger=%s",
        "accepted" if accepted else "rejected", lobby.name,
        room.name.decode("utf-8", "replace"),
        challenger.selected_profile.name if challenger.selected_profile else "",
    )
    if accepted:
        await _accept_challenge(context, room, challenger, lobby, peers)
    else:
        await _reject_challenge(room, challenger, peers)


async def _remove_from_room(
    ctx: RoomContext, lobby: Lobby
) -> tuple[Room, tuple[LobbyPeer, ...], bool] | None:
    async with lobby.lock:
        room = next((item for item in lobby.rooms.values() if item.id == ctx.room_id), None)
        if room is None or ctx not in room.players:
            ctx.room_id = 0
            return None
        room.players.remove(ctx)
        if ctx in room.participating_players:
            room.participating_players.remove(ctx)
        if room.players and room.owner is ctx:
            room.owner = room.players[0]
        ctx.room_id = 0
        empty = not room.players
        if empty:
            del lobby.rooms[room.name]
        return room, tuple(lobby.players.values()), empty


async def _notify_room_exit(
    ctx: RoomContext, room: Room, peers: tuple[LobbyPeer, ...], *, formats: WireFormats
) -> None:
    update = formats.room_update(room)
    show_stats = formats.player_stats and ctx.selected_profile is not None and bool(ctx.settings.get("ShowStats", True))
    if show_stats:
        assert ctx.selected_profile is not None
        stats = await ctx.repository.profile_stats(ctx.selected_profile.id)
    else:
        stats = ProfileStats()
    player = player_info(ctx, stats, encoder=formats.player_info)
    for peer in peers:
        await peer.reply(RoomPacketId.UPDATE, update)
        await peer.reply(RoomPacketId.PLAYER_UPDATE, player)


async def _notify_owner_left(ctx: RoomContext, room: Room) -> None:
    if ctx.selected_profile is None:
        return
    await room.owner.reply(RoomPacketId.PLAYER_LEFT_ROOM, Pes5RoomPeerNames(
        ctx.selected_profile.name.encode("utf-8"), room.name,
    ).pack())


async def _notify_room_closed(room: Room, peers: tuple[LobbyPeer, ...]) -> None:
    identifier = struct.pack("!i", room.id)
    for peer in peers:
        await peer.reply(RoomPacketId.REMOVED, identifier)


async def _notify_participation(room: Room) -> None:
    participation = participation_status(room)
    for member in room.players:
        await member.reply(RoomPacketId.PARTICIPATION_UPDATE, participation)


@dataclass(frozen=True)
class RoomExitRules:
    """Actual state/notification differences, not binary format selectors."""
    owner_notification: bool
    replay_history: bool
    participation_notification: bool
    record_disconnect: bool


PES5_EXIT_RULES = RoomExitRules(True, True, False, True)
PES6_EXIT_RULES = RoomExitRules(False, False, True, False)
ROOM_EXIT_RULES = MappingProxyType({
    **dict.fromkeys(FIVE_GAMES, PES5_EXIT_RULES), **dict.fromkeys(SIX_GAMES, PES6_EXIT_RULES),
})


async def exit_room(ctx: RoomContext, *, acknowledge: bool = True,
                    notify_owner: bool = True, rules: RoomExitRules | None = None,
                    formats: WireFormats | None = None) -> None:
    """Remove a player from a room and publish the original updates."""
    rules = rules or ROOM_EXIT_RULES[ctx.game]
    formats = formats or active_formats(ctx, role=ServiceRole.MAIN)
    if ctx.lobby_id is None or ctx.room_id == 0:
        if acknowledge:
            await ctx.reply(RoomPacketId.EXIT_REPLY, bytes(ROOM_STATUS_SIZE))
        return
    lobby = ctx.lobbies[ctx.lobby_id]
    removed = await _remove_from_room(ctx, lobby)
    if removed is None:
        return
    room, peers, empty = removed
    LOGGER.info(
        "Player left room: room=%s lobby=%s player=%s remaining=%d empty=%s",
        room.name.decode("utf-8", "replace"), lobby.name,
        ctx.selected_profile.name if ctx.selected_profile else "", len(room.players), empty,
    )
    if room.players and rules.owner_notification and notify_owner and ctx.selected_profile is not None:
        await _notify_owner_left(ctx, room)
    await _notify_room_exit(ctx, room, peers, formats=formats)
    if acknowledge:
        await ctx.reply(RoomPacketId.EXIT_REPLY, bytes(ROOM_STATUS_SIZE))
        if rules.replay_history and room.players:
            ctx.schedule_chat_history(lobby)
    if empty:
        LOGGER.info("Room deleted: room=%s id=%d", room.name.decode("utf-8", "replace"), room.id)
        await _notify_room_closed(room, peers)
    elif rules.participation_notification:
        await _notify_participation(room)


@packet_handler(RoomPacketId.EXIT_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN)
async def exit_room_pes5(ctx: PacketContext, request: object) -> None:
    """Handle room exit using the active game's explicit exit rules."""
    await exit_room(ctx)  # type: ignore[arg-type]


@packet_handler(RoomPacketId.CANCEL_CHALLENGE_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN)
async def cancel_challenge_pes5(ctx: PacketContext, request: object) -> None:
    """Cancel a pending PES5 challenge and notify the current owner."""
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.lobby_id is not None and context.room_id:
        lobby = context.lobbies[context.lobby_id]
        async with lobby.lock:
            room = next((item for item in lobby.rooms.values() if item.id == context.room_id), None)
            if room is not None and room.challenger is context:
                room.challenger = None
        await exit_room(context, acknowledge=False, notify_owner=False, rules=PES5_EXIT_RULES)
        if room is not None and room.players:
            await room.owner.reply(RoomPacketId.CHALLENGE_CANCELLED, bytes(ROOM_STATUS_SIZE))
    await ctx.reply(RoomPacketId.CANCEL_CHALLENGE_REPLY, bytes(ROOM_STATUS_SIZE))


def _current_room(ctx: RoomContext) -> tuple[Lobby, Room | None]:
    if ctx.lobby_id is None:
        raise ValueError("No selected lobby")
    lobby = ctx.lobbies[ctx.lobby_id]
    room = next((item for item in lobby.rooms.values() if item.id == ctx.room_id), None)
    return lobby, room


# TODO(0x4350 / 0x4344): verified in pes5.exe. 0x4350 is a typed envelope,
# u32 type | u32 length | body[64] (builder 0x0083ce70 via FUN_004952b0, parser
# 0x0083cf00); the server relays it unchanged. Two types are sent:
#   type 0x101, length 14 = match settings, sent by the room owner only
#     (FUN_004961e0 / FUN_004963e0). Body bytes, as FUN_0046c4e0 applies them
#     to g_InMatchSettings:
#       0 match time      1 time limit (legacy name)   2 pauses (legacy name)
#       3 condition (legacy name)                       4 injuries (0 = on)
#       5 substitutions   6 extra time: 0 off, 1 on, 2 golden goal, 3 silver
#       7 penalties (0 = on)   8 time of day (0, 1, else random)
#       9 weather (0..2, else random)   10 season (0, 1, else random)
#       11, 12, 13 random seeds used for the "random" choices (12 never 0)
#     Legacy formatRoomSettings named bytes 0-9 the same way (it called 9
#     "seasonWeather" and wrote a zero i32 over bytes 10-13).
#   type 0x105, length 4 = one u32 (0 / 1 / 2) from a client UI state flag,
#     re-sent every 30 frames when it changes (FUN_0049b7b0 -> FUN_0049d530);
#     what the peer does with it was not traced.
# 0x4344 (parser 0x0083c360) is a single u8: the room phase.
@packet_handler(RoomPacketId.SETTINGS_RELAY, games=ALL_GAMES, role=ServiceRole.MAIN)
async def relay_room_settings(ctx: PacketContext, request: object) -> None:
    """Relay room settings to every other room member."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.lobby_id is None:
        return
    lobby, room = _current_room(context)
    if room is None:
        return
    async with lobby.lock:
        recipients = tuple(peer for peer in room.players if peer is not context)
    for peer in recipients:
        await peer.reply(RoomPacketId.SETTINGS_RELAY, request)


@packet_handler(RoomPacketId.SETTINGS_RELAY, games=SIX_GAMES, role=ServiceRole.MAIN, override=True)
async def relay_room_settings_pes6(ctx: PacketContext, request: object) -> None:
    """Update PES6 match time from the settings header, then relay."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.lobby_id is not None:
        lobby, room = _current_room(context)
        if room is not None and len(request) >= 13 and request[:4] == b"\0\0\x01\x03":
            async with lobby.lock:
                room.match_time = 5 * (request[12] + 1)
    await relay_room_settings(ctx, request)


@packet_handler(RoomPacketId.MATCH_TIME_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN)
async def set_match_time_pes5(ctx: PacketContext, request: object) -> None:
    """Update PES5 room match time and publish room state."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if not request:
        raise ValueError("Match time is missing")
    lobby, room = _current_room(context)
    if room is not None:
        async with lobby.lock:
            room.match_time = request[0] * 5
            peers = tuple(lobby.players.values())
            update = encode_pes5_room(room, update=True)
        for peer in peers:
            await peer.reply(RoomPacketId.UPDATE, update)
    await ctx.reply(RoomPacketId.MATCH_TIME_REPLY, bytes(ROOM_STATUS_SIZE))


@packet_handler(RoomPacketId.TEAM_SELECT_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN, request_type=TeamSelectRequest)
async def select_team_pes5(ctx: RoomContext, request: TeamSelectRequest) -> None:
    """Assign a PES5 home/away team, replacing the match object as the source does."""
    context = ctx
    if context.selected_profile is None:
        raise ValueError("Invalid team selection")
    lobby, room = _current_room(context)
    if room is None:
        raise ValueError("Team selection outside room")
    team = request.team_id
    LOGGER.info("Team selected: %d", team)
    async with lobby.lock:
        # The original replaces the match object on every team selection,
        # preserving only the two profile/team assignments.
        previous = room.pes5_match
        match = PES5Match(
            previous.home_profile if previous else None,
            previous.away_profile if previous else None,
            previous.home_team_id if previous else None,
            previous.away_team_id if previous else None,
        )
        if room.owner is context:
            match.home_profile = context.selected_profile
            match.home_team_id = team
        else:
            match.away_profile = context.selected_profile
            match.away_team_id = team
        room.pes5_match = match
        if match.home_profile is not None and match.away_profile is not None:
            LOGGER.info("NEW MATCH starting: Team %s (%s) vs Team %s (%s)",
                        match.home_team_id, match.home_profile.name, match.away_team_id, match.away_profile.name)
    await ctx.reply(RoomPacketId.SPECTATOR_REPLY, b"\0\0\0\1")


def _mark_pes5_ready(room: Room, request: bytes) -> bool:
    room.ready_count += 1 if request[0] == 1 else -1
    if room.ready_count != PES5_READY_PLAYERS:
        return False
    room.ready_count = 0
    if room.pes5_match and room.pes5_match.start_datetime is None:
        room.pes5_match.start_datetime = datetime.now()
    return True


@packet_handler(RoomPacketId.MATCH_START_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN)
async def start_match_pes5(ctx: PacketContext, request: object) -> None:
    """Handle the PES5 ready counter and phase transition."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if not request:
        raise ValueError("Missing ready status")
    lobby, room = _current_room(context)
    ready = False
    if room is not None:
        async with lobby.lock:
            players = tuple(room.players)
            ready = _mark_pes5_ready(room, request)
        for peer in players:
            if peer is not context:
                await peer.reply(RoomPacketId.MATCH_START_UPDATE, request)
    await ctx.reply(RoomPacketId.MATCH_START_REPLY, bytes(ROOM_STATUS_SIZE))
    if room is not None and ready:
        LOGGER.info(
            "PES5 match ready: room=%s lobby=%s players=%d",
            room.name.decode("utf-8", "replace"), lobby.name, len(players),
        )
        for peer in players:
            await peer.reply(RoomPacketId.PHASE_UPDATE, PES5_MATCH_PHASE)
            peer.needs_lobby_chat_replay = True
