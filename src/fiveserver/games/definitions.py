"""Deterministic discovery limited to FiveServer's dedicated handler package."""

from functools import lru_cache
import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING, Iterable, Mapping

from ..protocol.dispatcher import (
    FIVE_GAMES, SIX_GAMES, GameId, Handler, HandlerMetadata, PacketRegistry, ServiceRole,
)
from .formats import PROTOCOL_FORMATS

__all__ = ["FIVE_GAMES", "SIX_GAMES", "make_registry"]

if TYPE_CHECKING:
    from ..protocol.formats import WireFormats


@lru_cache(maxsize=None)
def discover_handlers(package_name: str = "fiveserver.handlers") -> tuple[Handler, ...]:
    """Import and inspect local handlers once, ignoring re-exported functions."""
    package = importlib.import_module(package_name)
    names = sorted(module.name for module in pkgutil.walk_packages(
        package.__path__, package.__name__ + ".",
    ))
    handlers = []
    for name in [package_name, *names]:
        module = importlib.import_module(name)
        for _, function in inspect.getmembers(module, inspect.isfunction):
            if function.__module__ == name and isinstance(
                getattr(function, "__packet_handler__", None), HandlerMetadata,
            ):
                handlers.append(function)
    return tuple(handlers)


def make_registry(games: Iterable[GameId], *,
                  formats: Mapping[GameId, Mapping[ServiceRole, "WireFormats"]] | None = None) -> PacketRegistry:
    """Build an immutable effective table from discovered decorator metadata."""
    return PacketRegistry.from_handlers(
        games, discover_handlers(),
        formats=formats if formats is not None else PROTOCOL_FORMATS,
    )
