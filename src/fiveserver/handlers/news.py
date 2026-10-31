"""Source-compatible server discovery and news responses."""


import struct
import time
from datetime import datetime, timezone
from enum import IntEnum
from typing import Annotated, Any, Protocol

import dataclasses_struct as dcs

from ..protocol.dispatcher import ALL_GAMES, SIX_GAMES, PacketContext, ServiceRole, packet_handler
from ..protocol.records import NewsHeaderRecord
from ..protocol.formats import active_formats


SERVER_NAME_SIZE = 32
SERVER_IP_SIZE = 15
NEWS_TITLE_SIZE = 64
NEWS_TEXT_SIZE = 512
NEWS_DATE_SIZE = 19
STATUS_SIZE = 4
VERSION = "0.5.0"


class NewsPacketId(IntEnum):
    """Packet IDs used by server discovery and news."""
    DISCONNECT = 0x0003
    SERVER_LIST_REQUEST = 0x2005
    SERVER_LIST_START = 0x2002
    SERVER_LIST_ENTRY = 0x2003
    SERVER_LIST_END = 0x2004
    TIME_REQUEST = 0x2006
    TIME_RESPONSE = 0x2007
    NEWS_REQUEST = 0x2008
    NEWS_START = 0x2009
    NEWS_ENTRY = 0x200A
    NEWS_END = 0x200B
    WEB_SERVER_LIST_REQUEST = 0x2200
    WEB_SERVER_LIST_START = 0x2201
    WEB_SERVER_LIST_END = 0x2203


class DiscoveryType(IntEnum):
    """Service type codes embedded in PES5 and PES6 server-list records."""
    PES5_LOGIN = 1
    PES5_MAIN = 2
    PES5_NETWORK_MENU = 3
    PES6_LOGIN = 2
    PES6_MAIN = 3
    PES6_NETWORK_MENU = 8


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ServerRecord:
    """Fixed 61-byte server-list record.

    Layout: two signed 32-bit discriminators, a 32-byte name, a 15-byte IP,
    and three unsigned 16-bit values for port, users, and ordinal.
    """
    # TODO(0x2003): pes5.exe parser 0x00846020 reads every field below (up to 200
    # records, one per packet or several per packet). type_code is the CHANNEL:
    # the 0x2004 case of FUN_00498f40 groups records by type 0..5 and the client
    # connects to channel n by that index (1 login, 2 main, 3 network menu here).
    # The "Select server" list uses channel 2: name, and users drawn against a
    # hard-coded capacity of 1000 (FUN_004a5000 / FUN_004a5020). The first i32
    # (we send -1) and ordinal have no reader in those accessors.
    unknown: dcs.I32
    type_code: dcs.I32
    name: Annotated[bytes, SERVER_NAME_SIZE]
    ip: Annotated[bytes, SERVER_IP_SIZE]
    port: dcs.U16
    users: dcs.U16
    ordinal: dcs.U16


class NewsContext(PacketContext, Protocol):
    """Connection state required by news handlers."""
    settings: dict[str, Any]
    server_ip: str
    local_port: int
    online_count: int
    banned: bool


def pad_zeros(value: str, width: int) -> bytes:
    """Encode a string as UTF-8, truncate, and pad with NUL bytes."""
    encoded = value.encode("utf-8", "replace")[:width]
    return encoded + bytes(width - len(encoded))


def server_record(name: str, ip: str, port: int, type_code: int, ordinal: int, users: int) -> bytes:
    """Serialize one server-list record using the original -1 discriminator."""
    if len(ip.encode("utf-8")) > SERVER_IP_SIZE:
        raise ValueError("Server IP cannot fit the original packet layout")
    return ServerRecord(
        -1, type_code, pad_zeros(name, SERVER_NAME_SIZE), pad_zeros(ip, SERVER_IP_SIZE),
        port, users, ordinal,
    ).pack()


# TODO(0x200A): verified in pes5.exe. Parser FUN_008461c0 (0x3122 uses the same
# record, parser 0x0083e710) reads i32 | u8 flag1 | u8 flag2 | date[19] |
# title[64] | text[512]; the news screen (FUN_004b2f70) keeps at most 30 items.
# - i32 (we send 0 as "status"): copied as an item id; nothing uses its value.
# - flag1 != 0 = IMPORTANT: on entering Network Mode (FUN_0048d670 state 3) every
#   such item is shown as a pop-up dialog (title + text), and the list draws
#   icon frame 1 for it.
# - flag2 == 1: list icon frame 2 (only when flag1 is 0).
# - Neither: icon frame 0 when the date is less than 24 h old (client-computed
#   "new"), else no icon. The date must be "YYYY-MM-DD hh:mm:ss" for that check.
# We send 01 01, so every news item pops up on every login. Send 00 00 for normal
# items and flag1 = 1 only for announcements. Which textures the frames show
# (imp_icon / new_icon / ...) is not traced (UI bins).
# The 0x2003 server-record fields are documented at ServerRecord.
def _news_body(title: str, message: str, *, fixed_text: bool = False) -> bytes:
    # The source strips the trailing zeros from the normal greeting text.
    now = str(datetime.now(timezone.utc).replace(tzinfo=None))
    text = pad_zeros(message, NEWS_TEXT_SIZE)
    if not fixed_text:
        text = text.split(b"\0", 1)[0]
    return NewsHeaderRecord(0, b"\x01\x01", pad_zeros(now, NEWS_DATE_SIZE), pad_zeros(title, NEWS_TITLE_SIZE)).pack() + text


def _network(settings: dict[str, Any]) -> dict[str, Any]:
    return settings["NetworkServer"]


def encode_pes5_discovery(settings: dict[str, Any], server_ip: str, game: str, online_count: int) -> bytes:
    """Encode PES5 discovery in main/menu/login order with its original codes."""
    network = _network(settings)
    login_port = network["loginService"][game]
    records = (
        (str(settings.get("ServerName", "Fiveserver")), network["mainService"], DiscoveryType.PES5_MAIN, DiscoveryType.PES5_MAIN, max(0, online_count - 1)),
        ("NETWORK_MENU", network["networkMenuService"], DiscoveryType.PES5_NETWORK_MENU, DiscoveryType.PES5_NETWORK_MENU, 0),
        ("LOGIN", login_port, DiscoveryType.PES5_LOGIN, DiscoveryType.PES5_LOGIN, 0),
    )
    return b"".join(server_record(name, server_ip, int(port), type_code, ordinal, users) for name, port, type_code, ordinal, users in records)


def encode_pes6_discovery(settings: dict[str, Any], server_ip: str, game: str, online_count: int) -> bytes:
    """Encode PES6 discovery in login/main/menu order with codes 2/3/8."""
    network = _network(settings)
    records = (
        ("LOGIN", network["loginService"][game], DiscoveryType.PES6_LOGIN, DiscoveryType.PES6_LOGIN, 0),
        (str(settings.get("ServerName", "Fiveserver")), network["mainService"], DiscoveryType.PES6_MAIN, DiscoveryType.PES6_MAIN, max(0, online_count - 1)),
        ("NETWORK_MENU", network["networkMenuService"], DiscoveryType.PES6_NETWORK_MENU, DiscoveryType.PES6_NETWORK_MENU, 0),
    )
    return b"".join(server_record(name, server_ip, int(port), type_code, ordinal, users) for name, port, type_code, ordinal, users in records)


async def _server_list(ctx: NewsContext) -> None:
    payload = active_formats(ctx).discovery(ctx.settings, ctx.server_ip, ctx.game.value, ctx.online_count)
    await ctx.reply(NewsPacketId.SERVER_LIST_START, bytes(STATUS_SIZE))
    await ctx.reply(NewsPacketId.SERVER_LIST_ENTRY, payload)
    await ctx.reply(NewsPacketId.SERVER_LIST_END, bytes(STATUS_SIZE))


@packet_handler(NewsPacketId.SERVER_LIST_REQUEST, games=ALL_GAMES, role=ServiceRole.NEWS)
async def server_list(ctx: PacketContext, payload: object) -> None:
    """Send discovery records in the active protocol's order and format."""
    await _server_list(ctx)  # type: ignore[arg-type]


@packet_handler(NewsPacketId.TIME_REQUEST, games=ALL_GAMES, role=ServiceRole.NEWS)
async def server_time(ctx: PacketContext, payload: object) -> None:
    """Return the current Unix timestamp."""
    await ctx.reply(NewsPacketId.TIME_RESPONSE, struct.pack("!I", int(time.time())))


@packet_handler(NewsPacketId.NEWS_REQUEST, games=ALL_GAMES, role=ServiceRole.NEWS)
async def news(ctx: PacketContext, payload: object) -> None:
    """Send greeting, banned, or capacity news using the original packet sequence."""
    context: NewsContext = ctx  # type: ignore[assignment]
    await ctx.reply(NewsPacketId.NEWS_START, bytes(STATUS_SIZE))
    if context.banned:
        await ctx.reply(NewsPacketId.NEWS_ENTRY, _news_body(
            f"Fiveserver (v{VERSION})",
            "Sorry, but you are currently banned\r\nfrom playing on this server. Please\r\n"
            "contact server administrator if you\r\nbelieve that there was a mistake.",
            fixed_text=True,
        ))
    elif context.online_count >= int(context.settings.get("MaxUsers", 1000)):
        await ctx.reply(NewsPacketId.NEWS_ENTRY, _news_body(
            f"Fiveserver (v{VERSION})",
            "Sorry, but the server is currently at capacity.\r\n"
            f"We already have a maximum of {context.online_count} users logged in,\r\n"
            "so please come back at a later time.\r\nThanks.\r\n",
            fixed_text=True,
        ))
    else:
        greeting = context.settings.get("Greeting", {})
        text = greeting.get("text", "") if isinstance(greeting, dict) else ""
        await ctx.reply(NewsPacketId.NEWS_ENTRY, _news_body(f"SYSTEM: Fiveserver v{VERSION}", str(text)))
    await ctx.reply(NewsPacketId.NEWS_END)


@packet_handler(NewsPacketId.WEB_SERVER_LIST_REQUEST, games=SIX_GAMES, role=ServiceRole.NEWS)
async def web_server_list(ctx: PacketContext, payload: object) -> None:
    """Return the original empty PES6 web-server-list exchange.

    The source contains a TODO for packet ``0x2202`` and sends only the start
    and end markers, so no web-server records are fabricated here.
    """
    await ctx.reply(NewsPacketId.WEB_SERVER_LIST_START, bytes(STATUS_SIZE))
    await ctx.reply(NewsPacketId.WEB_SERVER_LIST_END, bytes(STATUS_SIZE))


@packet_handler(NewsPacketId.DISCONNECT, games=ALL_GAMES, role=ServiceRole.NEWS)
async def ignore_news_disconnect(ctx: PacketContext, payload: object) -> None:
    # The original NewsProtocol inherits an unknown-command policy that ignores this packet.
    """Ignore 0x0003 on news ports, matching the original dispatcher."""
    return
