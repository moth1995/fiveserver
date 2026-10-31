"""Login and profile command handlers wired into the production registry."""

import asyncio
import logging
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol

from ..database import Profile, User, UserRepository
from ..protocol.codecs import (
    PES5_NAME_SIZE,
    AuthenticationRequest,
    MatchResultRecord,
    decode_pes5_auth,
    decode_pes6_auth,
)
from ..protocol.dispatcher import (
    ALL_GAMES,
    SIX_GAMES,
    GameId,
    PacketContext,
    ServiceRole,
    packet_handler,
)
from ..protocol.formats import active_formats
from ..protocol.packets import ProfileIdRequest, TeamSelectRequest
from ..protocol.records import LoginProfileState, Pes5LoginProfile, Pes6LoginProfile

AUTH_REQUEST_ID = 0x3003
AUTH_RESPONSE_ID = 0x3004
PROFILE_REQUEST_ID = 0x3010
PROFILE_RESPONSE_ID = 0x3012
# TODO(0x3004): VERIFIED in pes5.exe. The status is stored in result slot 2
# (0x00fb5b90); FUN_004a2120 only checks 0 / non-zero, and the dialog is chosen by
# the network-connect flow FUN_0048ecb0 (state 3, via FUN_004a2490):
#   0xFFFFFF10 -> 520:8  "An error occurred during verification."
#   0xFFFFFF0E -> 520:9  "Residual connection data exists. Please try to connect
#                         again later."
#   0xFFFFFF0D -> 906:8  "Please update to the latest version and then reconnect."
#   0xFFFFFEE4 -> 520:13 "Blocked Registration code entered."
#   any other  -> 520:5  "Service is unavailable at this time."
# (A connection failure before auth shows 501:6 "Temporarily unable to connect to
# server. Please try again later.") The other login flow, FUN_0048e6c0, shows no
# code-specific text and just drops to frontend screen 0xc.
# So with our codes: AUTH_INVALID (ff10) is correct; AUTH_ALREADY_ONLINE (ff11)
# and AUTH_BAD_ROSTER (ff12) both fall to the generic 520:5. Better fits would be
# 0xFFFFFF0E ("residual connection data", i.e. already online) for
# AUTH_ALREADY_ONLINE, 0xFFFFFF0D ("update to the latest version") for
# AUTH_BAD_ROSTER, and 0xFFFFFEE4 for banned accounts. Codes not changed.
AUTH_INVALID = 0xFFFFFF10
AUTH_ALREADY_ONLINE = 0xFFFFFF11
AUTH_BAD_ROSTER = 0xFFFFFF12
LOGIN_ROLES = (ServiceRole.LOGIN, ServiceRole.NETWORK_MENU, ServiceRole.MAIN)
MENU_ROLES = (ServiceRole.NETWORK_MENU, ServiceRole.MAIN)
LOGGER = logging.getLogger(__name__)


class LoginPacketId(IntEnum):
    """Packet IDs used by login, profile, settings, and result screens."""

    HELLO_REQUEST = 0x3001
    HELLO_REPLY = 0x3002
    DISCONNECT = 0x0003
    PROFILE_CREATE = 0x3020
    PROFILE_CREATE_REPLY = 0x3022
    PROFILE_DELETE = 0x3030
    PROFILE_DELETE_REPLY = 0x3032
    PROFILE_SELECT = 0x3040
    PROFILE_NOT_FOUND = 0x3041
    PROFILE_SELECTED = 0x3042
    PROFILE_MORE = 0x3050
    PROFILE_MORE_REPLY = 0x3052
    PROFILE_CHECK = 0x3060
    PROFILE_CHECK_REPLY = 0x3062
    ACK_3090 = 0x3090
    ACK_3091 = 0x3091
    ACK_3100 = 0x3100
    ACK_3101 = 0x3101
    ACK_3120 = 0x3120
    ACK_3121 = 0x3121
    ACK_3123 = 0x3123
    MATCH_RESULTS_REQUEST = 0x3070
    MATCH_RESULTS_RESPONSE = 0x3072
    PES6_MATCH_RESULTS_START = 0x3071
    PES6_MATCH_RESULTS_END = 0x3073
    SETTINGS_REQUEST = 0x308A
    SETTINGS_INFO = 0x3087
    SETTINGS_DATA = 0x3088
    SETTINGS_END = 0x3089
    SETTINGS_COMMIT_REPLY = 0x308B
    SET_FAVOURITE_TEAM = 0x4110
    SET_COMMENT_REPLY = 0x4111
    SET_FAVOURITE_TEAM_REPLY = 0x4112
    SET_FAVOURITE_PLAYER = 0x4114
    SET_FAVOURITE_PLAYER_REPLY = 0x4116


# TODO(0x3022 / 0x3032): status codes the client turns into dialogs. Verified in
# pes5.exe: the result of packet 0x3022 is stored at 0x00fb5bac and of 0x3032 at
# 0x00fb5bb0 (result slot n = 0x00fb5b88 + 4n, i.e. slots 9 and 10); the profile
# screen FUN_0048db90 reads them through FUN_004a3900 / FUN_004a3920. Text from
# the game text dump (kitserver5/ai_docs/game_text_ai, folder 501):
#   0x3022 create reply
#     0xFFFFFEFC (-0x104) "Another player exists with the same name." (501:20)
#         = PROFILE_NAME_EXISTS below
#     0xFFFFFEFA (-0x106) "Includes unusable letters or phrases." (501:19)
#         NOT SENT by us; it would let the server reject names that match
#         Chat.bannedWords
#     any other non-zero: the client silently returns to the profile list
#   0x3032 delete reply
#     0xFFFFFEF4 (-0x10c) "You cannot delete a player file for one week
#         following its creation." (501:15). NOT SENT: we always answer 0
#         (success); enforcing it needs a profile creation timestamp.
#     any other non-zero: the client goes back to the list without a dialog
PROFILE_NAME_EXISTS = 0xFFFFFEFC
MAX_PROFILES = 3
SELECT_PROFILE_PAYLOAD_SIZE = 0x18E
MORE_PROFILE_REPLY_SIZE = 0x47
SETTINGS_UNAVAILABLE = 0xFFFFFEDD


class LoginContext(PacketContext, Protocol):
    """Connection state required by login and profile handlers."""

    settings: dict[str, Any]
    user: User | None
    repository: UserRepository
    sessions: dict[str, object]
    session_lock: asyncio.Lock | None
    selected_profile: Profile | None
    user_info: dict[str, tuple[GameId, bytes | None]]


class AuthenticationService:
    """Validate game-client credentials and claim authenticated sessions."""

    def __init__(
        self,
        repository: UserRepository,
        sessions: dict[str, object],
        lock: asyncio.Lock | None = None,
    ):
        self.repository = repository
        self.sessions = sessions
        self.lock = lock

    async def authenticate(
        self, request: AuthenticationRequest, connection: object, enforce_roster: bool
    ) -> tuple[int, User | None]:
        """Look up the user, enforce optional roster policy, and claim the online session."""
        user = await self.repository.by_hash(request.user_hash)
        if user is None:
            return AUTH_INVALID, None
        if self.lock is None:
            return self._claim_session(user, request, connection, enforce_roster)
        async with self.lock:
            return self._claim_session(user, request, connection, enforce_roster)

    def _claim_session(
        self,
        user: User,
        request: AuthenticationRequest,
        connection: object,
        enforce_roster: bool,
    ) -> tuple[int, User | None]:
        if user.hash in self.sessions:
            return AUTH_ALREADY_ONLINE, user
        # The source accepts an empty roster field even when enforcement is on;
        # it only rejects hashes containing four consecutive zero bytes.
        if (
            enforce_roster
            and request.roster_hash is not None
            and b"\0\0\0\0" in request.roster_hash
        ):
            return AUTH_BAD_ROSTER, user
        if request.roster_hash is not None:
            user.roster_hash = request.roster_hash
        self.sessions[user.hash] = connection
        return 0, user


# TODO(0x3002): these 16 bytes are the authentication NONCE, and sending zeros
# makes the login credential static. Verified in pes5.exe: the 0x3002 parser
# (FUN_00839a90) reads exactly one 16-byte field into the client's authChallenges
# slot, and SendAuthenticationResponse (0x00839b00) computes
#   digest = MD5(login ‖ challenge[16] ‖ password)
# (the MD5 length is strlen(login) + 0x10 + strlen(password); login is the 32-byte
# serial) and sends 0x3003 = [login 32 B][digest 16 B], Blowfish-encrypted. The
# client does not treat an all-zero challenge specially.
# Consequence (a design limit inherited from the original; behaviour unchanged):
# with a zero nonce the digest is identical on every login. registration.py:123
# stores Blowfish(CIPHER_KEY, digest) and the lookup is a plain equality match
# (by_hash), so a captured 0x3003 or a leaked users.hash value is equivalent to the
# password: it logs in without knowing it. CIPHER_KEY is fixed and public.
# A real per-login nonce would need the server to recompute
# MD5(serial ‖ nonce ‖ password), i.e. to keep the plaintext (or a reversible form
# of the password), which means a new registration scheme and migrating accounts.
# The registration page must compute its digest with this same 16-zero nonce, or
# logins would fail.
@packet_handler(LoginPacketId.HELLO_REQUEST, games=ALL_GAMES, role=LOGIN_ROLES)
async def login_hello(ctx: PacketContext, payload: object) -> None:
    """Answer the login hello with the original 16 zero bytes."""
    await ctx.reply(LoginPacketId.HELLO_REPLY, bytes(16))


def _log_authentication(status: int, user: User | None, context: LoginContext) -> None:
    if status == 0 and user is not None:
        # Upstream printed the credential hash here. Use the authenticated name,
        # not the secret, while retaining the meaningful identification event.
        LOGGER.info(
            "This user is: {%s} (Login accepted; game %s)", user.username, context.game
        )
    elif status == AUTH_ALREADY_ONLINE:
        LOGGER.warning("User already logged in! Login rejected (already online)")
    elif status == AUTH_BAD_ROSTER:
        LOGGER.warning("Roster-hash check FAILED. Login rejected (roster hash)")
    else:
        LOGGER.warning("UnknownUserError: Login rejected (unknown user)")


@packet_handler(
    AUTH_REQUEST_ID, games=ALL_GAMES, role=LOGIN_ROLES, decoder=decode_pes5_auth
)
async def authenticate(ctx: PacketContext, payload: object) -> None:
    """Handle PES5 authentication and publish the latest game/roster info."""
    context: LoginContext = ctx  # type: ignore[assignment]
    request: AuthenticationRequest = payload  # type: ignore[assignment]
    roster = context.settings.get("Roster", {})
    enforce = (
        bool(roster.get("enforceHash", False)) if isinstance(roster, dict) else False
    )
    service = AuthenticationService(
        context.repository, context.sessions, getattr(context, "session_lock", None)
    )
    status, user = await service.authenticate(request, context, enforce)
    context.user = user
    if status == 0 and user is not None:
        if context.role == ServiceRole.LOGIN:
            context.user_info[user.username] = (context.game, request.roster_hash)
        elif user.username in context.user_info:
            context.game, user.roster_hash = context.user_info[user.username]
        user.game_name = context.game.value
    _log_authentication(status, user, context)
    await ctx.reply(AUTH_RESPONSE_ID, struct.pack("!I", status))


@packet_handler(
    AUTH_REQUEST_ID,
    games=SIX_GAMES,
    role=LOGIN_ROLES,
    decoder=decode_pes6_auth,
    override=True,
)
async def authenticate_pes6(ctx: PacketContext, payload: object) -> None:
    """Handle PES6 authentication using the PES6 roster offset."""
    await authenticate(ctx, payload)


def _division(points: int) -> int:
    for index, threshold in enumerate((250, 450, 600, 750)):
        if points < threshold:
            return index
    return 4


def encode_pes5_login_profile(
    index: int, profile: Profile, state: LoginProfileState, rating: int, games: int
) -> bytes:
    """Encode a PES5 login-list row; rating has no wire field in this format."""
    return Pes5LoginProfile(
        index, profile.id, profile.name.encode("utf-8"), state, games
    ).pack()


def encode_pes6_login_profile(
    index: int, profile: Profile, state: LoginProfileState, rating: int, games: int
) -> bytes:
    """Encode a PES6 login-list row including the prepared rating."""
    return Pes6LoginProfile(
        index, profile.id, profile.name.encode("utf-8"), state, rating, games
    ).pack()


async def _profiles(ctx: LoginContext) -> None:
    if ctx.user is None:
        raise ValueError("Profiles requested before authentication")
    data = bytearray(4)
    show_stats = bool(ctx.settings.get("ShowStats", True))
    for index, profile in enumerate(ctx.user.profiles):
        points = profile.points if show_stats else 0
        games = await ctx.repository.games_played(profile.id) if show_stats else 0
        state = LoginProfileState(
            int(profile.play_time.total_seconds()) if show_stats else 0,
            _division(points),
            points,
        )
        data += active_formats(ctx).login_profile(
            index, profile, state, profile.rating if show_stats else 0, games
        )
    await ctx.reply(PROFILE_RESPONSE_ID, bytes(data))


@packet_handler(PROFILE_REQUEST_ID, games=ALL_GAMES, role=LOGIN_ROLES)
async def get_profiles(ctx: PacketContext, payload: object) -> None:
    """Return profile rows using the active service's login-list layout."""
    await _profiles(ctx)  # type: ignore[arg-type]


@packet_handler(LoginPacketId.DISCONNECT, games=ALL_GAMES, role=LOGIN_ROLES)
async def login_disconnect(ctx: PacketContext, payload: object) -> None:
    """Mark the user offline before closing the login transport."""
    context: LoginContext = ctx  # type: ignore[assignment]
    # The source marks the user offline before closing the transport.
    if context.user is not None and context.sessions.get(context.user.hash) is ctx:
        del context.sessions[context.user.hash]
        LOGGER.info(
            "User disconnected: user=%s game=%s", context.user.username, context.game
        )
    await ctx.disconnect()


def _user(ctx: LoginContext) -> User:
    if ctx.user is None:
        raise ValueError("Command requires authentication")
    return ctx.user


@dataclass(frozen=True)
class CreateProfileRequest:
    """Decoded 0x3020 profile index and name."""

    index: int
    name: str


def decode_create_profile(payload: bytes) -> CreateProfileRequest:
    """Decode a profile index and null-terminated name."""
    if not payload or payload[0] >= MAX_PROFILES:
        raise ValueError("Invalid profile index")
    return CreateProfileRequest(
        payload[0], payload[1:].split(b"\0", 1)[0].decode("utf-8")
    )


@packet_handler(
    LoginPacketId.PROFILE_CREATE,
    games=ALL_GAMES,
    role=LOGIN_ROLES,
    decoder=decode_create_profile,
)
async def create_profile(ctx: PacketContext, payload: object) -> None:
    """Create or rename a profile after checking global name uniqueness."""
    context: LoginContext = ctx  # type: ignore[assignment]
    request: CreateProfileRequest = payload  # type: ignore[assignment]
    user = _user(context)
    if await context.repository.profile_name_exists(request.name):
        LOGGER.warning(
            "Profile create rejected (name exists): user=%s name=%s",
            user.username,
            request.name,
        )
        await ctx.reply(
            LoginPacketId.PROFILE_CREATE_REPLY, struct.pack("!I", PROFILE_NAME_EXISTS)
        )
        return
    profile = user.profiles[request.index]
    profile.name = request.name
    profile.points = 0
    user.profiles[request.index] = await context.repository.save_profile(profile)
    LOGGER.info(
        "Profile created: user=%s name=%s slot=%d",
        user.username,
        request.name,
        request.index,
    )
    await ctx.reply(LoginPacketId.PROFILE_CREATE_REPLY, bytes(4))


def decode_profile_index(payload: bytes) -> int:
    """Decode and validate a profile slot index."""
    if not payload or payload[0] >= MAX_PROFILES:
        raise ValueError("Invalid profile index")
    return payload[0]


@packet_handler(
    LoginPacketId.PROFILE_DELETE,
    games=ALL_GAMES,
    role=LOGIN_ROLES,
    decoder=decode_profile_index,
)
async def delete_profile(ctx: PacketContext, payload: object) -> None:
    """Soft-delete a profile and clear its session slot."""
    context: LoginContext = ctx  # type: ignore[assignment]
    assert isinstance(payload, int)
    profile = _user(context).profiles[payload]
    LOGGER.info(
        "Profile deleted: user=%s name=%s slot=%d",
        context.user.username if context.user else "",
        profile.name,
        payload,
    )
    await context.repository.delete_profile(profile)
    profile.id = 0
    profile.name = ""
    profile.points = 0
    await ctx.reply(LoginPacketId.PROFILE_DELETE_REPLY, bytes(4))


# TODO(0x3042): the client parses only the first 61 bytes of these 0x18E (the rest
# is ignored). pes5.exe parser FUN_0083d9e0, in order after the u32 status:
#   name[16] | u8 division | i32 points | u16 x7 (games, then six result counters,
#   in the same order as 0x4103) | i32 goals_scored | i32 goals_allowed |
#   u16 favourite_team | i32 favourite_player | i32 rank | i32 (trailing)
# i.e. the 0x4103 profile record without the leading profile id, plus one trailing
# i32. The 0x3042 case of FUN_00498f40 puts the requested profile id at 0x03b7ca0c
# and this record right after it: that 0x8c-byte block is the client's OWN profile
# card (read by FUN_004925c0, which supplies the profile id for 0x3090 / 0x3100,
# by FUN_004933e0, and by FUN_00495a30 as the "old points" base of the result
# screen). The trailing i32 goes to DAT_03b7e154, which is only ever written,
# never read: unused. We send zeros for everything but the name, as the original
# did, so the own card shows empty stats until a 0x4103 refresh; sending the real
# values (same encoder as 0x4103) would be correct.
@packet_handler(
    LoginPacketId.PROFILE_SELECT,
    games=ALL_GAMES,
    role=LOGIN_ROLES,
    request_type=ProfileIdRequest,
)
async def select_profile(ctx: LoginContext, payload: ProfileIdRequest) -> None:
    """Select a profile and send the original 0x3042 payload."""
    context = ctx
    selected = next(
        (
            profile
            for profile in _user(context).profiles
            if profile.id == payload.profile_id
        ),
        None,
    )
    context.selected_profile = selected
    if selected is None:
        LOGGER.warning(
            "Profile selection failed: user=%s profile_id=%d",
            context.user.username if context.user else "",
            payload.profile_id,
        )
        await ctx.reply(LoginPacketId.PROFILE_NOT_FOUND, bytes(4))
        return
    LOGGER.info(
        "Profile selected: user=%s profile=%s",
        context.user.username if context.user else "",
        selected.name,
    )
    name = selected.name.encode("utf-8")[:PES5_NAME_SIZE].ljust(PES5_NAME_SIZE, b"\0")
    await ctx.reply(
        LoginPacketId.PROFILE_SELECTED,
        bytes(4) + name + bytes(SELECT_PROFILE_PAYLOAD_SIZE - 4 - PES5_NAME_SIZE),
    )


# TODO(0x3052): the pes5.exe parser (FUN_0083dd00) reads 65 of these 71 bytes:
# u32 status | u8 | i32 | u8 | i32 x4 | date[19] | i32 x5 (a non-zero status makes
# the parser fail). The request 0x3050 (builder 0x0083dca0) is u32 profile id,
# sent by FUN_004a3340 as step 0x12 of the "enter Network Mode" sequence
# (FUN_0048db90), right after profile select. RESOLVED: it is the DIVISION UPDATE
# record shown by the "Latest division changes" screen (504:34): new division u8,
# division rank after i32, old division u8, division rank before, total POINTS,
# POINTS up to last update, POINTS earned from title (bonus; captions 504:23..29),
# update date, then five i32 = NUMBER OF PLAYERS in each division (Amateur, 3B, 3A,
# Div 2, Div 1), drawn next to a person icon on a five-row ladder headed "Info as
# of <date>" (UI bin 0x102e9, texture "number"). Full field table
# and the original rank-based division system. All zeros here, as in the
# original; the client accepts that.
# 0x3062 (reply to 0x3060, PROFILE_CHECK below), for completeness: parser
# FUN_0083de60 reads u32 status, then up to 5 records of {u8 slot, u16, u16, u16}
# (the same shape as the 5 triples in 0x4103). FUN_004a3aa0 sends 0x3060 once per
# profile on the profile-select screen and copies the records to lobby UI data
# +0x00..+0x77. The only reference found passes their ADDRESSES (never values) to
# the class-title helper, so their content is unused. We reply with a single 0
# byte, which the client reads as status 0 and an empty list: fine.
# Profile-select screen, related: the title under each profile is a "class" from
# its POINTS (FUN_00492a20 thresholds 500, 1500, 3000, 5000, 8000, 12000, 16000,
# 21000, 27000, 33000, 40000, 48000, 57000, 67000, 77000 -> game text 514:1..15
# "Class 1" .. "Class 10", "Class 10+", "Star", "Superstar", "Master",
# "Grand Master"); play time is shown as h:m:s from the 0x3012 seconds field.
@packet_handler(LoginPacketId.PROFILE_MORE, games=ALL_GAMES, role=LOGIN_ROLES)
async def profile_more(ctx: PacketContext, payload: object) -> None:
    """Return the fixed 0x3052 profile-detail placeholder."""
    await ctx.reply(LoginPacketId.PROFILE_MORE_REPLY, bytes(MORE_PROFILE_REPLY_SIZE))


@packet_handler(LoginPacketId.PROFILE_CHECK, games=ALL_GAMES, role=LOGIN_ROLES)
async def profile_check(ctx: PacketContext, payload: object) -> None:
    """Return the original one-byte profile-check response."""
    await ctx.reply(LoginPacketId.PROFILE_CHECK_REPLY, b"\0")


# TODO(0x3090 / 0x3100 / 0x3120): verified in pes5.exe. These are the last three
# steps of the "enter Network Mode" sequence (FUN_0048db90 states 0x16, 0x17, 0x18,
# after profile select):
#   0x3090 = i32 profile id | u8 (always 1 there; also sent from FUN_00477260)
#   0x3100 = i32 profile id | u8 LANGUAGE (GetGameLanguage, see TODO(0x4200))
#   0x3120 = i32 local lobby player id
# Each reply (0x3091 / 0x3101 / 0x3121) is only u32 status (parsers 0x0083e550,
# 0x0083e610), stored in result slots 0x11, 0x12 and 7. 0 lets the client move to
# the next step; anything else drops the client to the generic error state 0x1f.
# What the server is meant to do with the 0x3090 flag is unknown.
# After 0x3121 the client accepts a list of 0x3122 records with the SAME layout
# as 0x200A news (parser 0x0083e710, see TODO(0x200A) in news.py), ended by
# 0x3123. 0x3121 resets the client's news list and 0x3122 appends to that same
# list, so these items appear in the in-game news screen. We send 0x3121 +
# 0x3123 only, i.e. an empty list.
@packet_handler(LoginPacketId.ACK_3090, games=ALL_GAMES, role=LOGIN_ROLES)
async def ack_3090(ctx: PacketContext, payload: object) -> None:
    """Acknowledge packet 0x3090 with 0x3091."""
    await ctx.reply(LoginPacketId.ACK_3091, bytes(4))


@packet_handler(LoginPacketId.ACK_3100, games=ALL_GAMES, role=LOGIN_ROLES)
async def ack_3100(ctx: PacketContext, payload: object) -> None:
    """Acknowledge packet 0x3100 with 0x3101."""
    await ctx.reply(LoginPacketId.ACK_3101, bytes(4))


@packet_handler(LoginPacketId.ACK_3120, games=ALL_GAMES, role=LOGIN_ROLES)
async def ack_3120(ctx: PacketContext, payload: object) -> None:
    """Acknowledge packet 0x3120 with 0x3121 and 0x3123."""
    await ctx.reply(LoginPacketId.ACK_3121, bytes(4))
    await ctx.reply(LoginPacketId.ACK_3123)


@packet_handler(
    LoginPacketId.SET_FAVOURITE_TEAM,
    games=ALL_GAMES,
    role=MENU_ROLES,
    request_type=TeamSelectRequest,
)
async def set_favourite_team(ctx: LoginContext, payload: TeamSelectRequest) -> None:
    """Persist the selected profile's favourite team."""
    context = ctx
    if context.selected_profile is None:
        raise ValueError(
            "Favourite team update requires a selected profile and two bytes"
        )
    profile = context.selected_profile
    profile.fav_team = payload.team_id
    await context.repository.save_profile(profile)
    await ctx.reply(LoginPacketId.SET_FAVOURITE_TEAM_REPLY, bytes(4))


@packet_handler(
    LoginPacketId.SET_FAVOURITE_TEAM,
    games=SIX_GAMES,
    role=ServiceRole.MAIN,
    override=True,
)
async def set_pes6_comment(ctx: PacketContext, payload: object) -> None:
    """Persist the PES6 profile comment, which reuses packet ``0x4110``.

    PES6 replaces the PES5 favourite-team meaning of this command on the main
    service; the reply ID is ``0x4111``.
    """
    context: LoginContext = ctx  # type: ignore[assignment]
    if not isinstance(payload, bytes) or context.selected_profile is None:
        raise ValueError("Comment update requires a selected profile")
    context.selected_profile.comment = payload
    await context.repository.save_profile(context.selected_profile)
    await ctx.reply(LoginPacketId.SET_COMMENT_REPLY, bytes(4))


@packet_handler(
    LoginPacketId.SET_FAVOURITE_PLAYER,
    games=ALL_GAMES,
    role=MENU_ROLES,
    request_type=ProfileIdRequest,
)
async def set_favourite_player(ctx: LoginContext, payload: ProfileIdRequest) -> None:
    """Persist the selected profile's favourite player."""
    context = ctx
    if context.selected_profile is None:
        raise ValueError(
            "Favourite player update requires a selected profile and four bytes"
        )
    profile = context.selected_profile
    profile.fav_player = payload.profile_id
    await context.repository.save_profile(profile)
    await ctx.reply(LoginPacketId.SET_FAVOURITE_PLAYER_REPLY, bytes(4))


@packet_handler(
    LoginPacketId.MATCH_RESULTS_REQUEST,
    games=ALL_GAMES,
    role=LOGIN_ROLES,
    request_type=ProfileIdRequest,
)
async def get_match_results(ctx: LoginContext, payload: ProfileIdRequest) -> None:
    """Return up to ten PES5 match-result records oldest first."""
    context = ctx
    profile_id = payload.profile_id
    selected = next(
        (profile for profile in _user(context).profiles if profile.id == profile_id),
        None,
    )
    context.selected_profile = selected
    response = bytearray(4)
    if selected is not None:
        for (
            opponent_id,
            my_score,
            opponent_score,
            my_team,
            opponent_team,
            played_on,
        ) in reversed(await context.repository.last_matches(profile_id)):
            name = await context.repository.profile_name(opponent_id)
            response += MatchResultRecord(
                0,
                played_on.strftime("%Y/%m/%d %H:%M:%S").encode("ascii"),
                name.encode("utf-8")[:PES5_NAME_SIZE],
                opponent_id,
                my_score,
                opponent_score,
                my_team,
                opponent_team,
            ).pack()
    await ctx.reply(LoginPacketId.MATCH_RESULTS_RESPONSE, bytes(response))


@packet_handler(
    LoginPacketId.MATCH_RESULTS_REQUEST,
    games=SIX_GAMES,
    role=ServiceRole.LOGIN,
    override=True,
)
async def get_match_results_pes6(ctx: PacketContext, payload: object) -> None:
    """Return the empty PES6 match-results start/end exchange."""
    await ctx.reply(LoginPacketId.PES6_MATCH_RESULTS_START, bytes(4))
    await ctx.reply(LoginPacketId.PES6_MATCH_RESULTS_END, bytes(4))


@packet_handler(LoginPacketId.SETTINGS_REQUEST, games=ALL_GAMES, role=LOGIN_ROLES)
async def request_settings(ctx: PacketContext, payload: object) -> None:
    """Send stored profile settings or the original unavailable status."""
    context: LoginContext = ctx  # type: ignore[assignment]
    if context.selected_profile is None or not context.settings.get(
        "StoreSettings", True
    ):
        await ctx.reply(
            LoginPacketId.SETTINGS_INFO, struct.pack("!I", SETTINGS_UNAVAILABLE)
        )
        return
    first, second = await context.repository.profile_settings(
        context.selected_profile.id
    )
    if first is None or second is None:
        await ctx.reply(
            LoginPacketId.SETTINGS_INFO, struct.pack("!I", SETTINGS_UNAVAILABLE)
        )
        return
    await ctx.reply(
        LoginPacketId.SETTINGS_INFO,
        bytes(4) + struct.pack("!I", context.selected_profile.id),
    )
    await ctx.reply(LoginPacketId.SETTINGS_DATA, zlib.decompress(first))
    await ctx.reply(LoginPacketId.SETTINGS_DATA, zlib.decompress(second))
    await ctx.reply(LoginPacketId.SETTINGS_END)


@packet_handler(LoginPacketId.SETTINGS_DATA, games=ALL_GAMES, role=LOGIN_ROLES)
async def receive_settings(ctx: PacketContext, payload: object) -> None:
    """Store received settings in settings2, preserving the original Python 3 behavior."""
    context: LoginContext = ctx  # type: ignore[assignment]
    if (
        context.selected_profile is None
        or not isinstance(payload, bytes)
        or len(payload) < 3
    ):
        raise ValueError(
            "Settings data requires a selected profile and at least three bytes"
        )
    # The original Python 3 handler compares an int with bytes, so all packets update settings2.
    context.selected_profile.settings2 = zlib.compress(payload)


@packet_handler(LoginPacketId.SETTINGS_END, games=ALL_GAMES, role=LOGIN_ROLES)
async def commit_settings(ctx: PacketContext, payload: object) -> None:
    """Reply to settings commit and persist settings when enabled."""
    context: LoginContext = ctx  # type: ignore[assignment]
    await ctx.reply(LoginPacketId.SETTINGS_COMMIT_REPLY, bytes(4))
    if context.settings.get("StoreSettings", True):
        if context.selected_profile is None:
            raise ValueError("Settings commit requires a selected profile")
        await context.repository.store_profile_settings(context.selected_profile)
