import asyncio
import struct

from fiveserver.database import ProfileStats
from fiveserver.protocol.dispatcher import GameId
from fiveserver.handlers.matches import MatchPacketId
from fiveserver.handlers.rooms import RoomPacketId
from fiveserver.handlers.room_codecs import encode_pes6_room
from fiveserver.handlers.lobbies import configured_lobbies
from fiveserver.games.definitions import make_registry
from fiveserver.protocol.wire import Packet, PacketHeader

from test_lobby_presence import Context


class Repository:
    def __init__(self, players):
        self.players = {player.selected_profile.id: player.selected_profile for player in players}
        self.matches = []

    async def profile_by_id(self, profile_id):
        return self.players.get(profile_id)

    async def profile_stats(self, profile_id):
        return ProfileStats(wins=1)

    async def store_match(self, match):
        self.matches.append(match)
        return len(self.matches)

    async def save_profile(self, profile):
        return profile


def packet(packet_id, payload=b""):
    return Packet(PacketHeader(packet_id, len(payload), 1), payload)


def test_pes6_match_flow_records_once_and_updates_room_wire():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        home, away = Context(1, lobbies), Context(2, lobbies)
        repository = Repository((home, away))
        for player in (home, away):
            player.game = GameId.PES6
            player.lobby_id = 0
            player.repository = repository
            lobbies[0].players[player.user.hash] = player
        registry = make_registry((GameId.PES6,))
        await registry.dispatch(home, packet(RoomPacketId.CREATE_REQUEST, b"match".ljust(64, b"\0") + bytes(16)))
        room = lobbies[0].rooms[b"match"]
        room.players.append(away)
        away.room_id = room.id
        await registry.dispatch(home, packet(
            MatchPacketId.PES6_PLAYER_SETTINGS,
            struct.pack("!iB3x", home.selected_profile.id, 0)
            + struct.pack("!iB3x", away.selected_profile.id, 1) + bytes(16),
        ))
        assert room.team_selection.home_captain.id == home.selected_profile.id
        assert room.team_selection.away_captain.id == away.selected_profile.id
        await registry.dispatch(home, packet(MatchPacketId.PES6_TEAM_SELECT, struct.pack("!H", 100)))
        await registry.dispatch(away, packet(MatchPacketId.PES6_TEAM_SELECT, struct.pack("!H", 200)))
        await registry.dispatch(home, packet(MatchPacketId.PES6_STATE, b"\x01"))
        await registry.dispatch(home, packet(MatchPacketId.PES6_GOAL, b"\x00"))
        assert room.match6.score_home == 1
        assert encode_pes6_room(room)[5] == 1
        await registry.dispatch(home, packet(MatchPacketId.PES6_STATE, b"\x0a"))
        await registry.dispatch(home, packet(MatchPacketId.PES6_STATE, b"\x0a"))
        assert room.phase == 8
        assert len(repository.matches) == 1
        assert repository.matches[0].score_home == 1
        assert repository.matches[0].team_home == 100
        assert repository.matches[0].team_away == 200
        await registry.dispatch(home, packet(RoomPacketId.READY_REQUEST, b"\x03"))
        assert (room.phase, room.match6) == (5, None)

    asyncio.run(scenario())


