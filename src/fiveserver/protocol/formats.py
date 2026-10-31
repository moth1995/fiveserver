"""Small immutable wire-operation definitions, independent of gameplay rules."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

from ..models import Profile, ProfileStats
from .dispatcher import GameId, ServiceRole
from .packets import RoomCreateRequest
from .records import LoginProfileState

if TYPE_CHECKING:
    from ..handlers.rooms import Room

ProfileEncoder = Callable[[Profile, ProfileStats], bytes]
LoginProfileEncoder = Callable[[int, Profile, LoginProfileState, int, int], bytes]
PlayerEncoder = Callable[[Profile, ProfileStats, int, int], bytes]
RoomEncoder = Callable[["Room"], bytes]
DiscoveryEncoder = Callable[[dict[str, Any], str, str, int], bytes]
ChatPrefixEncoder = Callable[[bytes, bytes, Profile], bytes]
_DEFAULT_FORMATS: Mapping[GameId, Mapping[ServiceRole, "WireFormats"]] | None = None


def set_default_formats(formats: Mapping[GameId, Mapping[ServiceRole, "WireFormats"]]) -> None:
    """Install the default game/role format table without importing game assembly here."""
    global _DEFAULT_FORMATS
    _DEFAULT_FORMATS = formats


@dataclass(frozen=True)
class WireFormats:
    """Only operations whose representations actually differ between protocols."""
    profile_info: ProfileEncoder
    login_profile: LoginProfileEncoder
    player_info: PlayerEncoder
    room_list: RoomEncoder
    room_update: RoomEncoder
    room_create: Callable[[bytes], RoomCreateRequest]
    discovery: DiscoveryEncoder
    chat_prefix: ChatPrefixEncoder
    system_chat_prefix: ChatPrefixEncoder
    player_stats: bool = False


class FormatContext(Protocol):
    """Identity used to resolve a connection's current protocol, not its listener."""
    game: GameId
    role: ServiceRole


def active_formats(context: FormatContext, *, role: ServiceRole | None = None) -> WireFormats:
    """Resolve by live game and role so authentication recovery cannot go stale."""
    definitions: Mapping[GameId, Mapping[ServiceRole, WireFormats]] | None = getattr(
        context, "protocol_formats", None,
    )
    if definitions is None:
        definitions = _DEFAULT_FORMATS
        if definitions is None:
            raise RuntimeError("Default protocol formats have not been installed")
    return definitions[context.game][role or context.role]


def decode_room_creation(context: FormatContext, payload: bytes) -> RoomCreateRequest:
    """Decode a variable room request with the active protocol's fixed prefix."""
    return active_formats(context).room_create(payload)
