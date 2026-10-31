"""Pre-refactor wire fingerprints, legacy evidence and independent formats."""

import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from typing import Annotated

from Crypto.Cipher import Blowfish
import dataclasses_struct as dcs
import pytest

from fiveserver.database import Profile, ProfileStats, User
from fiveserver.games.definitions import make_registry
from fiveserver.games.formats import PES5_FORMATS, PES6_FORMATS, PROTOCOL_FORMATS
from fiveserver.handlers.chat import broadcast_system
from fiveserver.handlers.lobbies import LobbySelection, configured_lobbies, leave_lobby, player_info
from fiveserver.handlers.profile_info import format_profile_info
from fiveserver.handlers.rooms import Room, RoomPacketId
from fiveserver.protocol.codecs import CIPHER_KEY
from fiveserver.protocol.dispatcher import ALL_GAMES, SIX_GAMES, GameId, ServiceRole
from fiveserver.protocol.formats import active_formats
from fiveserver.protocol.packets import RoomCreateRequest
from fiveserver.protocol.records import Pes5LoginProfile, Pes6LoginProfile, Pes6PlayerRecord, Pes6ProfileRecord
from fiveserver.protocol.wire import Packet, PacketHeader, decode_packet
from fiveserver.server import Connection

from test_async_server import FakeUsers
from test_lobby_presence import Context


def packet(packet_id, payload=b""):
    return Packet(PacketHeader(packet_id, len(payload), 1), payload)


def sample_profile():
    return Profile(0, 1, 42, "Synthetic", rank=3, rating=99, points=450,
                   disconnects=2, fav_player=11, fav_team=10, comment="Hello")


class Repository:
    def __init__(self):
        self.stats_calls = 0
        self.games_calls = 0
        self.profile = sample_profile()

    async def profile_by_id(self, profile_id):
        return self.profile if profile_id == 42 else None

    async def profile_stats(self, profile_id):
        self.stats_calls += 1
        return ProfileStats(4, 3, 2, 12, 8, 1, 5, (100, 200))

    async def games_played(self, profile_id):
        self.games_calls += 1
        return 9


@pytest.mark.parametrize("tag,game,formats", [("five", GameId.PES5, PES5_FORMATS), ("six", GameId.PES6, PES6_FORMATS)])
def test_captured_pre_refactor_outputs_are_byte_identical(tag, game, formats):
    # Full-payload SHA-256 fingerprints captured by executing the pre-refactor
    # functions saved before editing. All input is synthetic; no account data.
    expected = json.loads((Path(__file__).parent / "fixtures/wire-formats.json").read_text())

    def check(key, payload):
        assert [len(payload), hashlib.sha256(payload).hexdigest()] == expected[key]

    async def scenario():
        profile = sample_profile()
        repository = Repository()
        stats = await repository.profile_stats(42)
        ctx = Context(1, [])
        ctx.game = game
        ctx.repository = repository
        ctx.user = User(1, "synthetic", "unused", [profile])
        ctx.selected_profile = profile
        ctx.room_id, ctx.no_lobby_chat = 7, 1
        for show in (False, True):
            check(f"profile_{tag}_{show}", format_profile_info(profile, stats, encoder=formats.profile_info, show_stats=show))
        check(f"player_{tag}", player_info(ctx, stats, encoder=formats.player_info))
        room = Room(7, b"Room", ctx, locked=True, players=[ctx], match_time=10)
        check(f"room_{tag}_False", formats.room_list(room))
        check(f"room_{tag}_True", formats.room_update(room))
        ctx.role = ServiceRole.LOGIN
        await make_registry(ALL_GAMES).dispatch(ctx, packet(0x3010))
        check(f"login_{tag}", ctx.replies[-1][1])
        ctx.settings.update(ServerName="Synthetic", NetworkServer={
            "loginService": {"pes5": 1001, "pes6": 1001}, "mainService": 1002, "networkMenuService": 1003,
        })
        ctx.server_ip, ctx.online_count, ctx.role = "192.0.2.1", 4, ServiceRole.NEWS
        ctx.replies.clear()
        await make_registry(ALL_GAMES).dispatch(ctx, packet(0x2005))
        assert [pid for pid, _ in ctx.replies] == [0x2002, 0x2003, 0x2004]
        check(f"discovery_{tag}", ctx.replies[1][1])
        check(f"chat_{tag}", formats.chat_prefix(b"\x01\x08", b"\x12\x34\x56\x78", profile) + b"hello\0\0")

    asyncio.run(scenario())


def test_pes6_lobby_size_is_grounded_in_original_format_player_info():
    # Git HEAD:lib/fiveserver/protocol/pes6.py, MainService.formatPlayerInfo:
    # id/name/groupid/groupname/member/division/room/points/rating/games/
    # wins/losses/draws/pad. This is independent of the new DCS definition.
    profile, stats = sample_profile(), ProfileStats(4, 3, 2)
    original = struct.pack("!i48si48sBBiiHHHHH3s", 42, b"Synthetic", 0, bytes(48),
                           0, 2, 7, 450, 0, 9, 4, 3, 2, bytes(3))
    assert struct.calcsize("!i48si48sBBiiHHHHH3s") == 127
    assert PES6_FORMATS.player_info(profile, stats, 7, 1) == original
    assert original[52:104] == bytes(52)  # Both group fields are really present.


@pytest.mark.parametrize("game", list(GameId))
@pytest.mark.parametrize("role", [ServiceRole.LOGIN, ServiceRole.NETWORK_MENU, ServiceRole.MAIN])
def test_role_specific_inheritance_is_preserved(game, role):
    ctx = SimpleNamespace(game=game, role=role)
    formats = active_formats(ctx)
    profile, stats = sample_profile(), ProfileStats()
    assert len(formats.profile_info(profile, stats)) == (418 if game in SIX_GAMES and role == ServiceRole.MAIN else 57)
    assert len(formats.player_info(profile, stats, 0, 0)) == (127 if game in SIX_GAMES and role == ServiceRole.MAIN else 31)


def test_hidden_stats_missing_profiles_and_default_comments_keep_original_semantics():
    async def scenario():
        repository = Repository()
        ctx = Context(1, [])
        ctx.game, ctx.repository = GameId.PES6, repository
        ctx.settings["ShowStats"] = False
        registry = make_registry(ALL_GAMES)
        await registry.dispatch(ctx, packet(0x4102, struct.pack("!i", 42)))
        result = Pes6ProfileRecord.from_packed(ctx.replies[-1][1][4:])
        assert (result.profile_id, result.points, result.rating, result.rank, result.games) == (42, 0, 0, 0, 0)
        assert result.comment.startswith(b"Fiveserver rules!\0")
        assert repository.stats_calls == 0
        await registry.dispatch(ctx, packet(0x4102, struct.pack("!i", 999)))
        assert ctx.replies[-1] == (0x4103, b"")
        assert repository.stats_calls == 0
        assert repository.profile.comment == "Hello" and repository.profile.points == 450

    asyncio.run(scenario())


@pytest.mark.parametrize("game,role,record_type", [
    (GameId.PES5, ServiceRole.LOGIN, Pes5LoginProfile),
    (GameId.PES6, ServiceRole.LOGIN, Pes6LoginProfile),
    (GameId.PES6, ServiceRole.MAIN, Pes5LoginProfile),
    (GameId.PES6, ServiceRole.NETWORK_MENU, Pes5LoginProfile),
])
def test_hidden_login_rows_keep_identity_but_hide_time_rating_points_and_games(game, role, record_type):
    async def scenario():
        ctx = Context(1, [])
        ctx.game, ctx.role, ctx.repository = game, role, Repository()
        profile = sample_profile()
        profile.play_time = timedelta(seconds=12345)
        ctx.user.profiles = [profile]
        ctx.settings["ShowStats"] = False
        await make_registry(ALL_GAMES).dispatch(ctx, packet(0x3010))
        row = record_type.from_packed(ctx.replies[-1][1][4:])
        assert row.profile_id == 42 and row.name.startswith(b"Synthetic\0")
        assert (row.state.seconds_played, row.state.points, row.state.division, row.games) == (0, 0, 0, 0)
        assert getattr(row, "rating", 0) == 0
        assert ctx.repository.games_calls == 0
        assert profile.play_time.total_seconds() == 12345 and profile.rating == 99

    asyncio.run(scenario())


def test_hidden_lobby_statistics_preserve_legacy_points_visibility():
    async def scenario():
        ctx = Context(1, configured_lobbies({"Lobbies": ["England"]}))
        ctx.game, ctx.repository, ctx.selected_profile = GameId.PES6, Repository(), sample_profile()
        ctx.settings["ShowStats"] = False
        selection = LobbySelection(0, bytes(16), 1234, bytes(16), 1235, 0).pack()
        await make_registry(ALL_GAMES).dispatch(ctx, packet(0x4202, selection))
        record = Pes6PlayerRecord.from_packed(ctx.replies[-1][1])
        assert (record.profile_id, record.points, record.games, record.wins, record.losses, record.draws) == (42, 450, 0, 0, 0, 0)
        assert ctx.repository.stats_calls == 0

    asyncio.run(scenario())


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ThirdProfileRecord:
    """Test-only, deliberately incompatible 12-byte profile format."""
    marker: Annotated[bytes, 3]
    identity: dcs.I32
    points: dcs.I32
    games: dcs.U8


def third_profile(profile, stats):
    return ThirdProfileRecord(b"NEW", profile.id, profile.points, stats.wins + stats.draws + stats.losses).pack()


def definitions_with_third(formats):
    # Test-only protocol definition for an existing identity: no fake production
    # game and no edits to shared handlers or either established encoder.
    return {**PROTOCOL_FORMATS, GameId.WE9: dict.fromkeys(ServiceRole, formats)}


def test_independent_encoders_and_decoder_work_in_the_actual_shared_handlers():
    def create(payload):
        assert payload.startswith(b"CRE3")
        return RoomCreateRequest(payload[4:], False, None)

    formats = replace(PES5_FORMATS,
                      profile_info=third_profile,
                      login_profile=lambda i, p, state, rating, games: b"L3" + struct.pack("!i", state.points),
                      player_info=lambda p, s, room, chat: b"P3" + struct.pack("!i", room),
                      room_list=lambda room: b"RL3" + struct.pack("!i", room.id),
                      room_update=lambda room: b"RU3" + room.name,
                      room_create=create,
                      discovery=lambda settings, ip, game, count: b"DS3",
                      chat_prefix=lambda kind, special, p: b"CH3" + kind + special)
    registry = make_registry(ALL_GAMES, formats=definitions_with_third(formats))

    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        ctx = Context(1, lobbies)
        ctx.game, ctx.lobby_id, ctx.repository = GameId.WE9, 0, Repository()
        ctx.selected_profile = sample_profile()
        ctx.user.profiles = [ctx.selected_profile]
        lobbies[0].players[ctx.user.hash] = ctx
        await registry.dispatch(ctx, packet(0x4102, struct.pack("!i", 42)))
        assert ctx.replies[-1] == (0x4103, bytes(4) + b"NEW" + struct.pack("!iiB", 42, 450, 9))
        await registry.dispatch(ctx, packet(0x3010))
        assert ctx.replies[-1][1] == bytes(4) + b"L3" + struct.pack("!i", 450)
        await registry.dispatch(ctx, packet(0x4310, b"CRE3ThirdRoom"))
        assert ctx.replies[-3:] == [(0x4306, b"RU3ThirdRoom"), (0x4222, b"P3" + struct.pack("!i", 1)), (0x4311, bytes(4))]
        await registry.dispatch(ctx, packet(0x4300))
        assert ctx.replies[-3:] == [(0x4301, bytes(4)), (0x4302, b"RL3" + struct.pack("!i", 1)), (0x4303, bytes(4))]
        special = b"\x12\x34\x56\x78"
        await registry.dispatch(ctx, packet(0x4400, b"\0\1" + special + bytes(4) + b"hello\0"))
        assert ctx.replies[-1] == (0x4402, b"CH3\0\1" + special + b"hello\0\0")
        ctx.role, ctx.server_ip, ctx.online_count = ServiceRole.NEWS, "192.0.2.1", 1
        await registry.dispatch(ctx, packet(0x2005))
        assert ctx.replies[-3:] == [(0x2002, bytes(4)), (0x2003, b"DS3"), (0x2004, bytes(4))]

    asyncio.run(scenario())
    with pytest.raises(TypeError):
        registry.protocol_formats[GameId.WE9][ServiceRole.MAIN] = PES5_FORMATS


def test_authentication_recovery_updates_connection_formats_immediately():
    class Writer:
        def __init__(self):
            self.frames = []

        def write(self, data):
            self.frames.append(data)

        async def drain(self):
            pass

    class Users(FakeUsers, Repository):
        async def profile_by_id(self, profile_id):
            return sample_profile()

        async def profile_stats(self, profile_id):
            return ProfileStats(wins=9)

    async def scenario():
        credential = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(bytes(48))
        repo = Users(credential[32:48].hex())
        sessions, info = {}, {}
        formats = replace(PES5_FORMATS, profile_info=third_profile)
        registry = make_registry(ALL_GAMES, formats=definitions_with_third(formats))
        settings = {"Roster": {"enforceHash": False}, "ShowStats": True}
        login = Connection(asyncio.StreamReader(), Writer(), GameId.WE9, ServiceRole.LOGIN,
                           settings, "127.0.0.1", repo, sessions, user_info=info)
        await registry.dispatch(login, packet(0x3003, credential))
        sessions.clear()
        shared = Connection(asyncio.StreamReader(), Writer(), GameId.PES5, ServiceRole.MAIN,
                            settings, "127.0.0.1", repo, sessions, user_info=info)
        assert shared.formats is PES5_FORMATS
        await registry.dispatch(shared, packet(0x3003, credential))
        assert shared.game == GameId.WE9
        assert shared.formats is formats  # No stale listener-selected cache.
        await registry.dispatch(shared, packet(0x4102, struct.pack("!i", 42)))
        response = decode_packet(shared.writer.frames[-1])
        assert response.payload == bytes(4) + b"NEW" + struct.pack("!iiB", 42, 450, 9)

    asyncio.run(scenario())


def test_system_chat_keeps_pes6_layout_even_on_inherited_menu_connections():
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        ctx = Context(1, lobbies)
        ctx.game, ctx.role = GameId.PES6, ServiceRole.NETWORK_MENU
        lobbies[0].players[ctx.user.hash] = ctx
        await broadcast_system(lobbies[0], "message")
        assert ctx.replies[-1][1][:2] == b"\0\1"
        assert len(ctx.replies[-1][1]) == 58 + len(b"message") + 2

    asyncio.run(scenario())


@pytest.mark.parametrize("game,role,room_size,player_size,owner_notice,participation_notice", [
    (GameId.PES5, ServiceRole.MAIN, 87, 31, True, False),
    (GameId.PES6, ServiceRole.MAIN, 131, 127, False, True),
    (GameId.PES6, ServiceRole.NETWORK_MENU, 131, 127, False, True),
])
def test_disconnect_keeps_behavior_and_cleanup_formats_separate(
    game, role, room_size, player_size, owner_notice, participation_notice,
):
    async def scenario():
        lobbies = configured_lobbies({"Lobbies": ["England"]})
        owner, guest = Context(1, lobbies), Context(2, lobbies)
        for peer in (owner, guest):
            peer.game, peer.role, peer.lobby_id, peer.room_id = game, role, 0, 7
            peer.repository = Repository()
            lobbies[0].players[peer.user.hash] = peer
        room = Room(7, b"Room", owner, players=[owner, guest], participating_players=[owner, guest])
        lobbies[0].rooms[room.name] = room
        await leave_lobby(guest, disconnected=True)
        assert guest.room_id == 0 and guest.lobby_id is None
        assert guest.user.hash not in lobbies[0].players
        assert room.players == [owner]
        packets = dict(owner.replies)
        assert len(packets[RoomPacketId.UPDATE]) == room_size
        assert len(packets[RoomPacketId.PLAYER_UPDATE]) == player_size
        assert (RoomPacketId.PLAYER_LEFT_ROOM in packets) == owner_notice
        assert (RoomPacketId.PARTICIPATION_UPDATE in packets) == participation_notice
        assert RoomPacketId.EXIT_REPLY not in packets

    asyncio.run(scenario())
