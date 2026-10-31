"""Network menu commands with source-defined empty responses."""


from enum import IntEnum

from ..protocol.dispatcher import ALL_GAMES, PacketContext, ServiceRole, packet_handler


MENU_ROLES = (ServiceRole.NETWORK_MENU, ServiceRole.MAIN)
STATUS_SIZE = 4


class MenuPacketId(IntEnum):
    """Packet IDs for compatibility menu screens."""
    SETTINGS_LIST = 0x3080
    SETTINGS_LIST_START = 0x3082
    SETTINGS_LIST_END = 0x3086
    FRIENDS = 0x4580
    FRIENDS_START = 0x4581
    FRIENDS_END = 0x4583
    INBOX = 0x4780
    INBOX_START = 0x4781
    INBOX_END = 0x4783
    SEARCH_PLAYERS = 0x4600
    SEARCH_START = 0x4601
    SEARCH_END = 0x4603


@packet_handler(MenuPacketId.SETTINGS_LIST, games=ALL_GAMES, role=MENU_ROLES)
async def settings_list(ctx: PacketContext, payload: object) -> None:
    """Return the original empty 0x3082/0x3086 settings list."""
    await ctx.reply(MenuPacketId.SETTINGS_LIST_START, bytes(STATUS_SIZE))
    await ctx.reply(MenuPacketId.SETTINGS_LIST_END)


@packet_handler(MenuPacketId.FRIENDS, games=ALL_GAMES, role=MENU_ROLES)
async def friends(ctx: PacketContext, payload: object) -> None:
    """Return the original empty friends list."""
    await ctx.reply(MenuPacketId.FRIENDS_START, bytes(STATUS_SIZE))
    await ctx.reply(MenuPacketId.FRIENDS_END, bytes(STATUS_SIZE))


@packet_handler(MenuPacketId.INBOX, games=ALL_GAMES, role=MENU_ROLES)
async def inbox(ctx: PacketContext, payload: object) -> None:
    """Return the original empty inbox list."""
    await ctx.reply(MenuPacketId.INBOX_START, bytes(STATUS_SIZE))
    await ctx.reply(MenuPacketId.INBOX_END, bytes(STATUS_SIZE))


@packet_handler(MenuPacketId.SEARCH_PLAYERS, games=ALL_GAMES, role=MENU_ROLES)
async def search_players(ctx: PacketContext, payload: object) -> None:
    """Return the original empty player-search response."""
    await ctx.reply(MenuPacketId.SEARCH_START, bytes(STATUS_SIZE))
    await ctx.reply(MenuPacketId.SEARCH_END, bytes(STATUS_SIZE))
