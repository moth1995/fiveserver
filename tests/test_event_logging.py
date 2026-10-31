import asyncio
from datetime import datetime
import logging

from Crypto.Cipher import Blowfish

from fiveserver.database import MatchRecord, Profile, ProfileStats
from fiveserver.games.definitions import make_registry
from fiveserver.handlers.matches import MatchService
from fiveserver.protocol.codecs import CIPHER_KEY
from fiveserver.protocol.dispatcher import GameId, ServiceRole
from fiveserver.protocol.wire import Packet, PacketHeader

from test_async_server import FakeUsers
from test_lobby_presence import Context as LobbyContext


def packet(packet_id, payload=b""):
    return Packet(PacketHeader(packet_id, len(payload), 1), payload)


class LoginContext:
    def __init__(self, repository, credential):
        self.game = GameId.PES5
        self.role = ServiceRole.LOGIN
        self.repository = repository
        self.sessions = {}
        self.user_info = {}
        self.settings = {"Roster": {"enforceHash": False}}
        self.user = None
        self.selected_profile = None
        self.replies = []
        self.session_id = 4
        self.credential = credential

    async def reply(self, packet_id, payload=b""):
        self.replies.append((packet_id, payload))

    async def disconnect(self):
        self.closed = True


def test_auth_success_failure_and_disconnect_events(caplog):
    caplog.set_level(logging.INFO, logger="fiveserver.handlers.login")

    async def scenario():
        credential = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(bytes(48))
        repository = FakeUsers(credential[32:48].hex())
        registry = make_registry((GameId.PES5,))
        context = LoginContext(repository, credential)
        await registry.dispatch(context, packet(0x3003, credential))
        assert any("Login accepted" in record.getMessage() for record in caplog.records)
        await registry.dispatch(context, packet(0x0003))
        assert any("User disconnected" in record.getMessage() for record in caplog.records)

        unknown = LoginContext(FakeUsers("00" * 16), credential)
        caplog.clear()
        await registry.dispatch(unknown, packet(0x3003, credential))
        assert any("Login rejected" in record.getMessage() for record in caplog.records)

    asyncio.run(scenario())


def test_room_creation_and_match_persistence_events(caplog):
    caplog.set_level(logging.INFO, logger="fiveserver.handlers.rooms")
    caplog.set_level(logging.INFO, logger="fiveserver.handlers.matches")

    async def scenario():
        lobby = LobbyContext(1, [])
        lobby.lobby_id = 0
        lobby.lobbies = []
        from fiveserver.handlers.lobbies import configured_lobbies

        lobby.lobbies = configured_lobbies({"Lobbies": ["England"]})
        lobby.lobbies[0].players[lobby.user.hash] = lobby
        registry = make_registry((GameId.PES5,))
        await registry.dispatch(
            lobby, packet(0x4310, b"room".ljust(32, b"\0") + bytes(2)),
        )
        assert any("Room created" in record.getMessage() for record in caplog.records)

        class Repository:
            async def store_match(self, match):
                return 1

            async def profile_stats(self, profile_id):
                return ProfileStats(wins=1)

            async def save_profile(self, profile):
                return profile

        profile = Profile(0, 1, 2, "Player")
        await MatchService(Repository()).record(
            MatchRecord((2,), (3,), 1, 0, 10, 20), (profile,), datetime.now(),
        )
        assert any("Match recorded" in record.getMessage() for record in caplog.records)

    asyncio.run(scenario())
