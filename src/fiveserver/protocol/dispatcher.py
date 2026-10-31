"""Deterministic per-game, per-role packet registration and dispatch."""


from dataclasses import dataclass, replace
from enum import StrEnum
import inspect
import logging
import struct
from functools import wraps
from types import MappingProxyType
from typing import TYPE_CHECKING, Awaitable, Callable, Iterable, Mapping, Protocol, TypeVar, cast

import dataclasses_struct as dcs

from .wire import Packet

if TYPE_CHECKING:
    from .formats import WireFormats


LOGGER = logging.getLogger(__name__)


class GameId(StrEnum):
    """Supported PES5/PES6 game variants."""
    PES5 = "pes5"
    WE9 = "we9"
    WE9LE = "we9le"
    PES6 = "pes6"
    WE2007 = "we2007"


class ServiceRole(StrEnum):
    """TCP service roles that can receive the same packet ID."""
    NEWS = "news"
    LOGIN = "login"
    NETWORK_MENU = "network_menu"
    MAIN = "main"


FIVE_GAMES = frozenset({GameId.PES5, GameId.WE9, GameId.WE9LE})
SIX_GAMES = frozenset({GameId.PES6, GameId.WE2007})
ALL_GAMES = FIVE_GAMES | SIX_GAMES


class PacketContext(Protocol):
    """Minimal connection interface exposed to packet handlers."""
    game: GameId
    role: ServiceRole

    async def reply(self, packet_id: int, payload: bytes = b"") -> None:
        """Send a response packet on the connection."""
        ...
    async def disconnect(self) -> None:
        """Close the connection."""
        ...


Decoder = Callable[[bytes], object]
Handler = Callable[[PacketContext, object], Awaitable[None]]
HandlerT = TypeVar("HandlerT", bound=Callable[..., Awaitable[None]])


@dataclass(frozen=True)
class HandlerMetadata:
    """Decorator metadata attached to one packet handler."""
    packet_id: int
    roles: tuple[ServiceRole, ...]
    decoder: Decoder
    override: bool = False
    games: frozenset[GameId] = ALL_GAMES
    request_type: type | None = None
    takes_payload: bool = True

    def __post_init__(self) -> None:
        """Reject invalid declarations before any table can be constructed."""
        if not self.games or any(not isinstance(game, GameId) for game in self.games):
            raise ValueError("Handler games must be nonempty GameId declarations")
        if not self.roles or len(set(self.roles)) != len(self.roles):
            raise ValueError("Handler roles must be distinct and nonempty")
        if any(not isinstance(role, ServiceRole) for role in self.roles):
            raise ValueError("Unsupported service role")
        if not isinstance(self.packet_id, int) or not 0 <= self.packet_id <= 0xffff:
            raise ValueError("Invalid packet ID")
        if not callable(self.decoder) or inspect.iscoroutinefunction(self.decoder):
            raise ValueError("Decoder must be a synchronous callable")
        inspect.signature(self.decoder).bind(b"")


def raw_payload(payload: bytes) -> bytes:
    """Pass an opaque packet payload to a handler."""
    return payload


def packet_handler(
    packet_id: int,
    *,
    role: ServiceRole | tuple[ServiceRole, ...],
    games: Iterable[GameId],
    decoder: Decoder | None = None,
    request_type: type | None = None,
    override: bool = False,
) -> Callable[[HandlerT], HandlerT]:
    """Attach immutable registration metadata without registering global state."""
    roles = (role,) if isinstance(role, ServiceRole) else role
    metadata = HandlerMetadata(packet_id, tuple(roles), _resolve_decoder(decoder, request_type),
                               override, frozenset(games), request_type)

    def decorate(handler: HandlerT) -> HandlerT:
        if hasattr(handler, "__packet_handler__"):
            raise ValueError(f"Handler {handler.__name__} is already decorated")
        if not inspect.iscoroutinefunction(handler):
            raise ValueError("Packet handler must be async")
        takes_payload = _handler_accepts_payload(handler, decoder, request_type)
        setattr(handler, "__packet_handler__", metadata if takes_payload else replace(metadata, takes_payload=False))
        return cast(HandlerT, handler)

    return decorate


def _handler_accepts_payload(handler: HandlerT, decoder: Decoder | None, request_type: type | None) -> bool:
    """Validate the call signature once, including context-only commands."""
    signature = inspect.signature(handler)
    try:
        signature.bind(None, None)
    except TypeError:
        signature.bind(None)
        if decoder is not None or request_type is not None:
            raise ValueError("A decoded request needs a payload parameter")
        return False
    return True


def _bind_handler(handler: Handler, metadata: HandlerMetadata) -> Handler:
    """Adapt context-only commands at construction, not on each dispatch."""
    if metadata.takes_payload:
        return handler
    context_only = cast(Callable[[PacketContext], Awaitable[None]], handler)

    @wraps(handler)
    async def invoke(context: PacketContext, _request: object) -> None:
        await context_only(context)

    return invoke


def _resolve_decoder(decoder: Decoder | None, request_type: type | None) -> Decoder:
    """Choose one unambiguous structural, custom, or opaque decoder."""
    if request_type is not None:
        if decoder is not None:
            raise ValueError("Use decoder or request_type, not both")
        return _request_decoder(request_type)
    return raw_payload if decoder is None else decoder


def _request_decoder(request_type: type) -> Decoder:
    """Bind strict DCS unpacking once, before runtime dispatch."""
    if not dcs.is_dataclass_struct(request_type):
        raise ValueError("request_type must be a dataclasses-struct model")
    model = cast(type[dcs.DataclassStructProtocol], request_type)
    size = dcs.get_struct_size(model)

    def decode(payload: bytes) -> object:
        if len(payload) != size:
            raise ValueError(f"{model.__name__} requires {size} bytes, got {len(payload)}")
        return model.from_packed(payload)

    return decode


@dataclass(frozen=True)
class GameDefinition:
    """One game's base handler set and explicit overrides."""
    game: GameId
    handlers: tuple[Handler, ...]
    overrides: tuple[Handler, ...] = ()


class PacketRegistry:
    """Deterministic (game, role, packet ID) handler registry."""
    def __init__(self, definitions: tuple[GameDefinition, ...], *,
                 formats: Mapping[GameId, Mapping[ServiceRole, "WireFormats"]] | None = None):
        self.protocol_formats = None if formats is None else MappingProxyType({
            game: MappingProxyType(dict(roles)) for game, roles in formats.items()
        })
        table: dict[tuple[GameId, ServiceRole, int], tuple[Handler, Decoder]] = {}
        self._handlers: Mapping[tuple[GameId, ServiceRole, int], tuple[Handler, Decoder]] = table
        for definition in definitions:
            for handler in definition.handlers:
                self._add(table, definition.game, handler, replacing=False)
            for handler in definition.overrides:
                self._add(table, definition.game, handler, replacing=True)
        self._handlers = MappingProxyType(table)

    @classmethod
    def from_handlers(cls, games: Iterable[GameId], handlers: Iterable[Handler], *,
                      formats: Mapping[GameId, Mapping[ServiceRole, "WireFormats"]] | None = None) -> "PacketRegistry":
        """Apply shared bases then increasingly specific explicit overrides."""
        selected = frozenset(games)
        if not selected or any(not isinstance(game, GameId) for game in selected):
            raise ValueError("Invalid registry games")
        declarations = []
        for handler in handlers:
            metadata = getattr(handler, "__packet_handler__", None)
            if not isinstance(metadata, HandlerMetadata):
                raise ValueError(f"Undecorated handler: {handler}")
            declarations.append((handler, metadata))
        ordered = sorted(declarations, key=lambda item: (
            item[1].override, -len(item[1].games), item[0].__module__, item[0].__name__,
        ))
        definitions = []
        for game in sorted(selected):
            applicable = [(h, m) for h, m in ordered if game in m.games]
            definitions.append(GameDefinition(
                game, tuple(h for h, m in applicable if not m.override),
                tuple(h for h, m in applicable if m.override),
            ))
        return cls(tuple(definitions), formats=formats)

    def _add(
        self, table: dict[tuple[GameId, ServiceRole, int], tuple[Handler, Decoder]],
        game: GameId, handler: Handler, *, replacing: bool,
    ) -> None:
        metadata = getattr(handler, "__packet_handler__", None)
        if not isinstance(metadata, HandlerMetadata):
            raise ValueError(f"Undecorated handler: {handler}")
        if metadata.override != replacing:
            raise ValueError(f"Incorrect override declaration: {handler.__name__}")
        if game not in metadata.games:
            raise ValueError(f"Handler not declared for game: {game}")
        for role in metadata.roles:
            key = (game, role, metadata.packet_id)
            self._validate_slot(key, metadata)
            table[key] = _bind_handler(handler, metadata), metadata.decoder

    def _validate_slot(self, key: tuple[GameId, ServiceRole, int], metadata: HandlerMetadata) -> None:
        """Require a base and reject ambiguous overrides, allowing nested scopes."""
        previous = self._handlers.get(key)
        if previous is None:
            if metadata.override:
                raise ValueError(f"Override has no base registration: {key}")
            return
        if not metadata.override:
            raise ValueError(f"Duplicate packet registration: {key}")
        base = getattr(previous[0], "__packet_handler__")
        if base.override and not metadata.games < base.games:
            raise ValueError(f"Competing overrides: {key}")

    async def dispatch(self, context: PacketContext, packet: Packet) -> None:
        """Decode and invoke the handler, or apply the original unknown-command policy."""
        if self.protocol_formats is not None:
            setattr(context, "protocol_formats", self.protocol_formats)
        key = (context.game, context.role, packet.header.packet_id)
        registration = self._handlers.get(key)
        if registration is None:
            if context.role is ServiceRole.NEWS:
                LOGGER.warning("Ignoring unsupported packet: %s", key)
            else:
                # Original LoginService.defaultHandler acknowledged every unknown
                # packet with four zero bytes on the next packet ID.
                await context.reply((packet.header.packet_id + 1) & 0xFFFF, bytes(4))
            return
        handler, decoder = registration
        try:
            request = decoder(packet.payload)
        except (struct.error, OverflowError) as exc:
            raise ValueError(f"Malformed packet: {key}") from exc
        await handler(context, request)

    def registered(self, game: GameId, role: ServiceRole) -> frozenset[int]:
        """Return packet IDs registered for a game and service role."""
        return frozenset(key[2] for key in self._handlers if key[:2] == (game, role))
