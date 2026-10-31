"""Match completion and source-compatible ranking updates."""


import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Protocol

from ..database import MatchRecord, Profile, ProfileStats, UserRepository
from ..protocol.dispatcher import ALL_GAMES, SIX_GAMES, PacketContext, ServiceRole, packet_handler
from ..protocol.packets import TeamSelectRequest
from ..protocol.records import PlayerSettingsRecord, PostMatchPlayerRecord
from .lobby_state import Lobby, NO_STATS_LOBBY_CODE
from .rooms import PES5Match, Room, RoomContext, RoomPacketId
from ..protocol.formats import WireFormats, active_formats


RATING_PERFORMANCE_WEIGHT = 0.44
RATING_GAMES_WEIGHT = 0.56
DRAW_VALUE = 0.333
GAMES_DECAY = 0.05
RATING_SCALE = 1000
STATUS_SIZE = 4
HOME_SIDE = 0
PES6_TEAM_SLOTS = 4
PES6_TEAM_SLOT_SIZE = 8
PES6_FIRST_HALF = 1
PES6_FINISHED = 10
PES6_GOAL_STATES = {1: 0, 3: 1, 5: 2, 7: 3, 9: 4}
PES6_POINTS_ENTRY_SIZE = 22
MATCH_STATE_TEXT = {
    0: "Not started", 1: "1st half", 2: "Half-time", 3: "2nd half",
    4: "Normal time finished", 5: "Extra-time 1st half", 6: "Extra-time intermission",
    7: "Extra-time 2nd half", 8: "Before penalties", 9: "Penalties", 10: "Finished",
}
LOGGER = logging.getLogger(__name__)


class MatchPacketId(IntEnum):
    """Packet IDs used by PES5 and PES6 match progress."""
    FINISH_SERIES = 0x3087
    PES5_GOAL = 0x4368
    PES5_GOAL_REPLY = 0x4369
    PES5_EXIT = 0x4370
    PES5_EXIT_REPLY = 0x4371
    PES6_PLAYER_SETTINGS = 0x4369
    PES6_PLAYER_SETTINGS_REPLY = 0x436A
    PES6_PLAYER_SETTINGS_UPDATE = 0x436B
    PES6_GAME_SETTINGS = 0x436C
    PES6_GAME_SETTINGS_REPLY = 0x436D
    PES6_GAME_SETTINGS_UPDATE = 0x436E
    PES6_TEAM_SELECT = 0x4373
    PES6_TEAM_SELECT_REPLY = 0x4374
    PES6_GOAL = 0x4375
    PES6_GOAL_REPLY = 0x4376
    PES6_STATE = 0x4377
    PES6_STATE_REPLY = 0x4378
    PES6_CLOCK = 0x4385
    PES6_CLOCK_REPLY = 0x4386
    PES6_BACK_TO_MENU = 0x4383
    PES6_BACK_TO_MENU_REPLY = 0x4384


@dataclass
class TeamSelection:
    """PES6 side assignment with captains, additional players, and team IDs."""
    home_captain: Profile | None = None
    away_captain: Profile | None = None
    home_more: list[Profile] = field(default_factory=list)
    away_more: list[Profile] = field(default_factory=list)
    home_team_id: int | None = None
    away_team_id: int | None = None

    def side(self, profile_id: int) -> int:
        """Return 0 for home, 1 for away, or 0xff for an unassigned profile."""
        if (self.home_captain and self.home_captain.id == profile_id
                or any(player.id == profile_id for player in self.home_more)):
            return HOME_SIDE
        if (self.away_captain and self.away_captain.id == profile_id
                or any(player.id == profile_id for player in self.away_more)):
            return 1
        return 0xff

    def players(self) -> tuple[tuple[Profile, ...], tuple[Profile, ...]]:
        """Return home and away player tuples, requiring both captains."""
        if self.home_captain is None or self.away_captain is None:
            raise ValueError("Both team captains are required")
        return ((self.home_captain, *self.home_more), (self.away_captain, *self.away_more))


@dataclass
class Match6:
    """PES6 match state with per-period goal buckets and finish bookkeeping."""
    state: int = 0
    clock: int = 0
    home_goals: list[int] = field(default_factory=lambda: [0] * 5)
    away_goals: list[int] = field(default_factory=lambda: [0] * 5)
    started: datetime | None = None
    recorded: bool = False

    @property
    def score_home(self) -> int:
        """Return the home score summed across all periods."""
        return sum(self.home_goals)

    @property
    def score_away(self) -> int:
        """Return the away score summed across all periods."""
        return sum(self.away_goals)


class MatchContext(RoomContext, Protocol):
    """Connection state required by match handlers."""
    repository: UserRepository
    settings: dict[str, Any]


def rating_points(stats: ProfileStats) -> int:
    """Calculate profile points with the original 0.44 performance and 0.56 games weights.

    Performance is the non-losing percentage with a draw worth one third of a
    win. The score is ``0.56 + 0.44 * performance**2 - 0.56 *
    exp(-games * 0.05)``, scaled by 1000 and truncated to an integer.
    """
    games = stats.wins + stats.losses + stats.draws
    performance = (stats.wins + DRAW_VALUE * stats.draws) / games if games else 0.0
    score = RATING_GAMES_WEIGHT + RATING_PERFORMANCE_WEIGHT * performance**2
    score -= RATING_GAMES_WEIGHT * math.exp(-games * GAMES_DECAY)
    return int(RATING_SCALE * score)


class MatchService:
    """Persist finished matches and update play time and rating points."""
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def record(self, match: MatchRecord, profiles: tuple[Profile, ...], started: datetime) -> None:
        """Store one result, then update each participant's play time and points."""
        await self.repository.store_match(match)
        duration = datetime.now() - started
        LOGGER.info(
            "Match recorded: home=%s away=%s score=%d:%d duration=%s",
            ",".join(str(value) for value in match.home_players),
            ",".join(str(value) for value in match.away_players),
            match.score_home, match.score_away, duration,
        )
        for profile in profiles:
            profile.play_time += duration
            profile.points = rating_points(await self.repository.profile_stats(profile.id))
            await self.repository.save_profile(profile)
            LOGGER.debug("Profile updated after match: profile=%s points=%d", profile.name, profile.points)


def current_match(ctx: MatchContext) -> tuple[Lobby | None, Room | None]:
    """Resolve the caller's current lobby and room."""
    if ctx.lobby_id is None:
        return None, None
    lobby = ctx.lobbies[ctx.lobby_id]
    room = next((item for item in lobby.rooms.values() if item.id == ctx.room_id), None)
    return lobby, room


async def pes5_disconnect(ctx: MatchContext) -> None:
    """Apply PES5 disconnect-loss policy and count the disconnect.

    The source increments the profile's disconnect count once both team IDs
    are known. When ``Disconnects.CountAsLoss.Enabled`` is true it writes the
    configured score for the disconnecting player; otherwise the abandoned
    match is removed so it cannot be recorded later.
    """
    lobby, room = current_match(ctx)
    if lobby is None or room is None or ctx.selected_profile is None:
        return
    async with lobby.lock:
        match = room.pes5_match
        if match is None:
            return
        counted = match.home_team_id is not None and match.away_team_id is not None
        settings = ctx.settings.get("Disconnects", {})
        loss = settings.get("CountAsLoss", {}) if isinstance(settings, dict) else {}
        if loss.get("Enabled", False):
            score = loss.get("Score", {})
            player_score = int(score.get("player", 0))
            opponent_score = int(score.get("opponent", 3))
            if match.home_profile and match.home_profile.id == ctx.selected_profile.id:
                match.score_home, match.score_away = player_score, opponent_score
            else:
                match.score_home, match.score_away = opponent_score, player_score
        else:
            room.pes5_match = None
    if counted:
        ctx.selected_profile.disconnects += 1
        await ctx.repository.save_profile(ctx.selected_profile)
        LOGGER.warning(
            "PES5 match disconnect: profile=%s room=%s score=%d:%d count_as_loss=%s",
            ctx.selected_profile.name, room.name.decode("utf-8", "replace"),
            match.score_home, match.score_away, loss.get("Enabled", False),
        )


async def _notify_room(lobby: Lobby, room: Room, formats: WireFormats) -> None:
    async with lobby.lock:
        update = formats.room_update(room)
        recipients = tuple(lobby.players.values())
    for recipient in recipients:
        await recipient.reply(RoomPacketId.UPDATE, update)


async def _decode_team_selection(context: MatchContext, request: bytes) -> TeamSelection:
    if len(request) < PES6_TEAM_SLOTS * PES6_TEAM_SLOT_SIZE:
        raise ValueError("Incomplete PES6 player settings")
    selection = TeamSelection()
    settings = PlayerSettingsRecord.from_packed(request[:PES6_TEAM_SLOTS * PES6_TEAM_SLOT_SIZE])
    for index, slot in enumerate(settings.players):
        profile_id = slot.profile_id
        if profile_id == 0:
            continue
        profile = await context.repository.profile_by_id(profile_id)
        if profile is None:
            raise ValueError("Player settings contain unknown profile")
        away = slot.away == 1
        if index < 2:
            if away:
                selection.away_captain = profile
            else:
                selection.home_captain = profile
        elif away:
            selection.away_more.append(profile)
        else:
            selection.home_more.append(profile)
    return selection


@packet_handler(MatchPacketId.PES6_PLAYER_SETTINGS, games=SIX_GAMES, role=ServiceRole.MAIN)
async def pes6_player_settings(ctx: PacketContext, request: object) -> None:
    """Decode PES6 team slots, broadcast them, and update the room."""
    assert isinstance(request, bytes)
    context: MatchContext = ctx  # type: ignore[assignment]
    lobby, room = current_match(context)
    if lobby is None or room is None:
        raise ValueError("Player settings outside room")
    selection = await _decode_team_selection(context, request)
    async with lobby.lock:
        room.team_selection = selection
        peers = tuple(room.players)
    await ctx.reply(MatchPacketId.PES6_PLAYER_SETTINGS_REPLY, bytes(STATUS_SIZE))
    for peer in peers:
        await peer.reply(MatchPacketId.PES6_PLAYER_SETTINGS_UPDATE, b"\0" + request)
    await _notify_room(lobby, room, active_formats(context))


@packet_handler(MatchPacketId.PES6_GAME_SETTINGS, games=SIX_GAMES, role=ServiceRole.MAIN)
async def pes6_game_settings(ctx: PacketContext, request: object) -> None:
    """Store PES6 match settings and relay them to the room."""
    assert isinstance(request, bytes)
    context: MatchContext = ctx  # type: ignore[assignment]
    if len(request) < 12:
        raise ValueError("Incomplete PES6 game settings")
    lobby, room = current_match(context)
    if lobby is None or room is None:
        raise ValueError("Game settings outside room")
    async with lobby.lock:
        room.match_settings = request
        peers = tuple(room.players)
    await ctx.reply(MatchPacketId.PES6_GAME_SETTINGS_REPLY, bytes(STATUS_SIZE))
    for peer in peers:
        await peer.reply(MatchPacketId.PES6_GAME_SETTINGS_UPDATE, request)
    await _notify_room(lobby, room, active_formats(context))


@packet_handler(MatchPacketId.PES6_TEAM_SELECT, games=SIX_GAMES, role=ServiceRole.MAIN, request_type=TeamSelectRequest)
async def pes6_team_selected(ctx: MatchContext, request: TeamSelectRequest) -> None:
    """Apply a PES6 captain's selected team and refresh room state."""
    context = ctx
    if context.selected_profile is None:
        raise ValueError("Invalid PES6 team selection")
    lobby, room = current_match(context)
    if lobby is None or room is None or room.team_selection is None:
        raise ValueError("PES6 team selection has no side assignment")
    team = request.team_id
    async with lobby.lock:
        selection = room.team_selection
        if selection.home_captain and context.selected_profile.id == selection.home_captain.id:
            selection.home_team_id = team
        elif selection.away_captain and context.selected_profile.id == selection.away_captain.id:
            selection.away_team_id = team
    await ctx.reply(MatchPacketId.PES6_TEAM_SELECT_REPLY, bytes(STATUS_SIZE))
    await _notify_room(lobby, room, active_formats(context))


def _pes6_team_details(room: Room) -> tuple[int | None, str, int | None, str]:
    """Describe only currently selected teams/players, tolerating incomplete state."""
    selection = room.team_selection
    if selection is None:
        return None, "", None, ""
    home = ([selection.home_captain] if selection.home_captain else []) + selection.home_more
    away = ([selection.away_captain] if selection.away_captain else []) + selection.away_more
    return (selection.home_team_id, ",".join(p.name for p in home),
            selection.away_team_id, ",".join(p.name for p in away))


def _pes5_team_details(match: PES5Match) -> tuple[int | None, str, int | None, str]:
    return (match.home_team_id, match.home_profile.name if match.home_profile else "",
            match.away_team_id, match.away_profile.name if match.away_profile else "")


def _log_goal(details: tuple[int | None, str, int | None, str], home_score: int, away_score: int, side: int) -> None:
    """Restore upstream goal and score messages using actual available participants."""
    home_team, home_names, away_team, away_names = details
    scoring = (home_team, home_names) if side == HOME_SIDE else (away_team, away_names)
    LOGGER.info("GOAL SCORED by %s team %s (%s)", "HOME" if side == HOME_SIDE else "AWAY", *scoring)
    LOGGER.info("UPDATE: Team %s (%s) vs Team %s (%s) - %d:%d (in progress)",
                home_team, home_names, away_team, away_names, home_score, away_score)


@packet_handler(MatchPacketId.PES6_GOAL, games=SIX_GAMES, role=ServiceRole.MAIN)
async def pes6_goal(ctx: PacketContext, request: object) -> None:
    """Increment the correct PES6 period goal bucket for the scoring side."""
    assert isinstance(request, bytes)
    context: MatchContext = ctx  # type: ignore[assignment]
    if not request:
        raise ValueError("Goal side is missing")
    lobby, room = current_match(context)
    if lobby is not None and room is not None:
        async with lobby.lock:
            match = room.match6
            if match is not None and match.state in PES6_GOAL_STATES:
                goals = match.home_goals if request[0] == HOME_SIDE else match.away_goals
                goals[PES6_GOAL_STATES[match.state]] += 1
                _log_goal(_pes6_team_details(room), match.score_home, match.score_away, request[0])
            elif match is None:
                LOGGER.error("ERROR: Goal reported, but no match in the room.")
    await ctx.reply(MatchPacketId.PES6_GOAL_REPLY, bytes(STATUS_SIZE))
    if lobby is not None and room is not None:
        await _notify_room(lobby, room, active_formats(context))


@packet_handler(MatchPacketId.PES6_CLOCK, games=SIX_GAMES, role=ServiceRole.MAIN)
async def pes6_match_clock(ctx: PacketContext, request: object) -> None:
    """Store the current PES6 match clock and notify the room."""
    assert isinstance(request, bytes)
    context: MatchContext = ctx  # type: ignore[assignment]
    if not request:
        raise ValueError("Match clock value is missing")
    lobby, room = current_match(context)
    if lobby is not None and room is not None:
        async with lobby.lock:
            if room.match6 is not None:
                room.match6.clock = request[0]
                LOGGER.info("CLOCK: Team %s (%s) vs Team %s (%s). Minute: %d",
                            *_pes6_team_details(room), room.match6.clock)
            else:
                LOGGER.error("ERROR: got clock update, but no match")
    await ctx.reply(MatchPacketId.PES6_CLOCK_REPLY, bytes(STATUS_SIZE))
    if lobby is not None and room is not None:
        await _notify_room(lobby, room, active_formats(context))


PES6_FINISHED_PHASE = 8


def _advance_pes6_state(room: Room, state: int) -> tuple[MatchRecord, tuple[Profile, ...], datetime] | None:
    if state == PES6_FIRST_HALF:
        room.match6 = Match6(state=state, started=datetime.now())
        LOGGER.info("NEW MATCH started: Team %s (%s) vs Team %s (%s)", *_pes6_team_details(room))
        return None
    if room.match6 is None:
        return None
    room.match6.state = state
    if state != PES6_FINISHED or room.match6.recorded:
        return None
    room.phase = PES6_FINISHED_PHASE
    room.match6.recorded = True
    selection = room.team_selection
    if selection is None:
        return None
    home, away = selection.players()
    if (selection.home_team_id is None or selection.away_team_id is None
            or room.match6.started is None):
        raise ValueError("Finished match lacks team or start data")
    LOGGER.info("MATCH FINISHED: Team %s (%s) - Team %s (%s)  %d:%d. Match time: %s.",
                *_pes6_team_details(room), room.match6.score_home, room.match6.score_away,
                datetime.now() - room.match6.started)
    return (
        MatchRecord(
            tuple(player.id for player in home), tuple(player.id for player in away),
            room.match6.score_home, room.match6.score_away,
            selection.home_team_id, selection.away_team_id,
        ),
        (*home, *away), room.match6.started,
    )


async def _record_pes6_result(
    context: MatchContext, lobby: Lobby, room: Room,
    record: tuple[MatchRecord, tuple[Profile, ...], datetime],
) -> None:
    if lobby.type_code == NO_STATS_LOBBY_CODE:
        return
    match, players, started = record
    try:
        await MatchService(context.repository).record(match, players, started)
    except BaseException:
        async with lobby.lock:
            if room.match6 is not None:
                room.match6.recorded = False
        raise


@packet_handler(MatchPacketId.PES6_STATE, games=SIX_GAMES, role=ServiceRole.MAIN)
async def pes6_match_state(ctx: PacketContext, request: object) -> None:
    """Advance PES6 match state and record a finished result once."""
    assert isinstance(request, bytes)
    context: MatchContext = ctx  # type: ignore[assignment]
    if not request:
        raise ValueError("Match state is missing")
    lobby, room = current_match(context)
    record = None
    if lobby is not None and room is not None and room.team_selection is not None:
        async with lobby.lock:
            record = _advance_pes6_state(room, request[0])
    if record is not None and lobby is not None and room is not None:
        await _record_pes6_result(context, lobby, room, record)
    if lobby is not None and room is not None:
        await _notify_room(lobby, room, active_formats(context))
    await ctx.reply(MatchPacketId.PES6_STATE_REPLY, bytes(STATUS_SIZE))


@packet_handler(MatchPacketId.PES6_BACK_TO_MENU, games=SIX_GAMES, role=ServiceRole.MAIN)
async def pes6_back_to_menu(ctx: PacketContext, request: object) -> None:
    """Return the PES6 post-match points/rating payload."""
    context: MatchContext = ctx  # type: ignore[assignment]
    _, room = current_match(context)
    if room is None:
        raise ValueError("Match menu request outside room")
    players = tuple(room.participating_players)
    if len(players) > 4:
        raise ValueError("Too many match participants")
    entries = b"".join(PostMatchPlayerRecord(player.selected_profile.id, bytes(18)).pack()
                       for player in players if player.selected_profile is not None)
    payload = bytes(4) + entries + bytes(PES6_POINTS_ENTRY_SIZE * (4 - len(players))) + bytes(20)
    await ctx.reply(MatchPacketId.PES6_BACK_TO_MENU_REPLY, payload)


@packet_handler(MatchPacketId.PES5_GOAL, games=ALL_GAMES, role=ServiceRole.MAIN)
async def pes5_goal(ctx: PacketContext, request: object) -> None:
    """Increment the PES5 home or away score."""
    assert isinstance(request, bytes)
    context: MatchContext = ctx  # type: ignore[assignment]
    if not request:
        raise ValueError("Goal side is missing")
    lobby, room = current_match(context)
    if lobby is None or room is None or room.pes5_match is None:
        raise ValueError("Goal outside a match")
    async with lobby.lock:
        if request[0] == HOME_SIDE:
            room.pes5_match.score_home += 1
        else:
            room.pes5_match.score_away += 1
        match = room.pes5_match
        _log_goal(_pes5_team_details(match), match.score_home, match.score_away, request[0])
    await ctx.reply(MatchPacketId.PES5_GOAL_REPLY, bytes(STATUS_SIZE))


@packet_handler(MatchPacketId.PES5_EXIT, games=ALL_GAMES, role=ServiceRole.MAIN)
async def pes5_match_exit(ctx: PacketContext, request: object) -> None:
    """Record a PES5 player's exit flag for later result policy."""
    assert isinstance(request, bytes)
    context: MatchContext = ctx  # type: ignore[assignment]
    if len(request) < 2:
        raise ValueError("Match exit flags are missing")
    lobby, room = current_match(context)
    if lobby is not None and room is not None and room.pes5_match is not None:
        async with lobby.lock:
            if request[0] == HOME_SIDE:
                room.pes5_match.home_exit = request[1]
            else:
                room.pes5_match.away_exit = request[1]
    await ctx.reply(MatchPacketId.PES5_EXIT_REPLY, bytes(STATUS_SIZE))


def _should_record_pes5(match: PES5Match, settings: dict[str, Any]) -> bool:
    disconnects = settings.get("Disconnects", {})
    count_as_loss = (
        bool(disconnects.get("CountAsLoss", {}).get("Enabled", False))
        if isinstance(disconnects, dict) else False
    )
    if match.home_exit == 1 and match.away_exit == 1:
        return False
    return count_as_loss or (match.home_exit is None and match.away_exit is None)


async def _record_pes5_match(context: MatchContext, match: PES5Match) -> None:
    if (match.home_profile is None or match.away_profile is None
            or match.home_team_id is None or match.away_team_id is None
            or match.start_datetime is None):
        raise ValueError("Completed match lacks player or team data")
    await MatchService(context.repository).record(
        MatchRecord(
            (match.home_profile.id,), (match.away_profile.id,),
            match.score_home, match.score_away, match.home_team_id, match.away_team_id,
        ),
        (match.home_profile, match.away_profile),
        match.start_datetime,
    )


@packet_handler(MatchPacketId.FINISH_SERIES, games=ALL_GAMES, role=(ServiceRole.LOGIN, ServiceRole.NETWORK_MENU, ServiceRole.MAIN))
async def finish_pes5_match(ctx: PacketContext, request: object) -> None:
    """Apply PES5 finish-series and disconnect rules, recording eligible results once.

    Packet ``0x3087`` is also sent when a player leaves a match series. The
    match is detached before persistence; mutual exits are disregarded, while
    configured disconnect scores or a clean finish are stored.
    """
    context: MatchContext = ctx  # type: ignore[assignment]
    lobby, room = current_match(context)
    if lobby is None or room is None:
        return
    async with lobby.lock:
        match, room.pes5_match = room.pes5_match, None
    if match is None or match.start_datetime is None:
        return
    if match.home_exit == 1 and match.away_exit == 1:
        LOGGER.info("MUTUAL DISCONNECT: match disregarded. Room: %s", room.name.decode("utf-8", "replace"))
        return
    if not _should_record_pes5(match, context.settings) or lobby.type_code == NO_STATS_LOBBY_CODE:
        return
    LOGGER.info("MATCH FINISHED: Team %s (%s) - Team %s (%s)  %d:%d. Match time: %s.",
                *_pes5_team_details(match),
                match.score_home, match.score_away, datetime.now() - match.start_datetime)
    await _record_pes5_match(context, match)


@packet_handler(MatchPacketId.FINISH_SERIES, games=SIX_GAMES, role=ServiceRole.MAIN, override=True)
async def ignore_pes6_finish_series(ctx: PacketContext, request: object) -> None:
    """Intentionally ignore ``0x3087`` on the PES6 main service.

    The original PES6 main service overrides the PES5 match-finalization
    behavior with a no-op; PES6 results are recorded from match-state updates.
    """
    return
