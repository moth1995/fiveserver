"""Prove real enum/listener extension in an isolated source tree and process."""

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from fiveserver.protocol.dispatcher import GameId


ROOT = Path(__file__).resolve().parents[1]


def test_genuinely_new_game_identity_roles_listeners_and_handler_inheritance(tmp_path):
    isolated = tmp_path / "src/fiveserver"
    shutil.copytree(ROOT / "src/fiveserver", isolated, ignore=shutil.ignore_patterns("__pycache__"))
    dispatcher = isolated / "protocol/dispatcher.py"
    tree = ast.parse(dispatcher.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "GameId":
            node.body.append(ast.Assign(targets=[ast.Name(id="SYNTHETIC", ctx=ast.Store())],
                                        value=ast.Constant(value="synthetic")))
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == "ALL_GAMES" for n in node.targets):
            node.value = ast.BinOp(left=node.value, op=ast.BitOr(), right=ast.Call(
                func=ast.Name(id="frozenset", ctx=ast.Load()), args=[ast.Set(elts=[ast.Attribute(
                    value=ast.Name(id="GameId", ctx=ast.Load()), attr="SYNTHETIC", ctx=ast.Load(),
                )])], keywords=[],
            ))
    dispatcher.write_text(ast.unparse(ast.fix_missing_locations(tree)), encoding="utf-8")
    # A new command module is discovered normally, not manually registered.
    (isolated / "handlers/synthetic.py").write_text(
        "from ..protocol.dispatcher import ALL_GAMES, GameId, ServiceRole, packet_handler\n"
        "@packet_handler(0x7e00, games=ALL_GAMES, role=ServiceRole.MAIN)\n"
        "async def base(ctx, payload):\n    await ctx.reply(0x7e01, b'base')\n"
        "@packet_handler(0x7e00, games={GameId.SYNTHETIC}, role=ServiceRole.MAIN, override=True)\n"
        "async def specific(ctx, payload):\n    await ctx.reply(0x7e01, b'new-game')\n",
        encoding="utf-8",
    )
    script = r'''
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import struct
from typing import Annotated
from types import MappingProxyType

from Crypto.Cipher import Blowfish
import dataclasses_struct as dcs
import yaml

import fiveserver
from fiveserver.config import load_settings
from fiveserver.database import Profile, ProfileStats, User
from fiveserver.games.formats import PES5_FORMATS, PROTOCOL_FORMATS
from fiveserver.handlers.profile_info import get_menu_profile
from fiveserver.handlers.login import PROFILE_RESPONSE_ID
from fiveserver.handlers.lobbies import LobbySelection
from fiveserver.handlers import rooms
from fiveserver.protocol.codecs import CIPHER_KEY
from fiveserver.protocol.dispatcher import GameId, ServiceRole
from fiveserver.protocol.wire import encode_packet, read_packet
from fiveserver.server import FiveServer

@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class NewProfile:
    marker: Annotated[bytes, 3]
    identity: dcs.I32
    points: dcs.I32
    games: dcs.U8

def encode_new(profile, stats):
    return NewProfile(b"NEW", profile.id, profile.points, stats.wins).pack()

credential = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).encrypt(bytes(48))
profile = Profile(0, 1, 42, "Synthetic", points=450)

class Repository:
    async def by_hash(self, value):
        if value == credential[32:48].hex():
            return User(1, "synthetic-user", value, [profile])
        return None

    async def games_played(self, identity):
        return 9

    async def profile_by_id(self, identity):
        return profile if identity == 42 else None

    async def profile_stats(self, identity):
        return ProfileStats(wins=9)

async def main():
    game = GameId("synthetic")
    assert game is GameId.SYNTHETIC and game not in tuple(g for g in GameId if g.value != "synthetic")
    assert str(game) == "synthetic"
    assert Path(fiveserver.__file__).resolve().is_relative_to(Path.cwd() / "src")
    settings = {
        "ListenOn": "127.0.0.1", "Lobbies": ["England"], "ShowStats": True,
        "Roster": {"enforceHash": False},
        "GamePorts": {"synthetic": 0, "pes5": 0},
        "NetworkServer": {"networkMenuService": 0, "mainService": 0,
                          "loginService": {"synthetic": 0, "pes5": 0}},
    }
    path = Path("new-game.yaml")
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    roles = {
        ServiceRole.NEWS: replace(PES5_FORMATS, discovery=lambda *args: b"NEWS-NEW"),
        ServiceRole.LOGIN: replace(PES5_FORMATS, login_profile=lambda i, p, *args: b"L3" + struct.pack("!i", p.id)),
        ServiceRole.NETWORK_MENU: replace(PES5_FORMATS, profile_info=lambda p, stats: b"MENU" + struct.pack("!i", p.id)),
        ServiceRole.MAIN: replace(PES5_FORMATS, profile_info=encode_new),
    }
    definitions = {**PROTOCOL_FORMATS, game: roles}
    # Behavior inheritance is declared separately from the independent codecs.
    rooms.ROOM_EXIT_RULES = MappingProxyType({**rooms.ROOM_EXIT_RULES, game: rooms.PES5_EXIT_RULES})
    server = FiveServer(load_settings(path), (game, GameId.PES5), "127.0.0.1", Repository(),
                        protocol_formats=definitions)
    assert server.registry._handlers[(game, ServiceRole.MAIN, 0x4102)][0] is get_menu_profile
    assert server.registry._handlers[(GameId.PES5, ServiceRole.MAIN, 0x7e00)][0].__name__ == "base"
    assert server.registry._handlers[(game, ServiceRole.MAIN, 0x7e00)][0].__name__ == "specific"
    assert server.registry.registered(game, ServiceRole.MAIN) == server.registry.registered(GameId.PES5, ServiceRole.MAIN)
    await server.start()
    ports = {(g, role): listener.sockets[0].getsockname()[1]
             for (_, g, role), listener in zip(server._ports(), server.listeners)}
    assert len(set(ports.values())) == 6 and all(ports.values())

    async def connect(role, identity=game):
        return await asyncio.open_connection("127.0.0.1", ports[(identity, role)])

    async def request(reader, writer, packet_id, payload=b"", counter=1):
        writer.write(encode_packet(packet_id, payload, counter))
        await writer.drain()
        return await asyncio.wait_for(read_packet(reader), timeout=5)

    async def close(writer):
        writer.close()
        await writer.wait_closed()

    try:
        reader, writer = await connect(ServiceRole.NEWS)
        first = await request(reader, writer, 0x2005)
        entry, last = await read_packet(reader), await read_packet(reader)
        assert [p.header.packet_id for p in (first, entry, last)] == [0x2002, 0x2003, 0x2004]
        assert [p.header.counter for p in (first, entry, last)] == [1, 2, 3]
        assert entry.payload == b"NEWS-NEW"
        await close(writer)

        reader, writer = await connect(ServiceRole.NEWS, GameId.PES5)
        await request(reader, writer, 0x2005)
        assert len((await read_packet(reader)).payload) == 183
        await read_packet(reader)
        await close(writer)

        reader, writer = await connect(ServiceRole.LOGIN)
        hello = await request(reader, writer, 0x3001)
        assert hello.payload == bytes(16)
        auth = await request(reader, writer, 0x3003, credential, 2)
        assert auth.header.packet_id == 0x3004 and auth.payload == bytes(4)
        profiles = await request(reader, writer, 0x3010, counter=3)
        assert profiles.header.packet_id == PROFILE_RESPONSE_ID
        assert profiles.payload == bytes(4) + b"L3" + struct.pack("!i", 42)
        writer.write(encode_packet(0x0003, b"", 4))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 5) == b""
        await close(writer)

        reader, writer = await connect(ServiceRole.NETWORK_MENU)
        menu = await request(reader, writer, 0x4102, struct.pack("!i", 42))
        assert menu.header.packet_id == 0x4103 and menu.payload == bytes(4) + b"MENU" + struct.pack("!i", 42)
        await close(writer)

        reader, writer = await connect(ServiceRole.MAIN)
        auth = await request(reader, writer, 0x3003, credential)
        assert auth.payload == bytes(4)
        response = await request(reader, writer, 0x4102, struct.pack("!i", 42), 2)
        assert response.header.packet_id == 0x4103
        assert response.payload == bytes(4) + b"NEW" + struct.pack("!iiB", 42, 450, 9)
        override = await request(reader, writer, 0x7e00, counter=3)
        assert override.header.packet_id == 0x7e01 and override.payload == b"new-game"
        selected = await request(reader, writer, 0x3040, struct.pack("!i", 42), 4)
        assert selected.header.packet_id == 0x3042
        selection = LobbySelection(0, bytes(16), 1001, bytes(16), 1002, 0).pack()
        lobby = await request(reader, writer, 0x4202, selection, 5)
        assert lobby.header.packet_id == 0x4203
        assert len((await read_packet(reader)).payload) == 31
        created = await request(reader, writer, 0x4310, b"NewRoom".ljust(32, b"\0") + bytes(2), 6)
        assert created.header.packet_id == rooms.RoomPacketId.UPDATE and len(created.payload) == 87
        assert len((await read_packet(reader)).payload) == 31
        assert (await read_packet(reader)).header.packet_id == rooms.RoomPacketId.CREATE_REPLY
        exited = await request(reader, writer, rooms.RoomPacketId.EXIT_REQUEST, counter=7)
        assert exited.header.packet_id == rooms.RoomPacketId.UPDATE
        await read_packet(reader)  # Player update precedes exit acknowledgement.
        assert (await read_packet(reader)).header.packet_id == rooms.RoomPacketId.EXIT_REPLY
        assert (await read_packet(reader)).header.packet_id == rooms.RoomPacketId.REMOVED
        await close(writer)
    finally:
        await server.close()
    print(json.dumps({"game": game.value, "game_count": len(GameId), "roles": sorted(r.value for r in roles), "listeners": len(ports), "profile_bytes": 12}))

asyncio.run(main())
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path / "src")
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=45, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "game": "synthetic", "game_count": len(GameId) + 1, "roles": ["login", "main", "network_menu", "news"],
        "listeners": 6, "profile_bytes": 12,
    }
