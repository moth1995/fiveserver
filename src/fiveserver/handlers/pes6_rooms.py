"""PES6 room participation, peer exchange, and match-start handlers."""


import logging
import struct
from datetime import datetime, timedelta

from ..database import ProfileStats
from ..protocol.dispatcher import SIX_GAMES, PacketContext, ServiceRole, packet_handler
from ..protocol.records import KickoffRecord, Pes6RoomRenameHeader, ReadyUpdateRecord
from ..protocol.formats import active_formats
from ..protocol.packets import (
    PES6_ROOM_PASSWORD_OFFSET, PES6_ROOM_PASSWORD_SIZE,
    ProfileIdRequest, decode_room_join,
)
from .lobby_state import Lobby, LobbyPeer, player_info
from .room_codecs import (
    DEFAULT_ROOM_PHASE, PES6_MAX_ROOM_PLAYERS, _participation, _peer_info,
    _profile_id, participation_status,
)
from .rooms import (
    FORCED_CANCEL_SECONDS, FORCED_CANCEL_STATUS, MATCH_SERIES_ENDING_PHASE,
    PARTICIPATION_ROSTER_ERROR, PREGAME_FIRST_PHASE, PREGAME_LAST_PHASE,
    ROOM_JOIN_ERROR, ROOM_PASSWORD_ERROR, ROOM_STATUS_SIZE, Room, RoomContext,
    RoomPacketId, _current_room,
)


LOGGER = logging.getLogger(__name__)
def _join_pes6_room(
    context: RoomContext, lobby: Lobby, room_id: int, password: bytes
) -> tuple[Room | None, bytes, tuple[RoomContext, ...], tuple[LobbyPeer, ...]]:
    room = next((candidate for candidate in lobby.rooms.values() if candidate.id == room_id), None)
    if room is None:
        return None, ROOM_JOIN_ERROR, (), ()
    if room.locked and password != room.password:
        return room, ROOM_PASSWORD_ERROR, (), ()
    if len(room.players) >= PES6_MAX_ROOM_PLAYERS:
        return room, ROOM_JOIN_ERROR, (), ()
    others = tuple(room.players)
    room.players.append(context)
    context.room_id = room.id
    return room, b"", others, tuple(lobby.players.values())


async def _send_pes6_peer_lists(context: RoomContext, room: Room, others: tuple[RoomContext, ...]) -> None:
    for peer in others:
        await peer.reply(RoomPacketId.PEER_ENTERED, _peer_info(context, room, incoming=True))
    await context.reply(RoomPacketId.PEER_LIST_START)
    for peer in others:
        await context.reply(RoomPacketId.PEER_LIST_ENTRY, _peer_info(peer, room, incoming=False))
    await context.reply(RoomPacketId.PEER_LIST_END)


@packet_handler(RoomPacketId.JOIN_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN, override=True)
async def join_room_pes6(ctx: PacketContext, request: object) -> None:
    """Join a PES6 room, enforce password/capacity, and exchange peer endpoints."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.lobby_id is None or context.selected_profile is None:
        raise ValueError("Room joining requires lobby and profile")
    join = decode_room_join(request)
    lobby = context.lobbies[context.lobby_id]
    async with lobby.lock:
        room, error, others, peers = _join_pes6_room(context, lobby, join.room_id, join.password)
    if error:
        LOGGER.warning(
            "PES6 room join rejected: room_id=%d player=%s error=%s",
            join.room_id, context.selected_profile.name, error.hex(),
        )
        await ctx.reply(RoomPacketId.JOIN_REPLY, error)
        return
    assert room is not None
    LOGGER.info(
        "PES6 room joined: room=%s lobby=%s player=%s players=%d",
        room.name.decode("utf-8", "replace"), lobby.name,
        context.selected_profile.name, len(room.players),
    )
    update = active_formats(context).room_update(room)
    show_stats = bool(context.settings.get("ShowStats", True))
    stats = await context.repository.profile_stats(context.selected_profile.id) if show_stats else ProfileStats()
    player = player_info(context, stats, encoder=active_formats(context).player_info)
    for peer in peers:
        await peer.reply(RoomPacketId.UPDATE, update)
        await peer.reply(RoomPacketId.PLAYER_UPDATE, player)
    match_time = room.match_settings[:1] if room.match_settings else b""
    await ctx.reply(RoomPacketId.JOIN_REPLY, bytes(ROOM_STATUS_SIZE) + match_time)
    await _send_pes6_peer_lists(context, room, others)


@packet_handler(RoomPacketId.OWNER_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN, request_type=ProfileIdRequest)
async def set_owner_pes6(ctx: RoomContext, request: ProfileIdRequest) -> None:
    """Transfer room ownership to a PES6 profile and publish the update."""
    context = ctx
    lobby, room = _current_room(context)
    if room is not None:
        update = b""
        target_id = request.profile_id
        async with lobby.lock:
            target = next((peer for peer in room.players if peer.selected_profile and peer.selected_profile.id == target_id), None)
            if target is not None:
                room.owner = target
                peers = tuple(lobby.players.values())
                update = active_formats(context).room_update(room)
                LOGGER.info(
                    "PES6 room owner changed: room=%s owner=%s",
                    room.name.decode("utf-8", "replace"),
                    target.selected_profile.name if target.selected_profile else "",
                )
            else:
                LOGGER.warning("PES6 room owner change failed: room_id=%d profile_id=%d", room.id, target_id)
                peers = ()
        for peer in peers:
            await peer.reply(RoomPacketId.UPDATE, update)
    await ctx.reply(RoomPacketId.OWNER_REPLY, bytes(ROOM_STATUS_SIZE))


@packet_handler(RoomPacketId.RENAME_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN)
async def rename_room_pes6(ctx: PacketContext, request: object) -> None:
    """Rename a PES6 room, update its password, and reject duplicate names."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if len(request) < 65:
        raise ValueError("Room rename packet is too short")
    lobby, room = _current_room(context)
    status = bytes(ROOM_STATUS_SIZE)
    if room is not None:
        header = Pes6RoomRenameHeader.from_packed(request[:PES6_ROOM_PASSWORD_OFFSET])
        name = header.name.split(b"\0", 1)[0]
        async with lobby.lock:
            if name != room.name and name in lobby.rooms:
                status = b"\xff" * ROOM_STATUS_SIZE
            else:
                if name != room.name:
                    del lobby.rooms[room.name]
                    room.name = name
                    lobby.rooms[name] = room
                room.locked = header.locked == 1
                room.password = (
                    request[PES6_ROOM_PASSWORD_OFFSET:PES6_ROOM_PASSWORD_OFFSET + PES6_ROOM_PASSWORD_SIZE]
                    .split(b"\0", 1)[0] if room.locked else None
                )
            peers = tuple(lobby.players.values())
            update = active_formats(context).room_update(room)
        for peer in peers:
            await peer.reply(RoomPacketId.UPDATE, update)
        if status == bytes(ROOM_STATUS_SIZE):
            LOGGER.info(
                "PES6 room renamed: room=%s locked=%s",
                room.name.decode("utf-8", "replace"), room.locked,
            )
        else:
            LOGGER.warning("PES6 room rename rejected: room=%s", name.decode("utf-8", "replace"))
    await ctx.reply(RoomPacketId.RENAME_REPLY, status)


async def _send_stun_lists(
    context: RoomContext, room: Room, members: tuple[RoomContext, ...]
) -> None:
    incoming = _peer_info(context, room, incoming=True)
    for member in members:
        await context.reply(RoomPacketId.PEER_LIST_ENTRY, _peer_info(member, room, incoming=False))
        for other in members:
            if other is not context:
                await other.reply(RoomPacketId.PEER_ENTERED, incoming)


@packet_handler(RoomPacketId.STUN_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN)
async def get_stun_info_pes6(ctx: PacketContext, request: object) -> None:
    """Send PES6 room participant endpoints to the requester and other members."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.lobby_id is None or len(request) < 4:
        raise ValueError("Invalid peer info request")
    lobby = context.lobbies[context.lobby_id]
    room_id = struct.unpack_from("!i", request)[0]
    async with lobby.lock:
        room = next((item for item in lobby.rooms.values() if item.id == room_id), None)
        members = tuple(room.players) if room is not None else ()
    await ctx.reply(RoomPacketId.PEER_LIST_START)
    if room is not None:
        await _send_stun_lists(context, room, members)
    await ctx.reply(RoomPacketId.PEER_LIST_END)


@packet_handler(RoomPacketId.SPECTATOR_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN, override=True)
async def become_spectator_pes6(ctx: PacketContext, request: object) -> None:
    """Mark the caller as a PES6 spectator."""
    context: RoomContext = ctx  # type: ignore[assignment]
    context.spectator = True
    await ctx.reply(RoomPacketId.SPECTATOR_REPLY, bytes(ROOM_STATUS_SIZE))


@packet_handler(RoomPacketId.SPECTATOR_PEERS, games=SIX_GAMES, role=ServiceRole.MAIN)
async def send_spectator_peers_pes6(ctx: PacketContext, request: object) -> None:
    """Relay host connection data to non-participating room spectators."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if context.lobby_id is not None:
        lobby, room = _current_room(context)
        if room is not None:
            async with lobby.lock:
                spectators = tuple(peer for peer in room.players if peer not in room.participating_players)
            for peer in spectators:
                await peer.reply(RoomPacketId.SPECTATOR_PEERS, request)
    await ctx.reply(RoomPacketId.SPECTATOR_PEERS_REPLY, bytes(ROOM_STATUS_SIZE))


@packet_handler(RoomPacketId.QUICK_SEARCH_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN)
async def quick_search_pes6(ctx: PacketContext, request: object) -> None:
    """Return the original empty PES6 quick-search reply."""
    await ctx.reply(RoomPacketId.QUICK_SEARCH_REPLY)


def _roster_mismatch(context: RoomContext, lobby: Lobby, room: Room) -> bool:
    if not room.participating_players:
        return False
    host = room.participating_players[0]
    roster_config = context.settings.get("Roster", {})
    compare = isinstance(roster_config, dict) and bool(roster_config.get("compareHash", False))
    return bool(
        compare and lobby.check_roster_hash and host.user and context.user
        and host.user.roster_hash != context.user.roster_hash
    )


def _set_participation(context: RoomContext, lobby: Lobby, room: Room, requested: bool) -> bytes:
    if not requested:
        if context in room.participating_players:
            room.participating_players.remove(context)
        return bytes(ROOM_STATUS_SIZE)
    if _roster_mismatch(context, lobby, room):
        return PARTICIPATION_ROSTER_ERROR
    if context.cancelled_until is not None and datetime.now() < context.cancelled_until:
        return FORCED_CANCEL_STATUS
    if context not in room.participating_players:
        room.participating_players.append(context)
    return bytes(ROOM_STATUS_SIZE)


@packet_handler(RoomPacketId.PARTICIPATE_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN)
async def toggle_participation_pes6(ctx: PacketContext, request: object) -> None:
    """Apply PES6 roster/forced-cancel policy and broadcast participation."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if not request:
        raise ValueError("Participation value is missing")
    requested = request[0] == 1
    lobby, room = _current_room(context)
    status = bytes(ROOM_STATUS_SIZE)
    participants: tuple[RoomContext, ...] = ()
    encoded, position = b"", 0xff
    if room is not None:
        async with lobby.lock:
            status = _set_participation(context, lobby, room, requested)
            position = _participation(room, context)
            encoded = participation_status(room)
            participants = tuple(room.players)
    for peer in participants:
        await peer.reply(RoomPacketId.PARTICIPATION_UPDATE, encoded)
    await ctx.reply(RoomPacketId.PARTICIPATE_REPLY, status + bytes((int(requested), position)))


@packet_handler(RoomPacketId.FORCE_CANCEL_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN, request_type=ProfileIdRequest)
async def force_cancel_pes6(ctx: RoomContext, request: ProfileIdRequest) -> None:
    """Cancel a profile's participation for the original ten-second window."""
    context = ctx
    profile_id = request.profile_id
    lobby, room = _current_room(context)
    if room is not None:
        async with lobby.lock:
            target = next((peer for peer in room.players if peer.selected_profile and peer.selected_profile.id == profile_id), None)
            if target is not None:
                if target in room.participating_players:
                    room.participating_players.remove(target)
                target.cancelled_until = datetime.now() + timedelta(seconds=FORCED_CANCEL_SECONDS)
                LOGGER.info(
                    "PES6 participation cancelled: room=%s profile=%s seconds=%d",
                    room.name.decode("utf-8", "replace"), profile_id, FORCED_CANCEL_SECONDS,
                )
            status = participation_status(room)
            peers = tuple(room.players)
        for peer in peers:
            await peer.reply(RoomPacketId.PARTICIPATION_UPDATE, status)
    await ctx.reply(RoomPacketId.FORCE_CANCEL_REPLY, bytes(ROOM_STATUS_SIZE))


@packet_handler(RoomPacketId.MATCH_START_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN, override=True)
async def start_match_pes6(ctx: PacketContext, request: object) -> None:
    """Start the PES6 pregame phase and send the participant kickoff list."""
    context: RoomContext = ctx  # type: ignore[assignment]
    lobby, room = _current_room(context)
    if room is not None:
        async with lobby.lock:
            participants = tuple(room.participating_players)
            peers = tuple(room.players)
            room.phase = PREGAME_FIRST_PHASE
            room.match_starter = context
            room.ready_count = 0
            update = active_formats(context).room_update(room)
        LOGGER.info(
            "PES6 match start: room=%s lobby=%s participants=%d",
            room.name.decode("utf-8", "replace"), lobby.name, len(participants),
        )
        kickoff = KickoffRecord(2, b"".join(struct.pack("!i", _profile_id(peer)) for peer in participants)).pack()
        for peer in peers:
            await peer.reply(RoomPacketId.MATCH_START_UPDATE, kickoff)
        async with lobby.lock:
            recipients = tuple(lobby.players.values())
        for recipient in recipients:
            await recipient.reply(RoomPacketId.UPDATE, update)
    await ctx.reply(RoomPacketId.MATCH_START_REPLY, bytes(ROOM_STATUS_SIZE))


def _apply_postmatch_ready(context: RoomContext, room: Room, state: int) -> None:
    if state == 0:
        if context in room.participating_players:
            room.participating_players.remove(context)
        room.phase = DEFAULT_ROOM_PHASE if not room.participating_players else MATCH_SERIES_ENDING_PHASE
        room.match6 = None
    elif state == 3:
        room.phase, room.match6 = 4, None
    elif state == 4:
        room.phase, room.match6 = 6, None


def _apply_ready_state(context: RoomContext, room: Room, state: int) -> bool:
    if PREGAME_FIRST_PHASE <= room.phase <= PREGAME_LAST_PHASE:
        room.ready_count += 1 if state == 1 else -1 if state == 0 else 0
    elif room.phase > PREGAME_LAST_PHASE:
        _apply_postmatch_ready(context, room, state)
    advance = (PREGAME_FIRST_PHASE <= room.phase <= PREGAME_LAST_PHASE
               and room.ready_count == len(room.participating_players))
    if advance:
        room.phase += 1
        room.ready_count = 0
    return advance


@packet_handler(RoomPacketId.READY_REQUEST, games=SIX_GAMES, role=ServiceRole.MAIN)
async def toggle_ready_pes6(ctx: PacketContext, request: object) -> None:
    """Advance PES6 ready phases or reset a finished match series."""
    assert isinstance(request, bytes)
    context: RoomContext = ctx  # type: ignore[assignment]
    if not request or context.selected_profile is None:
        raise ValueError("Invalid ready packet")
    lobby, room = _current_room(context)
    if room is not None:
        state = request[0]
        async with lobby.lock:
            advance = _apply_ready_state(context, room, state)
            peers = tuple(room.players)
            update = active_formats(context).room_update(room)
            lobby_peers = tuple(lobby.players.values())
        if advance:
            LOGGER.info(
                "PES6 room phase advanced: room=%s phase=%d",
                room.name.decode("utf-8", "replace"), room.phase,
            )
        for peer in peers:
            if peer is not context:
                await peer.reply(RoomPacketId.READY_UPDATE, ReadyUpdateRecord(context.selected_profile.id, state).pack())
    await ctx.reply(RoomPacketId.READY_REPLY, bytes(ROOM_STATUS_SIZE))
    if room is not None:
        if advance:
            for peer in peers:
                await peer.reply(RoomPacketId.PHASE_UPDATE, bytes((room.phase,)))
        for recipient in lobby_peers:
            await recipient.reply(RoomPacketId.UPDATE, update)
