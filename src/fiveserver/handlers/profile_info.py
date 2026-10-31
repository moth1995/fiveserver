"""Menu profile selection and source-derived profile response layouts."""


import struct
from enum import IntEnum
from typing import Protocol

from ..database import Profile, ProfileStats, User, UserRepository
from ..protocol.dispatcher import ALL_GAMES, PacketContext, ServiceRole, packet_handler
from ..protocol.records import Pes5ProfileRecord, Pes6ProfileRecord, ResultsRecord
from ..protocol.packets import ProfileIdRequest
from ..protocol.formats import ProfileEncoder, active_formats


MENU_ROLES = (ServiceRole.NETWORK_MENU, ServiceRole.MAIN)
PES5_NAME_SIZE = 16
PES6_NAME_SIZE = 48
PES6_COMMENT_SIZE = 256
PES6_RECENT_TEAMS = 5
STATUS_SIZE = 4
# TODO(0x4101): decode PROFILE_SELECT_TAIL into a named record. Reverse-engineered
# from pes5.exe (parser FUN_00839d70), the full 41-byte 0x4101 payload is:
#   +0   u32 BE  status. Non-zero = error; the client stops reading here.
#   +4   i32 BE  profile id. If it differs from the id the client requested,
#                the client overwrites status with 0xFFFFFEF6 (error).
#   +8   32 B    allowed-team bitmask, copied to DAT_03b7cb5c. Team <id> is
#                selectable when mask[id >> 3] & (0x80 >> (id & 7)); only ids
#                0..200 are checked (FUN_00498bd0), so bytes 26..31 are unused.
#   +40  u8      notification flag, copied to DAT_03b7c519 (see below).
# The value below was copied from the original server, which most likely copied
# it from a Konami capture without decoding it. Its final "\x01" is the last mask
# byte and its "\x00" is the flag, so "\x02" * 7 + "\x01" is NOT a separate field.
#
# Mask use: BuildTeamSelectAllCategorySlots (online team select). Your own side
# is filtered by this mask (side 0 for the host, side 1 for the guest); the other
# side uses the opponent's mask DAT_03b7cb7c, which comes from 0x4330 (see the
# TODO in rooms.py). Team 0xEA is always hidden online. Team-id ranges are from
# g_nTeamSelectRangePairs @ 0x00c0f680 and g_nTeamSelectEditOverlayRangePairs
# @ 0x00c0f748. What the current value allows:
#   0-56    national teams (groups 0-19, 20-31, 32-38, 39-50, 51-56)  all allowed
#   57-63   extra teams added to the national groups (57-58, 59-63)   all BLOCKED
#   64-179  six club leagues (64-83, 84-103, 104-121, 122-141,
#           142-159, 160-179)                                         all allowed
#   180-198 other clubs                    180-185 and 198 allowed, 186-197 BLOCKED
#   199-200 last range (199-201)                                      BLOCKED
# To unlock every team, send b"\xff" * 32. Ideally make it configurable (YAML),
# e.g. per lobby, to support restricted lobbies.
#
# Notification flag: when 1, the lobby screen (FUN_0049e250) shows UI element
# 0x102ee (FUN_004aab20), and FUN_004aab90 animates it for about 0x115 frames
# while playing sound 0x8404880d. Packet 0x4704 sets a related flag
# (DAT_03b7c518) that also turns this one on. RESOLVED: it is "you have unread
# MESSAGES". The 0x47xx family is the friends-Messages feature (game text 507:85
# "Compose a new Message or view all old Messages", 507:86/87 received / sent):
# 0x4784 is one message (parser 0x0083ac60: i32 id | i32 | sender name[16] |
# text[512] | date[19] | u32 | u8 | i32, up to 50 kept), 0x4782 / 0x4791 /
# 0x47a1 / 0x4702 are u32 status replies, and 0x4704 is a bare "new message
# arrived" push (no payload). We implement no messages, so keep this byte 0.
# Texture hint (kitserver5/ai_docs/game_textures_ai, atlas AFS entry 264, ids 20300+):
# the online UI has mail icons ol_plist3 / ol_plist5 (envelope, envelope with "!")
# and imp_icon (yellow "!"), which fit this flag (user also reads ol_plist as a
# message icon; 0x4704 setting a related flag is consistent with "new message"). Textures are only registered by id
# in a table (0x00ae7fd0); UI bins pick them, so element 0x102ee -> texture is NOT
# proven. Also unproven: the `division` texture (20276) holds 5 icons in the order
# A, 2, 3B, 3A, 1; the order the u8 division of 0x4103 indexes them is unchecked.
PROFILE_SELECT_TAIL =b"\xff" * 7 + b"\x80" + b"\xff" * 15 + b"\xc0" + b"\x02" * 7 + b"\x01\x00"
DIVISION_THRESHOLDS = (250, 450, 600, 750)


class ProfilePacketId(IntEnum):
    """Packet IDs for menu profile selection and profile details."""
    SELECT = 0x4100
    SELECT_REPLY = 0x4101
    GET = 0x4102
    INFO = 0x4103


def _pad(value: str | bytes, length: int) -> bytes:
    raw = value.encode("utf-8", "replace") if isinstance(value, str) else value
    return raw[:length].ljust(length, b"\0")


def _division(points: int) -> int:
    return next((index for index, threshold in enumerate(DIVISION_THRESHOLDS) if points < threshold), 4)


def format_profile_info(profile: Profile, stats: ProfileStats, *, encoder: ProfileEncoder, show_stats: bool) -> bytes:
    """Apply the legacy hidden-statistics policy before encoding a profile."""
    if not show_stats:
        profile = Profile(profile.index, profile.user_id, profile.id, profile.name)
    return encoder(profile, stats)


def _profile_results(profile: Profile, stats: ProfileStats) -> tuple[int, ResultsRecord]:
    """Prepare the common total and result counters without choosing a layout."""
    return stats.wins + stats.losses + stats.draws, ResultsRecord(
        stats.wins, stats.losses, stats.draws, stats.streak_current, stats.streak_best, profile.disconnects,
    )


# TODO(0x4103): this PES5 reply is 32 bytes SHORTER than what the client parses,
# and two of the "reserved" pairs are not reserved. The pes5.exe parser
# (FUN_0083ba30) reads, after the u32 status:
#   i32 profile_id | name[16] | u8 division | i32 points | u16 games |
#   u16 wins, losses, draws, streak_current, streak_best, disconnects |
#   i32 goals_scored | i32 goals_allowed | u16 favourite_team |
#   i32 favourite_player | i32 rank                    <- 57 bytes, what we send
#   then 5 x (u16, u16, u16) | u8 | u8                 <- 32 more bytes, never sent
# That is 89 bytes after the status (93 = 0x5d in total). The original server has
# a commented-out line padding to range(0x5d - len(data)), so its author knew.
# Read semantics (verified): ReadNetworkPacket* only bound-check the fixed
# 1024-byte buffer, not the packet's real length, so the parser reads every field
# above unconditionally; ReceiveNetworkPacket (008465f0) zero-fills the payload
# before each packet. A short reply is therefore safe: the missing tail reads as
# 0 and the parser does not fail, which is why 61 bytes has worked. That the real
# server sends the full 93 bytes is inferred from this parser plus the 0x5d above.
# - Goals: Pes5ProfileRecord.reserved_scored / reserved_allowed (PAD1 / PAD2 in the
#   original) are the HIGH halves of two i32 counters, not padding. The consumers
#   only use the low 16 bits (FUN_004928d0, goals-allowed per game, masks with
#   0xffff).
# - Last byte of the packet (stored at +0x3c) is the player's LANGUAGE.
#   FUN_00492960 maps it to string ids 0x01F70078..0x7C, which the game text dump
#   (kitserver5/ai_docs/game_text_ai, folder 503, ids 120..124) resolves to
#   English, French, German, Italian, Spanish: the same 0..4 numbering as the
#   language byte the client sends (see TODO(0x4200) in lobbies.py). 0 and 8 both
#   give "English" (8 is the English id of the we2007 Asian install). 5, 6
#   (Korean), 7 and 9+ (Polish) have no string, so the card uses a fallback one.
#   FUN_004929d0 hands the string to the profile card. The caption is probably
#   "Language" (503:109, which sits next to "Your location", 503:119), but
#   captions live in the UI resource and no code references them, so that part is
#   unverified. We send nothing here, so every profile card reads "English".
#   To show real languages: keep the byte the client sends in 0x4100 / 0x4200 per
#   profile and append it as the last byte of this record.
# - The byte before it (+0x3e) and the 5 u16 triples (struct +0x42, stride 8) are
#   parsed, but nothing reads them on PC: the card builder FUN_004abd10 and its
#   accessors (0x004926c0..0x00492a00) never touch +0x3e or +0x40..+0x5e, and the
#   profile copies (0x03b7ca0c / 0x03b7ca98) have no references at those offsets.
#   Treat them as unused (probably a PS2 leftover) and keep them 0. The same
#   5 slots are filled by the HTTP player-info page (send_player_info.html, two
#   numbers per slot). Text hint:
#   507:29 / 507:55 "View the results of the 5 most recent games ... Match cards
#   for the 5 most recent games and results info (win / draw / loss)" fits five
#   records of three u16 (e.g. team, own goals, opponent goals).
# - The profile-card icon byte (+0x3f, FUN_00492a00) is forced to 0 by the parser,
#   so it never comes from the wire in 0x4103. It DOES come from the wire in
#   0x4322 (the challenge card): it is the challenger's ping grade, see
#   TODO(0x4320 / 0x4322) in rooms.py. Its only reader is the profile-card builder
#   FUN_004abd10: values 2/3/4 select frame 0/1/2 of the card's UI element
#   (bin 0x102f7, child 6); 0, 1 and anything else select frame 3 (probably the
#   blank one). The 3 frames likely are the win01 (crown) / win02 (medal) style
#   badges, but this is a guess: UI bins are not decoded, and no code references
#   the win01/win02 texture ids (20318/20319), they are only registered by id.
# - Division byte (+0x15, FUN_004926e0; values 0..4 are valid, 5+ = no icon):
#   icon = UI bin 0x102ea, frame = {4,3,2,1,0}[division] (table 0x00b17f1c), and
#   the card colour is {grey, green, blue, purple, red}[division] (table
#   0x00b17f70). Colours rise with the index, so 0 = lowest division and 4 =
#   highest, the same direction as _division() below (<250 pts -> 0). Which icon
#   each frame shows (texture `division` holds A, 2, 3B, 3A, 1) is unverified.
# Card fields confirmed from their readers: +0x38 rank (shown as "%d", "-" if 0),
# +0x1e / +0x20 current / best streak ("%3d / %3d"), +0x24 favourite team (badge),
# +0x26 / +0x28 favourite player (low / high u16), goals per game ("%3.2f").
def encode_pes5_profile(profile: Profile, stats: ProfileStats) -> bytes:
    """Encode the independently verified 57-byte PES5 detail record."""
    games, results = _profile_results(profile, stats)
    return Pes5ProfileRecord(
        profile.id, _pad(profile.name, PES5_NAME_SIZE), _division(profile.points),
        profile.points, games, results, bytes(2), stats.goals_scored,
        bytes(2), stats.goals_allowed, profile.fav_team, profile.fav_player, profile.rank,
    ).pack()


def encode_pes6_profile(profile: Profile, stats: ProfileStats) -> bytes:
    """Encode the independently verified 418-byte PES6 detail record."""
    games, results = _profile_results(profile, stats)
    comment = profile.comment or "Fiveserver rules!"
    return Pes6ProfileRecord(
        profile.id, _pad(profile.name, PES6_NAME_SIZE), 0, b"Playmakers", 1,
        _division(profile.points), profile.points, profile.rating, games, results,
        stats.goals_scored, stats.goals_allowed, _pad(comment, PES6_COMMENT_SIZE),
        profile.rank, bytes(14), list(stats.teams) + [0xffff] * (PES6_RECENT_TEAMS - len(stats.teams)),
    ).pack()


class ProfileContext(PacketContext, Protocol):
    """Connection state required by profile-info handlers."""
    user: User | None
    selected_profile: Profile | None
    repository: UserRepository
    settings: dict[str, object]


async def _send_info(ctx: ProfileContext, profile_id: int) -> None:
    profile = await ctx.repository.profile_by_id(profile_id)
    if profile is None:
        await ctx.reply(ProfilePacketId.INFO)
        return
    show_stats = bool(ctx.settings.get("ShowStats", True))
    stats = await ctx.repository.profile_stats(profile.id) if show_stats else ProfileStats()
    await ctx.reply(
        ProfilePacketId.INFO,
        bytes(STATUS_SIZE) + format_profile_info(profile, stats, encoder=active_formats(ctx).profile_info, show_stats=show_stats),
    )


# TODO(0x4100): byte 1 is the client language (pes5/WE9LEK: [index, lang];
# pes6/we2007: [index, lang, unknown]). See the TODO(0x4200) in lobbies.py.
def _index(payload: bytes) -> int:
    if not payload or payload[0] >= 3:
        raise ValueError("Invalid profile index")
    return payload[0]


async def _select(ctx: ProfileContext, index: int) -> None:
    if ctx.user is None:
        raise ValueError("Profile selection requires authentication")
    ctx.selected_profile = ctx.user.profiles[index]
    profile_id = ctx.selected_profile.id
    await ctx.reply(ProfilePacketId.SELECT_REPLY, bytes(STATUS_SIZE) + struct.pack("!i", profile_id) + PROFILE_SELECT_TAIL)
    await _send_info(ctx, profile_id)


@packet_handler(ProfilePacketId.SELECT, games=ALL_GAMES, role=MENU_ROLES, decoder=_index)
async def select_menu_profile(ctx: PacketContext, request: object) -> None:
    """Select a profile and send details in the active service's wire format."""
    assert isinstance(request, int)
    await _select(ctx, request)  # type: ignore[arg-type]


@packet_handler(ProfilePacketId.GET, games=ALL_GAMES, role=MENU_ROLES, request_type=ProfileIdRequest)
async def get_menu_profile(ctx: ProfileContext, request: ProfileIdRequest) -> None:
    """Return profile details by ID using the active service's wire format."""
    await _send_info(ctx, request.profile_id)
