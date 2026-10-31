"""Declarative game/role wire bindings; shared-listener roles keep legacy layouts."""

from dataclasses import replace
from functools import partial
from types import MappingProxyType

from ..handlers.chat import encode_pes5_chat_prefix, encode_pes6_chat_prefix
from ..handlers.lobby_state import encode_pes5_player, encode_pes6_player
from ..handlers.login import encode_pes5_login_profile, encode_pes6_login_profile
from ..handlers.news import encode_pes5_discovery, encode_pes6_discovery
from ..handlers.profile_info import encode_pes5_profile, encode_pes6_profile
from ..handlers.room_codecs import encode_pes5_room, encode_pes6_room
from ..protocol.dispatcher import FIVE_GAMES, SIX_GAMES, ServiceRole
from ..protocol.formats import WireFormats, set_default_formats
from ..protocol.packets import decode_pes5_room_create, decode_pes6_room_create

PES5_FORMATS = WireFormats(
    encode_pes5_profile, encode_pes5_login_profile, encode_pes5_player,
    encode_pes5_room, partial(encode_pes5_room, update=True), decode_pes5_room_create,
    encode_pes5_discovery, encode_pes5_chat_prefix, encode_pes5_chat_prefix,
)
PES6_FORMATS = WireFormats(
    encode_pes6_profile, encode_pes6_login_profile, encode_pes6_player,
    encode_pes6_room, encode_pes6_room, decode_pes6_room_create,
    encode_pes6_discovery, encode_pes6_chat_prefix, encode_pes6_chat_prefix, player_stats=True,
)

# The PES6 network menu retains PES5 profile/player/room layouts. Login alone
# overrides the login-list row; only the main service overrides profile details.
PES6_MENU_FORMATS = replace(PES5_FORMATS, discovery=encode_pes6_discovery, system_chat_prefix=encode_pes6_chat_prefix)
PES6_LOGIN_FORMATS = replace(PES6_MENU_FORMATS, login_profile=encode_pes6_login_profile)
PES6_MAIN_FORMATS = replace(PES6_FORMATS, login_profile=encode_pes5_login_profile)

PROTOCOL_FORMATS = MappingProxyType({
    **{game: MappingProxyType(dict.fromkeys(ServiceRole, PES5_FORMATS)) for game in FIVE_GAMES},
    **{game: MappingProxyType({
        ServiceRole.NEWS: PES6_FORMATS,
        ServiceRole.LOGIN: PES6_LOGIN_FORMATS,
        ServiceRole.NETWORK_MENU: PES6_MENU_FORMATS,
        ServiceRole.MAIN: PES6_MAIN_FORMATS,
    }) for game in SIX_GAMES},
})
set_default_formats(PROTOCOL_FORMATS)
