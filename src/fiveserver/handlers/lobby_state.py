"""Shared lobby state and player presence encoders."""

import asyncio
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol

import dataclasses_struct as dcs

from ..database import Profile, ProfileStats, User
from ..protocol.dispatcher import GameId, ServiceRole
from ..protocol.formats import PlayerEncoder
from ..protocol.records import Pes5PlayerRecord, Pes6PlayerRecord


LOBBY_NAME_SIZE = 32
OPEN_LOBBY_CODE = 0x5F
NO_STATS_LOBBY_CODE = 0x20


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class LobbyRecord:
    """Fixed 35-byte lobby record used in the ``0x4201`` listing.

    The source layout is one type byte, a 32-byte UTF-8 name padded with NULs,
    and the current player count.
    """
    type_code: dcs.U8
    name: Annotated[bytes, LOBBY_NAME_SIZE]
    players: dcs.U16


class LobbyPeer(Protocol):
    """Per-connection lobby state exposed to lobby handlers."""
    user: User | None
    selected_profile: Profile | None
    room_id: int
    no_lobby_chat: int
    role: ServiceRole
    game: GameId
    ip1: bytes
    ip2: bytes
    udp_port1: int
    udp_port2: int

    async def reply(self, packet_id: int, payload: bytes = b"") -> None:
        """Send a packet back to this lobby peer."""
        ...


@dataclass
class Lobby:
    """A configured lobby and its live players, rooms, and chat history."""
    name: str
    type_code: int
    type_name: str = "open"
    show_matches: bool = True
    check_roster_hash: bool = True
    players: dict[str, LobbyPeer] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    rooms: dict[bytes, Any] = field(default_factory=dict)
    room_ordinal: int = 0
    chat_history: list[Any] = field(default_factory=list)

    @property
    def player_count(self) -> int:
        """Return the number of players currently in the lobby."""
        return len(self.players)

    def pack(self) -> bytes:
        """Serialize this lobby for the 0x4201 lobby list."""
        encoded = self.name.encode("utf-8", "replace")[:LOBBY_NAME_SIZE]
        return LobbyRecord(self.type_code, encoded, self.player_count).pack()


def player_info(peer: LobbyPeer, stats: ProfileStats | None = None, *, encoder: PlayerEncoder) -> bytes:
    """Prepare selected-profile state for a caller-selected presence encoder."""
    profile = peer.selected_profile
    if profile is None:
        raise ValueError("Player has no selected profile")
    return encoder(profile, stats or ProfileStats(), peer.room_id, peer.no_lobby_chat)


def encode_pes5_player(profile: Profile, stats: ProfileStats, room_id: int, no_lobby_chat: int) -> bytes:
    """Encode the original 31-byte PES5 lobby presence."""
    return Pes5PlayerRecord(profile.id, profile.name.encode("utf-8"),
                            int(room_id != 0), room_id, no_lobby_chat, bytes(2)).pack()


def encode_pes6_player(profile: Profile, stats: ProfileStats, room_id: int, no_lobby_chat: int) -> bytes:
    """Encode the legacy-source 127-byte PES6 lobby presence, including group fields."""
    division = next((index for index, threshold in enumerate((250, 450, 600, 750)) if profile.points < threshold), 4)
    return Pes6PlayerRecord(profile.id, profile.name.encode("utf-8"), bytes(52),
                            0, division, room_id, profile.points, 0,
                            stats.wins + stats.losses + stats.draws,
                            stats.wins, stats.losses, stats.draws, bytes(3)).pack()
