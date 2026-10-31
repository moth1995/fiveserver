import asyncio
import struct

from fiveserver.protocol.dispatcher import GameId
from fiveserver.handlers.lobbies import configured_lobbies, leave_lobby
from fiveserver.handlers.rooms import RoomPacketId
from fiveserver.handlers.room_codecs import encode_pes5_room, encode_pes6_room
from fiveserver.games.definitions import make_registry
from fiveserver.protocol.wire import Packet, PacketHeader

from test_lobby_presence import Context


def packet(packet_id, payload=b""):
    return Packet(PacketHeader(packet_id, len(payload), 1), payload)


def test_room_create_list_duplicate_and_cleanup():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        first, second = Context(1, lobbies), Context(2, lobbies)
        first.lobby_id = second.lobby_id = 0
        lobbies[0].players[first.user.hash] = first
        lobbies[0].players[second.user.hash] = second
        registry = make_registry((GameId.PES5,))
        name = b"test-room"
        request = packet(RoomPacketId.CREATE_REQUEST, name.ljust(32, b"\0") + b"\0\0")
        await registry.dispatch(first, request)
        room = lobbies[0].rooms[name]
        assert first.room_id == room.id
        assert len(encode_pes5_room(room)) == 87
        assert len(encode_pes5_room(room, update=True)) == 87
        assert first.replies[-1] == (RoomPacketId.CREATE_REPLY, bytes(4))
        assert [value for kind, value in second.replies if kind == RoomPacketId.UPDATE]

        await registry.dispatch(second, request)
        assert second.replies[-1] == (RoomPacketId.CREATE_REPLY, bytes.fromhex("ffffff10"))

        await registry.dispatch(second, packet(RoomPacketId.LIST_REQUEST))
        assert [kind for kind, _ in second.replies[-3:]] == [
            RoomPacketId.LIST_START, RoomPacketId.LIST_ENTRY, RoomPacketId.LIST_END,
        ]
        assert second.replies[-2][1] == encode_pes5_room(room)

        await registry.dispatch(first, packet(RoomPacketId.EXIT_REQUEST))
        assert first.room_id == 0
        assert name not in lobbies[0].rooms
        assert second.replies[-1] == (RoomPacketId.REMOVED, struct.pack("!i", room.id))
        await leave_lobby(first)

    asyncio.run(scenario())


def test_pes6_room_layout():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner = Context(1, lobbies)
        owner.game = GameId.PES6
        owner.lobby_id = 0
        lobbies[0].players[owner.user.hash] = owner
        request = packet(RoomPacketId.CREATE_REQUEST, b"six-room".ljust(64, b"\0") + bytes(16))
        await make_registry((GameId.PES6,)).dispatch(owner, request)
        room = lobbies[0].rooms[b"six-room"]
        assert len(encode_pes6_room(room)) == 131
        assert owner.replies[-1] == (RoomPacketId.CREATE_REPLY, bytes(4))
        await leave_lobby(owner)
        assert not lobbies[0].rooms

    asyncio.run(scenario())


def test_room_serializers_match_original_field_order():
    from fiveserver.handlers.rooms import Room

    owner = Context(1, [])
    room = Room(7, b"Fixture", owner, players=[owner])
    pes5_list = (
        struct.pack("!iBB", 7, 1, 0) + b"Fixture".ljust(32, b"\0") + b"\x01"
        + struct.pack("!i", owner.selected_profile.id) + bytes(44)
    )
    pes5_update = (
        struct.pack("!iBB", 7, 1, 0) + b"Fixture".ljust(32, b"\0") + b"\x01"
        + struct.pack("!i", owner.selected_profile.id) + bytes(7) + bytes(37)
    )
    pes6 = (
        struct.pack("!iBB", 7, 1, 0) + b"Fixture".ljust(64, b"\0") + b"\0"
        + struct.pack("!iBBBBBB", owner.selected_profile.id, 1, 0, 0xff, 0, 0, 0xff)
        + b"\0\0\0\0\0\0\xff\0\0\xff" * 3
        + b"\xff\xff" + bytes(5) + b"\xff\xff" + bytes(5)
        + b"\0\0\0\x02\0\0"
    )
    assert encode_pes5_room(room) == pes5_list
    assert encode_pes5_room(room, update=True) == pes5_update
    assert encode_pes6_room(room) == pes6


def test_unknown_command_uses_login_default_handler_outside_news():
    async def scenario():
        context = Context(1, [])
        await make_registry((GameId.PES5,)).dispatch(context, packet(0x7FFF, b"opaque"))
        assert context.replies == [(0x8000, bytes(4))]

    asyncio.run(scenario())


def test_pes5_challenge_accept_broadcasts_both_players_room_membership():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner, challenger = Context(1, lobbies), Context(2, lobbies)
        for peer in (owner, challenger):
            peer.lobby_id = 0
            lobbies[0].players[peer.user.hash] = peer
        registry = make_registry((GameId.PES5,))
        await registry.dispatch(owner, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(32, b"\0") + bytes(2)))
        room = lobbies[0].rooms[b"room"]
        await registry.dispatch(challenger, packet(
            RoomPacketId.JOIN_REQUEST, struct.pack("!i", room.id) + bytes(16) + b"\0"))
        assert owner.replies[-1][0] == RoomPacketId.CHALLENGE
        await registry.dispatch(owner, packet(RoomPacketId.CHALLENGE_RESPONSE, b"\x01"))
        for peer in (owner, challenger):
            updates = [payload for kind, payload in peer.replies if kind == RoomPacketId.PLAYER_UPDATE]
            assert len(updates[-2:]) == 2
            assert struct.unpack_from("!i", updates[-2])[0] == owner.selected_profile.id
            assert struct.unpack_from("!i", updates[-1])[0] == challenger.selected_profile.id

    asyncio.run(scenario())


def test_pes5_ready_and_challenge_defer_chat_history_until_room_list():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner, challenger = Context(1, lobbies), Context(2, lobbies)
        for peer in (owner, challenger):
            peer.lobby_id = 0
            lobbies[0].players[peer.user.hash] = peer
        registry = make_registry((GameId.PES5,))
        await registry.dispatch(owner, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(32, b"\0") + bytes(2)))
        room = lobbies[0].rooms[b"room"]
        await registry.dispatch(challenger, packet(
            RoomPacketId.JOIN_REQUEST, struct.pack("!i", room.id) + bytes(16) + b"\0"))
        await registry.dispatch(owner, packet(RoomPacketId.CHALLENGE_RESPONSE, b"\x01"))

        assert challenger.needs_lobby_chat_replay is True
        assert challenger.chat_history_schedules == 0

        for peer in (owner, challenger):
            await registry.dispatch(peer, packet(RoomPacketId.MATCH_START_REQUEST, b"\x01"))
        assert owner.needs_lobby_chat_replay is True
        assert challenger.needs_lobby_chat_replay is True
        assert owner.chat_history_schedules == challenger.chat_history_schedules == 0

        await registry.dispatch(challenger, packet(RoomPacketId.LIST_REQUEST))
        assert challenger.needs_lobby_chat_replay is False
        assert challenger.chat_history_schedules == 1

    asyncio.run(scenario())


def test_pes5_challenge_hides_points_when_show_stats_is_disabled():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner, challenger = Context(1, lobbies), Context(2, lobbies)
        challenger.settings["ShowStats"] = False
        challenger.selected_profile.points = 500
        for peer in (owner, challenger):
            peer.lobby_id = 0
            lobbies[0].players[peer.user.hash] = peer
        registry = make_registry((GameId.PES5,))
        await registry.dispatch(owner, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(32, b"\0") + bytes(2)))
        room = lobbies[0].rooms[b"room"]
        await registry.dispatch(challenger, packet(
            RoomPacketId.JOIN_REQUEST, struct.pack("!i", room.id) + bytes(16) + b"\0"))
        challenge = owner.replies[-1][1]
        assert struct.unpack_from("!i", challenge, 21)[0] == 0

    asyncio.run(scenario())


def test_pes5_cancel_challenge_notifies_the_current_owner():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner, challenger = Context(1, lobbies), Context(2, lobbies)
        for peer in (owner, challenger):
            peer.lobby_id = 0
            lobbies[0].players[peer.user.hash] = peer
        registry = make_registry((GameId.PES5,))
        await registry.dispatch(owner, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(32, b"\0") + bytes(2)))
        room = lobbies[0].rooms[b"room"]
        await registry.dispatch(challenger, packet(
            RoomPacketId.JOIN_REQUEST, struct.pack("!i", room.id) + bytes(16) + b"\0"))
        await registry.dispatch(owner, packet(RoomPacketId.CANCEL_CHALLENGE_REQUEST))
        assert challenger.replies[-1] == (RoomPacketId.CHALLENGE_CANCELLED, bytes(4))

    asyncio.run(scenario())


def test_pes6_room_join_and_peer_exchange():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner, joiner = Context(1, lobbies), Context(2, lobbies)
        for peer in (owner, joiner):
            peer.game = GameId.PES6
            peer.lobby_id = 0
            peer.ip1 = b"127.0.0.1"
            peer.ip2 = b"192.168.1.2"
            peer.udp_port1 = 6000
            peer.udp_port2 = 6001
            lobbies[0].players[peer.user.hash] = peer
        registry = make_registry((GameId.PES6,))
        await registry.dispatch(owner, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(64, b"\0") + bytes(16)))
        room = lobbies[0].rooms[b"room"]
        await registry.dispatch(joiner, packet(RoomPacketId.JOIN_REQUEST, struct.pack("!i", room.id)))
        assert joiner.room_id == room.id
        assert [kind for kind, _ in joiner.replies[-4:]] == [
            RoomPacketId.JOIN_REPLY, RoomPacketId.PEER_LIST_START,
            RoomPacketId.PEER_LIST_ENTRY, RoomPacketId.PEER_LIST_END,
        ]
        assert len(joiner.replies[-2][1]) == 75
        assert owner.replies[-1][0] == RoomPacketId.PEER_ENTERED
        await leave_lobby(joiner)
        assert len(room.players) == 1

    asyncio.run(scenario())


def test_pes6_participation_and_ready_phase():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner = Context(1, lobbies)
        owner.game = GameId.PES6
        owner.lobby_id = 0
        owner.ip1 = owner.ip2 = b"127.0.0.1"
        owner.udp_port1 = owner.udp_port2 = 6000
        lobbies[0].players[owner.user.hash] = owner
        registry = make_registry((GameId.PES6,))
        await registry.dispatch(owner, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(64, b"\0") + bytes(16)))
        room = lobbies[0].rooms[b"room"]
        await registry.dispatch(owner, packet(RoomPacketId.PARTICIPATE_REQUEST, b"\x01"))
        assert room.participating_players == [owner]
        assert owner.replies[-1] == (RoomPacketId.PARTICIPATE_REPLY, bytes(4) + b"\x01\x00")
        assert len([payload for kind, payload in owner.replies if kind == RoomPacketId.PARTICIPATION_UPDATE][-1]) == 24
        await registry.dispatch(owner, packet(RoomPacketId.MATCH_START_REQUEST))
        assert room.phase == 2
        assert len([payload for kind, payload in owner.replies if kind == RoomPacketId.MATCH_START_UPDATE][-1]) == 37
        await registry.dispatch(owner, packet(RoomPacketId.READY_REQUEST, b"\x01"))
        assert room.phase == 3
        assert owner.replies[-2] == (RoomPacketId.PHASE_UPDATE, b"\x03")

    asyncio.run(scenario())


def test_pes5_match_result_is_recorded_once():
    from fiveserver.handlers.matches import MatchPacketId
    from test_pes6_match import Repository

    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        home, away = Context(1, lobbies), Context(2, lobbies)
        repository = Repository((home, away))
        for peer in (home, away):
            peer.lobby_id = 0
            peer.repository = repository
            lobbies[0].players[peer.user.hash] = peer
        registry = make_registry((GameId.PES5,))
        await registry.dispatch(home, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(32, b"\0") + bytes(2)))
        room = lobbies[0].rooms[b"room"]
        room.players.append(away)
        away.room_id = room.id
        await registry.dispatch(home, packet(RoomPacketId.TEAM_SELECT_REQUEST, struct.pack("!H", 100)))
        await registry.dispatch(away, packet(RoomPacketId.TEAM_SELECT_REQUEST, struct.pack("!H", 200)))
        await registry.dispatch(home, packet(RoomPacketId.MATCH_START_REQUEST, b"\x01"))
        await registry.dispatch(away, packet(RoomPacketId.MATCH_START_REQUEST, b"\x01"))
        assert room.pes5_match.start_datetime is not None
        await registry.dispatch(home, packet(MatchPacketId.PES5_GOAL, b"\x00"))
        await registry.dispatch(home, packet(MatchPacketId.FINISH_SERIES))
        await registry.dispatch(away, packet(MatchPacketId.FINISH_SERIES))
        assert len(repository.matches) == 1
        assert repository.matches[0].score_home == 1

    asyncio.run(scenario())


def test_pes5_disconnect_applies_configured_loss_and_profile_count():
    from test_pes6_match import Repository

    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        home, away = Context(1, lobbies), Context(2, lobbies)
        repository = Repository((home, away))
        for peer in (home, away):
            peer.lobby_id = 0
            peer.repository = repository
            peer.settings = {"Disconnects": {"CountAsLoss": {"Enabled": True,
                                                               "Score": {"player": 0, "opponent": 3}}}}
            lobbies[0].players[peer.user.hash] = peer
        registry = make_registry((GameId.PES5,))
        await registry.dispatch(home, packet(RoomPacketId.CREATE_REQUEST, b"room".ljust(32, b"\0") + bytes(2)))
        room = lobbies[0].rooms[b"room"]
        room.players.append(away)
        away.room_id = room.id
        await registry.dispatch(home, packet(RoomPacketId.TEAM_SELECT_REQUEST, struct.pack("!H", 100)))
        await registry.dispatch(away, packet(RoomPacketId.TEAM_SELECT_REQUEST, struct.pack("!H", 200)))
        await leave_lobby(home, disconnected=True)
        assert home.selected_profile.disconnects == 1
        assert (room.pes5_match.score_home, room.pes5_match.score_away) == (0, 3)
        assert room.players == [away]

    asyncio.run(scenario())


