import asyncio

from Crypto.Cipher import Blowfish

from fiveserver.protocol.dispatcher import GameId, ServiceRole
from fiveserver.protocol.codecs import CIPHER_KEY
from fiveserver.games.definitions import make_registry
from fiveserver.protocol.wire import Packet, PacketHeader

from test_async_server import FakeUsers


class Context:
    def __init__(self, game, role, repository, sessions, user_info):
        self.game = game
        self.role = role
        self.repository = repository
        self.sessions = sessions
        self.user_info = user_info
        self.settings = {"Roster": {"enforceHash": False}}
        self.user = None
        self.selected_profile = None
        self.replies = []

    async def reply(self, packet_id, payload=b""):
        self.replies.append((packet_id, payload))


def test_shared_menu_listener_recovers_game_variant_from_login():
    async def scenario():
        credential = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(bytes(48))
        repository = FakeUsers(credential[32:48].hex())
        sessions, user_info = {}, {}
        registry = make_registry((GameId.PES5, GameId.WE9))
        login = Context(GameId.WE9, ServiceRole.LOGIN, repository, sessions, user_info)
        packet = Packet(PacketHeader(0x3003, len(credential), 1), credential)
        await registry.dispatch(login, packet)
        assert login.replies == [(0x3004, bytes(4))]
        assert user_info["test"][0] == GameId.WE9
        sessions.clear()  # Login connection closes before the network menu connects.
        menu = Context(GameId.PES5, ServiceRole.NETWORK_MENU, repository, sessions, user_info)
        await registry.dispatch(menu, packet)
        assert menu.game == GameId.WE9
        assert menu.user.game_name == "we9"
        assert menu.replies == [(0x3004, bytes(4))]

    asyncio.run(scenario())


