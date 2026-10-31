import asyncio
import hashlib
import struct
import zlib
from datetime import datetime

import pytest
from Crypto.Cipher import Blowfish

from fiveserver.database import Profile, User
from fiveserver.protocol.dispatcher import GameDefinition, GameId, PacketRegistry, ServiceRole
from fiveserver.handlers.login import AUTH_BAD_ROSTER, AuthenticationService
from fiveserver.protocol.codecs import CIPHER_KEY, decode_pes5_auth, decode_pes6_auth
from fiveserver.handlers.news import news
from fiveserver.games.definitions import make_registry
from fiveserver.server import FiveServer
from fiveserver.protocol.wire import InvalidPacket, Packet, PacketHeader, decode_packet, encode_packet, read_packet


def test_wire_matches_source_derived_bytes():
    header = struct.pack("!HHI", 0x3001, 3, 7)
    clear = header + hashlib.md5(header + b"abc").digest() + b"abc"
    key = bytes.fromhex("a677957c")
    expected = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(clear))
    assert encode_packet(0x3001, b"abc", 7) == expected
    assert decode_packet(expected).payload == b"abc"
    with pytest.raises(InvalidPacket):
        decode_packet(expected[:-1] + bytes([expected[-1] ^ 1]))


def test_registry_rejects_duplicates_and_missing_overrides():
    with pytest.raises(ValueError, match="Duplicate"):
        PacketRegistry((GameDefinition(GameId.PES5, (news, news)),))
    with pytest.raises(ValueError, match="Override has no base"):
        from fiveserver.handlers.login import authenticate_pes6

        PacketRegistry((GameDefinition(GameId.PES6, (), (authenticate_pes6,)),))


def test_unknown_packets_follow_original_default_handlers():
    class Context:
        game = GameId.PES5

        def __init__(self, role):
            self.role = role
            self.replies = []

        async def reply(self, packet_id, payload=b""):
            self.replies.append((packet_id, payload))

    async def scenario():
        registry = make_registry((GameId.PES5,))
        news = Context(ServiceRole.NEWS)
        await registry.dispatch(news, Packet(PacketHeader(0x2FFF, 0, 1), b""))
        assert news.replies == []
        for role in (ServiceRole.LOGIN, ServiceRole.NETWORK_MENU, ServiceRole.MAIN):
            context = Context(role)
            await registry.dispatch(context, Packet(PacketHeader(0x2FFF, 0, 1), b""))
            assert context.replies == [(0x3000, bytes(4))]

    asyncio.run(scenario())


def test_auth_codecs_keep_raw_user_hash_and_optional_roster():
    cipher = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB)
    for size in (48, 64):
        payload = cipher.encrypt(bytes(range(size)))
        assert decode_pes5_auth(payload).user_hash == payload[32:48].hex()
        assert decode_pes5_auth(payload).roster_hash == (bytes(range(size))[48:64] if size == 64 else None)
        assert decode_pes6_auth(payload).roster_hash is None
    with pytest.raises(ValueError):
        decode_pes5_auth(bytes(56))
    with pytest.raises(ValueError):
        decode_pes6_auth(bytes(80))


def test_roster_enforcement_matches_original_empty_and_zero_heuristics():
    class Repository:
        def __init__(self, expected_hash):
            self.expected_hash = expected_hash

        async def by_hash(self, value):
            if value == self.expected_hash:
                return User(1, "test", value, [])
            return None

    async def scenario():
        cipher = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB)
        short = cipher.encrypt(bytes(48))
        request = decode_pes5_auth(short)
        connection = object()
        sessions = {}
        service = AuthenticationService(Repository(request.user_hash), sessions)
        status, user = await service.authenticate(request, connection, True)
        assert status == 0 and user is not None
        assert sessions[request.user_hash] is connection
        status, _ = await service.authenticate(request, object(), False)
        assert status == 0xFFFFFF11

        zero_roster = cipher.encrypt(bytes(64))
        request = decode_pes5_auth(zero_roster)
        service = AuthenticationService(Repository(request.user_hash), {})
        status, _ = await service.authenticate(request, object(), True)
        assert status == AUTH_BAD_ROSTER
        assert request.user_hash not in service.sessions

    asyncio.run(scenario())


def test_concurrent_authentication_claims_only_one_session():
    class Repository:
        async def by_hash(self, value):
            await asyncio.sleep(0.01)
            return User(1, "test", value, [])

    async def scenario():
        request = decode_pes5_auth(Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(bytes(48)))
        service = AuthenticationService(Repository(), {}, asyncio.Lock())
        first, second = await asyncio.gather(
            service.authenticate(request, object(), False),
            service.authenticate(request, object(), False),
        )
        assert sorted(status for status, _ in (first, second)) == [0, 0xFFFFFF11]

    asyncio.run(scenario())


def test_4110_is_favourite_team_for_pes5_and_comment_for_pes6():
    class Repository:
        async def save_profile(self, profile):
            self.saved = profile

    class Context:
        role = ServiceRole.MAIN

        def __init__(self, game):
            self.game = game
            self.selected_profile = Profile(0, 1, 2)
            self.repository = Repository()
            self.replies = []

        async def reply(self, packet_id, payload=b""):
            self.replies.append((packet_id, payload))

    async def scenario():
        registry = make_registry((GameId.PES5, GameId.PES6))
        pes5 = Context(GameId.PES5)
        await registry.dispatch(pes5, Packet(PacketHeader(0x4110, 2, 1), b"\x00\x19"))
        assert pes5.selected_profile.fav_team == 25
        assert pes5.replies == [(0x4112, bytes(4))]
        pes6 = Context(GameId.PES6)
        await registry.dispatch(pes6, Packet(PacketHeader(0x4110, 2, 1), b"hi"))
        assert pes6.selected_profile.comment == b"hi"
        assert pes6.replies == [(0x4111, bytes(4))]

    asyncio.run(scenario())


def test_match_results_pes5_record_and_pes6_login_override():
    class Repository:
        async def last_matches(self, profile_id):
            return [(9, 2, 1, 10, 20, datetime(2026, 1, 2, 3, 4, 5))]

        async def profile_name(self, profile_id):
            return "Opponent"

    class Context:
        def __init__(self, game):
            self.game = game
            self.role = ServiceRole.LOGIN
            self.user = User(1, "test", "hash", [Profile(0, 1, 7)])
            self.repository = Repository()
            self.selected_profile = None
            self.replies = []

        async def reply(self, packet_id, payload=b""):
            self.replies.append((packet_id, payload))

    async def scenario():
        registry = make_registry((GameId.PES5, GameId.PES6))
        request = Packet(PacketHeader(0x3070, 4, 1), struct.pack("!i", 7))
        pes5 = Context(GameId.PES5)
        await registry.dispatch(pes5, request)
        expected = bytes(4) + struct.pack("!B19s16siBBHH", 0, b"2026/01/02 03:04:05", b"Opponent", 9, 2, 1, 10, 20)
        assert pes5.replies == [(0x3072, expected)]
        pes6 = Context(GameId.PES6)
        await registry.dispatch(pes6, request)
        assert pes6.replies == [(0x3071, bytes(4)), (0x3073, bytes(4))]

    asyncio.run(scenario())


def test_settings_packets_keep_source_storage_and_response_order():
    class Repository:
        async def profile_settings(self, profile_id):
            return zlib.compress(b"first"), zlib.compress(b"second")

        async def store_profile_settings(self, profile):
            self.saved = profile.settings2

    class Context:
        game = GameId.PES5
        role = ServiceRole.LOGIN

        def __init__(self):
            self.settings = {"StoreSettings": True}
            self.selected_profile = Profile(0, 1, 7)
            self.repository = Repository()
            self.replies = []

        async def reply(self, packet_id, payload=b""):
            self.replies.append((packet_id, payload))

    async def scenario():
        registry = make_registry((GameId.PES5,))
        ctx = Context()
        await registry.dispatch(ctx, Packet(PacketHeader(0x308A, 0, 1), b""))
        assert ctx.replies == [
            (0x3087, bytes(4) + struct.pack("!I", 7)),
            (0x3088, b"first"),
            (0x3088, b"second"),
            (0x3089, b""),
        ]
        await registry.dispatch(ctx, Packet(PacketHeader(0x3088, 3, 2), b"\x00\x00\x03"))
        await registry.dispatch(ctx, Packet(PacketHeader(0x3089, 0, 3), b""))
        assert zlib.decompress(ctx.repository.saved) == b"\x00\x00\x03"
        assert ctx.replies[-1] == (0x308B, bytes(4))

    asyncio.run(scenario())


class FakeUsers:
    def __init__(self, expected_hash: str):
        self.expected_hash = expected_hash

    async def by_hash(self, value: str):
        if value == self.expected_hash:
            return User(1, "test", value, [Profile(index, 1) for index in range(3)])
        return None

    async def games_played(self, profile_id: int):
        return 0


def test_real_async_listener_handles_fragmentation_heartbeat_and_login():
    async def scenario():
        encrypted = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(bytes(48))
        settings = {
            "ListenOn": "127.0.0.1",
            "ServerName": "Test",
            "Lobbies": ["England", {"name": "Training", "type": "noStats"}],
            "GamePorts": {"pes5": 0},
            "NetworkServer": {"networkMenuService": 0, "mainService": 0, "loginService": {"pes5": 0}},
            "Roster": {"enforceHash": False},
            "ShowStats": False,
        }
        server = FiveServer(settings, (GameId.PES5,), "127.0.0.1", FakeUsers(encrypted[32:48].hex()))
        await server.start()
        try:
            news_port = server.listeners[0].sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", news_port)
            wire = encode_packet(0x2005, b"\0", 1)
            writer.write(wire[:5])
            await writer.drain()
            writer.write(wire[5:])
            await writer.drain()
            assert [ (await read_packet(reader)).header.packet_id for _ in range(3) ] == [0x2002, 0x2003, 0x2004]
            writer.write(encode_packet(0x0005, b"ping", 2))
            await writer.drain()
            beat = await read_packet(reader)
            assert (beat.header.packet_id, beat.payload, beat.header.counter) == (0x0005, b"ping", 4)
            writer.close()
            await writer.wait_closed()

            menu_port = server.listeners[1].sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", menu_port)
            writer.write(encode_packet(0x4580, b"", 1))
            await writer.drain()
            assert [(await read_packet(reader)).header.packet_id for _ in range(2)] == [0x4581, 0x4583]
            writer.write(encode_packet(0x3003, encrypted, 2))
            await writer.drain()
            assert (await read_packet(reader)).payload == bytes(4)
            writer.write(encode_packet(0x4200, b"\x01", 3))
            await writer.drain()
            listing = await read_packet(reader)
            assert listing.header.packet_id == 0x4201
            assert listing.payload[:2] == struct.pack("!H", 2)
            assert listing.payload[2:37] == struct.pack("!B32sH", 0x5F, b"England", 0)
            assert listing.payload[37:72] == struct.pack("!B32sH", 0x20, b"Training", 0)
            writer.close()
            await writer.wait_closed()
            for _ in range(10):
                if not server.sessions:
                    break
                await asyncio.sleep(0.01)

            login_port = server.listeners[-1].sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", login_port)
            writer.write(encode_packet(0x3001, b"", 1) + encode_packet(0x3003, encrypted, 2))
            await writer.drain()
            assert (await read_packet(reader)).header.packet_id == 0x3002
            result = await read_packet(reader)
            assert (result.header.packet_id, result.payload) == (0x3004, bytes(4))
            writer.write(encode_packet(0x3010, b"", 3))
            await writer.drain()
            profiles = await read_packet(reader)
            assert profiles.header.packet_id == 0x3012
            assert len(profiles.payload) == 4 + 3 * (1 + 4 + 16 + 4 + 1 + 4 + 2)
            writer.write(encode_packet(0x3003, encrypted, 4))
            await writer.drain()
            repeated = await read_packet(reader)
            assert repeated.payload == struct.pack("!I", 0xFFFFFF11)

            second_reader, second_writer = await asyncio.open_connection("127.0.0.1", login_port)
            second_writer.write(encode_packet(0x3003, encrypted, 1))
            await second_writer.drain()
            assert (await read_packet(second_reader)).payload == struct.pack("!I", 0xFFFFFF11)
            second_writer.close()
            await second_writer.wait_closed()
            writer.close()
            await writer.wait_closed()
            for _ in range(10):
                if not server.sessions:
                    break
                await asyncio.sleep(0.01)
            assert not server.sessions
            reconnect_reader, reconnect_writer = await asyncio.open_connection("127.0.0.1", login_port)
            reconnect_writer.write(encode_packet(0x3003, encrypted, 1))
            await reconnect_writer.drain()
            assert (await read_packet(reconnect_reader)).payload == bytes(4)
            reconnect_writer.close()
            await reconnect_writer.wait_closed()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_server_close_disconnects_active_clients_without_deadlock():
    async def scenario():
        settings = {
            "ListenOn": "127.0.0.1",
            "ServerName": "Test",
            "Lobbies": ["England"],
            "GamePorts": {"pes5": 0},
            "NetworkServer": {"networkMenuService": 0, "mainService": 0, "loginService": {"pes5": 0}},
            "Roster": {"enforceHash": False},
            "ShowStats": False,
        }
        server = FiveServer(settings, (GameId.PES5,), "127.0.0.1")
        await server.start()
        port = server.listeners[0].sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(encode_packet(0x2005, b"\0", 1))
            await writer.drain()
            assert (await read_packet(reader)).header.packet_id == 0x2002
            # Python 3.12+ Server.wait_closed() also waits for active clients;
            # shutdown must disconnect them first instead of waiting here.
            await asyncio.wait_for(server.close(), timeout=3)
            try:
                while await asyncio.wait_for(reader.read(4096), timeout=2):
                    pass  # Discard queued server-list packets before EOF.
            except (ConnectionError, OSError):
                pass  # A reset also proves the socket was torn down.
        finally:
            writer.close()
            await server.close()

    asyncio.run(scenario())


