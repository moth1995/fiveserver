"""Configured lobby listing and its fixed PES wire record."""


import logging
import struct
from enum import IntEnum
from typing import Annotated, Any, Protocol

import dataclasses_struct as dcs

from ..protocol.dispatcher import ALL_GAMES, PacketContext, ServiceRole, packet_handler
from ..protocol.records import PingRecord
from ..protocol.packets import decode_profile_id
from ..database import Profile, ProfileStats, User, UserRepository
from ..protocol.formats import ChatPrefixEncoder, active_formats
from .lobby_state import (
    Lobby, NO_STATS_LOBBY_CODE, OPEN_LOBBY_CODE, player_info,
)
from .matches import pes5_disconnect
from .rooms import ROOM_EXIT_RULES, exit_room


# TODO(0x4201): lobby type byte, verified against pes5.exe. The lobby-select screen
# (FUN_00483fa0) picks the description text with FUN_004a82f0, which tests the
# bits of the type byte (higher bits first) and returns an index into this table:
#   0x20 set                          -> 511:24 "open to all players. Matches played
#                                        here are not recorded in your profile."
#   0x1F (bits 0-4 all set, no 0x20)  -> 511:23 "This lobby is open to all players."
#   0x10 only                         -> 511:18 "for Division 1 only"
#   0x08 only                         -> 511:19 "for Division 2 only"
#   0x04 only                         -> 511:20 "for Division 3A only"
#   0x02 only                         -> 511:21 "for Division 3B only"
#   0x01 only                         -> 511:22 "for Amateur only"   (the "A" below)
#   any other mix of bits 0-4         -> 511:30 "Division-restricted lobby."
#   no bit of 0x1F and no 0x20        -> index 8, past the 8-entry table (unknown,
#                                        probably not a valid type)
# Bit 6 (0x40, so 0x5F = 0x1F + 0x40) is never tested: the bit helper
# FUN_004a5140 only accepts masks 0x01..0x20 (anything else returns false), and
# the only readers of the type byte are this classifier and the eligibility check
# FUN_004a53d0. So 0x40 has no effect; 0x5F and 0x1F behave the same. 511:16 says the lobby
# list shows "Stars are an estimate of level", which probably comes from the same
# mask. 511:28 "Cannot enter lobby. You are not eligible to enter this division."
# is the client-side eligibility message. "A" = Amateur, bit 0 is the lowest
# division and bit 4 (Division 1) the highest: same order as the card colours.
DIVISION_BITS = {"A": 0, "3B": 1, "3A": 2, "2": 3, "1": 4}
MENU_ROLES = (ServiceRole.NETWORK_MENU, ServiceRole.MAIN)
STATUS_SIZE = 4
LOGGER = logging.getLogger(__name__)
PLAYER_NAME_SIZE_PES5 = 16
PLAYER_NAME_SIZE_PES6 = 48


class LobbyPacketId(IntEnum):
    """Packet IDs for lobby listing, selection, users, peer lookup, and quick search."""
    LIST_REQUEST = 0x4200
    LIST_RESPONSE = 0x4201
    SELECT_REQUEST = 0x4202
    SELECT_RESPONSE = 0x4203
    USER_LIST_REQUEST = 0x4210
    USER_LIST_START = 0x4211
    USER_LIST_ENTRY = 0x4212
    USER_LIST_END = 0x4213
    USER_ENTERED = 0x4220
    USER_LEFT = 0x4221
    PING_REQUEST = 0x4B00
    PING_REPLY = 0x4B01
    QUICK_MATCH_REQUEST = 0x4A00
    QUICK_MATCH_REPLY = 0x4A01


# TODO(0x4201): how the lobby-select screen uses these fields (pes5.exe, row builder
# FUN_004b3b10). There are NO stars in the row, only the name and a player bar:
#   - the bar is count / 100, where 100 is hard-coded (FUN_004a5100), so the
#     client assumes a capacity of 100 and any count of 100 or more shows "full";
#   - bar colour: 0 players grey (128,128,128), 1..99 yellow (211,197,0),
#     100+ red (255,27,27);
#   - name colour: white when the local player may enter, grey when not
#     (eligibility FUN_004a53d0: bit 0x20 set, or bit 1 << own division set).
# The "Stars are an estimate of level" text (511:16) has no reader in this screen,
# so it is probably a leftover from another version. The lobby cap is the one
# thing a real server could tune: keep players <= 100 to make the bar meaningful.
@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class LobbySelection:
    """Fixed 39-byte ``0x4202`` lobby-entry payload.

    Layout: one-byte lobby ordinal, two 16-byte IP fields, two UDP ports, and
    one opaque 16-bit field preserved from the client.
    """
    ordinal: dcs.U8
    ip1: Annotated[bytes, 16]
    udp_port1: dcs.U16
    ip2: Annotated[bytes, 16]
    udp_port2: dcs.U16
    # TODO(0x4202): the pes5.exe builder (0x00839f30, called from 0x004a4a60)
    # takes ip1/port1, ip2/port2 and this u16 from the client's UDP/NAT state at
    # 0x00f815c4 (+0x16). FUN_0046fa60 compares that value with 0x10 and 0x91 and
    # then keeps its public port, so it is most likely a NAT / connection type.
    # We only store it (lobby_unknown); nothing reads it back.
    unknown: dcs.U16


def configured_lobbies(settings: dict[str, Any]) -> list[Lobby]:
    """Build lobby state from the original YAML lobby definitions."""
    result = []
    for item in settings["Lobbies"]:
        if isinstance(item, str):
            result.append(Lobby(item, OPEN_LOBBY_CODE))
            continue
        name = item["name"]
        kind = item.get("type", "open")
        if kind == "noStats":
            code = NO_STATS_LOBBY_CODE
        elif isinstance(kind, list):
            try:
                code = sum(1 << DIVISION_BITS[division] for division in kind)
            except KeyError as exc:
                raise ValueError(f"Invalid lobby division: {exc.args[0]}") from exc
        else:
            code = OPEN_LOBBY_CODE
        try:
            check_roster = bool(int(item.get("checkRosterHash", 1)))
        except (TypeError, ValueError):
            check_roster = True
        result.append(Lobby(name, code, str(kind), item.get("showMatches", True), check_roster))
    return result


class LobbyContext(PacketContext, Protocol):
    """Connection state required by lobby handlers."""
    lobbies: list[Lobby]
    user: User | None
    selected_profile: Profile | None
    repository: UserRepository
    settings: dict[str, Any]
    lobby_id: int | None
    room_id: int
    no_lobby_chat: int
    ip1: bytes
    ip2: bytes
    udp_port1: int
    udp_port2: int
    lobby_unknown: int

    def schedule_chat_history(self, lobby: Lobby, *, encoder: ChatPrefixEncoder | None = None) -> None:
        """Schedule delayed chat-history replay after entering a lobby."""
        ...


# TODO(0x4200): this byte is the client's LANGUAGE, not a game version.
# Checked in Ghidra on pes5.exe, WE9LEK.exe, pes6.exe and we2007.dat (the
# unpacked we2007.exe). Every client builds 0x4200 with exactly one u8, the
# game's ACTIVE language (slot 1 of a two-slot global; slot 0 is the setting
# saved in the option file). It is sent on the network-menu connection when the
# client enters the lobby screen.
#
# 0x4200 is NOT the only packet with the language. In pes5.exe every network
# caller of the language getter (wrapper FUN_004925e0) was traced, and the
# language goes out in five client->server packets, starting with the very
# first connection:
#   0x2005  news: server-list request    [u8 lang]
#           sender FUN_00845f20, connection 0 (FUN_004b2410 / FUN_004b2a20)
#   0x2008  news: news request           [u8 lang]
#           sender FUN_00846110, connection 0 (FUN_004b2580 / FUN_004b28a0)
#   0x3100  login                        [u32 BE profile id, u8 lang]
#           sender FUN_0083e5a0. The id is FUN_004925c0, i.e. DAT_03b7ca0c,
#           which 0x3042 fills, or 0 before login.
#   0x4100  profile select               [u8 profile index, u8 lang]
#           sender FUN_00839d00; sent after authentication on the menu and main
#           connections (FUN_004a4770 / FUN_004a8b70 / FUN_004a8fc0)
#   0x4200  lobby list                   [u8 lang]  (this handler)
# Our server ignores the payload of 0x2005, 0x2008 and 0x3100 (ack only), and
# profile_info._index reads only byte 0 of 0x4100. So 0x2005 is the earliest
# point where the language is known, before login even happens; that would let
# the news greeting or server list be localized.
# Other games: the builders have the same shape in every exe checked. 0x2005
# and 0x2008 are [u8] and 0x3100 is [u32, u8] in WE9LEK, pes6 and we2007.
# 0x4100 is [u8, u8] in WE9LEK, but [u8, u8, u8] in pes6 and we2007
# (pes6 sender FUN_00b6a1b0, callers FUN_00a79620 / FUN_00a84b10). In pes6 the
# wire order is [profile index, lang (FUN_00a52560), unknown (FUN_00a52570,
# reads an obfuscated global)]. In those three exes, only 0x4200 and pes6's
# 0x4100 were traced back to the language getter; for the others only the
# packet shape was checked.
#   pes5.exe   sender FUN_00839e20, caller FUN_004945b0, GetGameLanguage(1) & 0xFF
#   WE9LEK.exe sender FUN_008615d0, callers FUN_00494420 / FUN_004a7680, FUN_005dca30(1)
#   pes6.exe   sender FUN_00b6a3f0, caller FUN_00a53e00, FUN_0065c510(1)
#   we2007.dat sender FUN_00b6af40, caller FUN_00a54820, FUN_0065c550(1)
# Values (from each exe's registry-key switch, lang_x under
# HKLM\SOFTWARE\KONAMI\..., and its default chosen from the Windows locale):
#   0 English   1 French   2 German   3 Italian   4 Spanish   all games
#   6 Korean    WE9LEK only (locale 0x12, key lang_k); 5 is skipped
#   9 Polish    pes6 / we2007 (locale 0x15 = LANG_POLISH, key lang_p); 5-8 unused
#   8 English   we2007, Asian install type only (key text_e); in that mode
#               4 = Spanish (key text_s) and 0 is the base language
#               (0_text.afs, probably Japanese)
# Not checked: WE9 (Japanese PES5; not in the Ghidra project). It is most likely
# the same packet and may send its own Japanese id.
# The 0x4201 reply has no language field: u16 count, then up to 10 records of
# (u8 type, name[32], u16 players), which matches LobbyRecord.
#
# Consequence: the original server stored this byte as "gameVersion", and here it
# becomes user.game_version. rooms._challenge_allowed requires it to match, so
# players whose game languages differ CANNOT challenge each other, and the log
# line says "Game versions differ". The real game check is the separate game_name
# comparison (it comes from the login service and port). Nothing in the client
# needs matching languages; in-match text is all local.
# Fix: rename game_version to language and decode it as an IntEnum with the
# values above. Keep it for logs, stats and the web UI, and remove it from
# _challenge_allowed, or put that check behind a config flag (e.g.
# Lobbies.sameLanguageOnly) if language-segregated lobbies are wanted.
# The language is also shown to other players: the last byte of the 0x4103 profile
# reply is drawn on the profile card as the language name (English, French, German,
# Italian, Spanish for 0..4; see TODO(0x4103) in profile_info.py). Storing the byte
# received here (or in 0x4100 / 0x3100 / 0x2005) per user would let the server send
# each player's real language instead of the 0 = "English" it sends today.
# Only 0..4 and 8 have a card label, so Korean (6) and Polish (9) would show the
# fallback string.
def decode_game_version(payload: bytes) -> int:
    """Decode the first byte of a PES5/PES6 lobby-list request."""
    if not payload:
        raise ValueError("Lobby request has no game version")
    return payload[0]


@packet_handler(LobbyPacketId.LIST_REQUEST, games=ALL_GAMES, role=MENU_ROLES, decoder=decode_game_version)
async def get_lobbies(ctx: PacketContext, request: object) -> None:
    """Record the client game version and return the configured lobby records."""
    context: LobbyContext = ctx  # type: ignore[assignment]
    if context.user is None:
        raise ValueError("Lobby list requires authentication")
    assert isinstance(request, int)
    context.user.game_version = request
    LOGGER.debug("Lobby list requested: user=%s game=%s lobbies=%d", context.user.username, context.game, len(context.lobbies))
    payload = struct.pack("!H", len(context.lobbies)) + b"".join(lobby.pack() for lobby in context.lobbies)
    await ctx.reply(LobbyPacketId.LIST_RESPONSE, payload)


def decode_lobby_selection(payload: bytes) -> LobbySelection:
    """Decode the fixed 39-byte lobby-selection request."""
    if len(payload) < 39:
        raise ValueError("Lobby selection requires 39 bytes")
    return LobbySelection.from_packed(payload[:39])


# TODO(0x4212 / 0x4220 / 0x4222): the trailing bytes(2) are not reserved. In
# pes5.exe the 0x4212 (FUN_0083a0e0) and 0x4220 (NetParseLobbyMemberJoin4220)
# parsers read these 31 bytes as a Ps2LobbyMemberEntry: i32 playerId | name[16] |
# u8 status1 (our in_room) | i32 value1 (room_id) | i32 value2 (no_lobby_chat) |
# u8 bMemberStatus | u8 status2. 0x4222 (NetParseLobbyMemberUpdate4222) reads the
# same 31 bytes. In the 0x24-byte client entry they land at name +0, status1
# +0x11, playerId +0x14, value1 +0x18, value2 +0x1c, status2 +0x20,
# bMemberStatus +0x22; the lobby UI copies entries byte for byte (FUN_004a67d0).
# bMemberStatus is a member class 1..4 (5 = missing/invalid; see
# GetLobbyMemberStatusByPlayerId @ 00490b60). It decides the CHAT COLOUR:
# FUN_004b7d30(playerId) returns a sender class: 7 for id 0 (system); 4 / 5 / 6 for
# status 2 / 3 / 4; otherwise 1 for the local player, 2 or 3 if the sender is in one
# of two client-side lists (FUN_00491080 / FUN_004910a0, probably friends and
# blocked, unverified), else 0. FUN_004b5910 maps the class to an RGBA colour
# (tables at DAT_00b18010 and DAT_00b17fc0, 10 colours). FUN_004b5b40 applies it to
# lobby / room chat lines when the chat "special" field is 0; a non-zero "special"
# selects a fixed colour (DAT_00b17fe0) instead.
# We send 0, so the client falls back to its default colour for everyone. Sending
# 2, 3 or 4 would give that player's chat a distinct colour (admins, moderators,
# VIPs...); read the palette at DAT_00b17fc0 to see which status is which colour.
# Status 1 behaves like 0 and 5. status2 (last byte, entry +0x20) is UNUSED: every
# member reader was enumerated (GetLobbyMemberUi* accessors, FUN_004a5890,
# FUN_004a58f0, BuildLobbyRoomUiList, GetLobbyMemberStatusByPlayerId) and none
# reads +0x20. Keep 0. (FUN_004a58f0 reads +0x21 for a name colour, but no parser
# ever writes +0x21, so that flag is always 0.)
# status1: GetLobbyMemberUiStatus returns 5 for a missing entry; we send
# int(room_id != 0), as the original sent usr.state.inRoom.
# Read semantics (verified): the 0x4212 list loop stops through
# GetNetworkPacketPayloadReadPosition, which uses the real payload length, so each
# entry must be exactly 31 bytes or every later entry is misaligned (max 100).
# BuildLobbyRoomUiList (004a6880) is the only reader of status1 and value2: it
# discards a room row if a participant's status1 is above 4 or its value1 (room_id)
# differs from the room's id, and it copies value2 (no_lobby_chat) into each
# participant's row (that row's reader was not traced). It does not read
# bMemberStatus or status2; bMemberStatus is read only for the chat colour above.
async def _enter_lobby(ctx: LobbyContext, selection: LobbySelection) -> None:
    if ctx.user is None or ctx.selected_profile is None:
        raise ValueError("Lobby entry requires a selected profile")
    if selection.ordinal >= len(ctx.lobbies):
        raise ValueError("Unknown lobby")
    if ctx.lobby_id is not None:
        await leave_lobby(ctx)
    lobby = ctx.lobbies[selection.ordinal]
    ctx.lobby_id = selection.ordinal
    ctx.room_id = 0
    ctx.no_lobby_chat = 0
    ctx.ip1 = selection.ip1
    ctx.ip2 = selection.ip2
    ctx.udp_port1 = selection.udp_port1
    ctx.udp_port2 = selection.udp_port2
    ctx.lobby_unknown = selection.unknown
    await ctx.reply(LobbyPacketId.SELECT_RESPONSE, bytes(STATUS_SIZE))
    async with lobby.lock:
        lobby.players[ctx.user.hash] = ctx  # type: ignore[assignment]
        recipients = tuple(lobby.players.values())
    LOGGER.info("User {%s} entering lobby %d", ctx.selected_profile.name, selection.ordinal)
    LOGGER.debug("Lobby %s population: %d", lobby.name, len(recipients))
    formats = active_formats(ctx)
    stats = await ctx.repository.profile_stats(ctx.selected_profile.id) if formats.player_stats and ctx.settings.get("ShowStats", True) else ProfileStats()
    data = player_info(ctx, stats, encoder=formats.player_info)  # type: ignore[arg-type]
    for peer in recipients:
        await peer.reply(LobbyPacketId.USER_ENTERED, data)
    ctx.schedule_chat_history(lobby)


@packet_handler(LobbyPacketId.SELECT_REQUEST, games=ALL_GAMES, role=MENU_ROLES, request_type=LobbySelection)
async def select_lobby(ctx: LobbyContext, request: LobbySelection) -> None:
    """Enter a lobby using the active service's player record layout."""
    await _enter_lobby(ctx, request)


async def _list_users(ctx: LobbyContext) -> None:
    if ctx.lobby_id is None:
        raise ValueError("User has not selected a lobby")
    await ctx.reply(LobbyPacketId.USER_LIST_START, bytes(STATUS_SIZE))
    lobby = ctx.lobbies[ctx.lobby_id]
    async with lobby.lock:
        players = tuple(lobby.players.values())
    for peer in players:
        profile = peer.selected_profile
        formats = active_formats(ctx)
        stats = await ctx.repository.profile_stats(profile.id) if formats.player_stats and profile and ctx.settings.get("ShowStats", True) else None
        await ctx.reply(LobbyPacketId.USER_LIST_ENTRY, player_info(peer, stats, encoder=formats.player_info))
    await ctx.reply(LobbyPacketId.USER_LIST_END, bytes(STATUS_SIZE))


@packet_handler(LobbyPacketId.USER_LIST_REQUEST, games=ALL_GAMES, role=MENU_ROLES)
async def user_list(ctx: PacketContext, request: object) -> None:
    """Send the lobby user list using the active service's player records."""
    await _list_users(ctx)  # type: ignore[arg-type]


async def leave_lobby(ctx: LobbyContext, *, disconnected: bool = False) -> None:
    """Remove a player from the lobby, room, and chat presence."""
    if ctx.lobby_id is None or ctx.user is None or ctx.selected_profile is None:
        return
    if ctx.room_id:
        try:
            if disconnected and ROOM_EXIT_RULES[ctx.game].record_disconnect:
                await pes5_disconnect(ctx)  # type: ignore[arg-type]
        except Exception:
            LOGGER.exception("Failed to record player disconnect")
        try:
            await exit_room(ctx, acknowledge=False)  # type: ignore[arg-type]
        except Exception:
            LOGGER.exception("Failed to notify room exit")
    lobby = ctx.lobbies[ctx.lobby_id]
    async with lobby.lock:
        if lobby.players.get(ctx.user.hash) is not ctx:
            return
        del lobby.players[ctx.user.hash]
        peers = tuple(lobby.players.values())
    LOGGER.info("User {%s} exiting lobby %d", ctx.selected_profile.name, ctx.lobby_id)
    LOGGER.debug("Lobby %s population: %d (disconnected=%s)", lobby.name, len(peers), disconnected)
    data = struct.pack("!i", ctx.selected_profile.id)
    for peer in peers:
        try:
            await peer.reply(LobbyPacketId.USER_LEFT, data)
        except (ConnectionError, OSError):
            pass
    ctx.lobby_id = None


# TODO(0x4b01): the success record is verified. The pes5.exe parser (FUN_0083d510)
# reads u32 status | ip1[16] | i16 port1 | ip2[16] | i16 port2 | i32 profile_id
# = 44 bytes, which is PingRecord. What the 0x4b01 case of FUN_00498f40 does:
#   status != 0: stores the status as the result and sets the "answer received"
#       flag (DAT_00fb55ac) so the waiting state advances. The value is not
#       compared there, so ff ff ff ff just means "not found". The record is not
#       copied.
#   status == 0: copies the record only if its profile id equals the id the client
#       asked for (DAT_03b7e4c8). If they differ the flag is never set and the
#       client waits for its own timeout, so PingRecord.profile_id must be the
#       requested id (it is).
# In both cases FUN_0046f310(inet_addr(ip1), port1) then sends a 4-byte UDP
# datagram 02 00 08 00 to ip1:port1 (a NAT / reachability probe, only if the UDP
# socket exists). The parser does not stop at a non-zero status, so on the
# not-found path that probe goes to whatever the record buffer holds; what it
# holds was not verified.
# Non-zero status values (FUN_004954e0, result slot 0x45): any non-zero value
# takes the same "failed" branch; the value itself is never compared, so
# ff ff ff ff is as good as any. Callers: the room-owner ping prober
# (FUN_004a7210, see TODO(0x4320 / 0x4322) in rooms.py) and two menu flows
# (FUN_004829e0, FUN_00485550).
# Texture hint: speed_icon (20316, 3 green / 2 yellow / 1 orange chevrons) looks like
# a connection-quality indicator. This reply carries no latency value, only the
# peer's address, so the client would measure the quality itself over that UDP
# link. Unverified: no code references the texture id.
@packet_handler(LobbyPacketId.PING_REQUEST, games=ALL_GAMES, role=ServiceRole.MAIN)
async def ping_player(ctx: PacketContext, request: object) -> None:
    """Look up another lobby player and return their UDP endpoints."""
    assert isinstance(request, bytes)
    context: LobbyContext = ctx  # type: ignore[assignment]
    if context.lobby_id is None:
        raise ValueError("Invalid peer lookup")
    requested_id = decode_profile_id(request).profile_id
    lobby = context.lobbies[context.lobby_id]
    async with lobby.lock:
        peer = next((item for item in lobby.players.values()
                     if item.selected_profile and item.selected_profile.id == requested_id), None)
    if peer is None:
        await ctx.reply(LobbyPacketId.PING_REPLY, b"\xff" * 4)
        return
    address = peer  # Lobby players are connections carrying their selected network addresses.
    payload = PingRecord(0, address.ip1, address.udp_port1, address.ip2, address.udp_port2, requested_id).pack()
    await ctx.reply(LobbyPacketId.PING_REPLY, payload)


@packet_handler(LobbyPacketId.QUICK_MATCH_REQUEST, games=ALL_GAMES, role=MENU_ROLES)
async def quick_match_search(ctx: PacketContext, request: object) -> None:
    """Return the original no-results response and leave the lobby."""
    context: LobbyContext = ctx  # type: ignore[assignment]
    await ctx.reply(LobbyPacketId.QUICK_MATCH_REPLY, b"\0\0\0\1")
    await leave_lobby(context)
