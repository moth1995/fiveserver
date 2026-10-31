import asyncio
import struct

from fiveserver.handlers.chat import ChatPacketId, send_history
from fiveserver.protocol.dispatcher import GameId
from fiveserver.handlers.lobbies import configured_lobbies
from fiveserver.games.definitions import make_registry
from fiveserver.protocol.wire import Packet, PacketHeader

from test_lobby_presence import Context


def test_repeated_chat_history_triggers_coalesce_into_one_replay(monkeypatch):
    from datetime import datetime

    from fiveserver.database import Profile
    from fiveserver.handlers.chat import ChatMessage
    from fiveserver.protocol.dispatcher import ServiceRole
    from fiveserver.server import Connection
    from fiveserver import server as server_module

    class Writer:
        def __init__(self):
            self.packets = []

        def get_extra_info(self, name):
            return {"sockname": ("127.0.0.1", 12345), "peername": ("127.0.0.1", 54321)}.get(name)

        def write(self, data):
            self.packets.append(data)

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def scenario():
        monkeypatch.setattr(server_module, "CHAT_HISTORY_DELAY_SECONDS", 0.01)
        lobby = configured_lobbies({"Lobbies": ["England"]})[0]
        lobby.chat_history.append(ChatMessage(
            Profile(0, 0, 0, "SYSTEM"), b"Date: test", None, bytes(4), datetime.now(),
        ))
        writer = Writer()
        context = Connection(asyncio.StreamReader(), writer, GameId.PES5, ServiceRole.MAIN, {}, "127.0.0.1")
        context.selected_profile = Profile(1, 1, 1, "Player")
        context.lobby_id = 0
        context.lobbies = [lobby]
        try:
            for _ in range(5):
                context.schedule_chat_history(lobby)
            await asyncio.sleep(0.05)
            assert len(writer.packets) == 1
        finally:
            await context.disconnect()

    asyncio.run(scenario())


def test_pes5_lobby_and_private_chat_history():
    async def scenario():
        lobby = configured_lobbies({"Lobbies": ["England"]})[0]
        users = [Context(index, [lobby]) for index in (1, 2, 3)]
        for user in users:
            user.lobby_id = 0
            user.settings = {"Chat": {"bannedWords": ["blocked"], "warningMessage": "removed"}}
            lobby.players[user.user.hash] = user
        registry = make_registry((GameId.PES5,))
        sender, recipient, other = users

        async def send(kind, target, text):
            payload = kind + bytes(4) + struct.pack("!i", target) + text
            await registry.dispatch(sender, Packet(PacketHeader(ChatPacketId.SEND, len(payload), 1), payload))

        await send(b"\0\1", 0, b"hello")
        assert all(user.replies[-1][0] == ChatPacketId.RECEIVE for user in users)
        assert sender.replies[-1][1].endswith(b"hello\0\0")
        assert len(lobby.chat_history) == 1

        await send(b"\0\2", recipient.selected_profile.id, b"blocked text")
        assert len(lobby.chat_history) == 2
        assert recipient.replies[-1][1].endswith(b"[removed]\0\0")
        before = len(other.replies)
        await send_history(other, lobby)
        assert len(other.replies) == before + 1

    asyncio.run(scenario())


def test_pes6_room_chat_uses_two_byte_kind_and_long_name():
    async def scenario():
        lobby = configured_lobbies({"Lobbies": ["England"]})[0]
        sender = Context(1, [lobby])
        sender.game = GameId.PES6
        sender.lobby_id = 0
        sender.room_id = 1
        lobby.players[sender.user.hash] = sender

        class Room:
            id = 1
            players = [sender]

        lobby.rooms[b"r"] = Room()
        payload = b"\x01\x08" + bytes(8) + b"room message"
        await make_registry((GameId.PES6,)).dispatch(
            sender, Packet(PacketHeader(ChatPacketId.SEND, len(payload), 1), payload),
        )
        assert sender.replies[-1][1][:2] == b"\x01\x08"
        assert len(sender.replies[-1][1]) == 2 + 4 + 4 + 48 + len(b"room message") + 2

    asyncio.run(scenario())


