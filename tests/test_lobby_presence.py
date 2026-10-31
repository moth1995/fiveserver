import asyncio
import struct

from fiveserver.database import Profile, ProfileStats, User
from fiveserver.protocol.dispatcher import GameId, ServiceRole
from fiveserver.handlers.lobbies import LobbySelection, configured_lobbies, leave_lobby
from fiveserver.games.definitions import make_registry
from fiveserver.protocol.wire import Packet, PacketHeader


class FakeRepository:
    async def profile_stats(self, profile_id):
        return ProfileStats(wins=1)


class Context:
    game = GameId.PES5
    role = ServiceRole.MAIN

    def __init__(self, user_id, lobbies):
        self.user = User(user_id, f"user{user_id}", f"hash{user_id}", [])
        self.selected_profile = Profile(0, user_id, user_id + 10, f"player{user_id}")
        self.lobbies = lobbies
        self.repository = FakeRepository()
        self.settings = {"ShowStats": True}
        self.lobby_id = None
        self.room_id = 0
        self.spectator = False
        self.cancelled_until = None
        self.no_lobby_chat = 0
        self.needs_lobby_chat_replay = False
        self.chat_history_schedules = 0
        self.replies = []

    async def reply(self, packet_id, payload=b""):
        self.replies.append((packet_id, payload))

    def schedule_chat_history(self, lobby, *, encoder=None):
        self.chat_history_schedules += 1


def test_lobby_entry_listing_and_disconnect_notifications():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        first, second = Context(1, lobbies), Context(2, lobbies)
        registry = make_registry((GameId.PES5,))
        selection = LobbySelection(0, bytes(16), 1234, bytes(16), 1235, 0).pack()
        packet = Packet(PacketHeader(0x4202, len(selection), 1), selection)
        await registry.dispatch(first, packet)
        await registry.dispatch(second, packet)
        assert lobbies[0].player_count == 2
        assert first.replies[0] == (0x4203, bytes(4))
        assert [packet_id for packet_id, _ in first.replies].count(0x4220) == 2

        await registry.dispatch(first, Packet(PacketHeader(0x4210, 0, 2), b""))
        assert [packet_id for packet_id, _ in first.replies[-4:]] == [0x4211, 0x4212, 0x4212, 0x4213]
        assert all(len(payload) == 31 for packet_id, payload in first.replies[-4:] if packet_id == 0x4212)

        await leave_lobby(second)
        assert lobbies[0].player_count == 1
        assert first.replies[-1] == (0x4221, struct.pack("!i", second.selected_profile.id))

    asyncio.run(scenario())


