"""Room state serialization shared by PES5 and PES6 handlers."""


import struct
from typing import TYPE_CHECKING

from ..protocol.records import (
    ParticipationArray, ParticipationRecord, PeerRecord, Pes5RoomRecord,
    Pes6RoomRecord, RoomPlayerRecord, RoomTeamsRecord, TeamGoalsRecord,
)

if TYPE_CHECKING:
    from .lobby_state import LobbyPeer
    from .rooms import Room, RoomContext


PES5_ROOM_NAME_SIZE = 32
PES5_ROOM_PLAYERS_SIZE = 48
PES6_ROOM_NAME_SIZE = 64
PES6_MAX_ROOM_PLAYERS = 4
PES6_EMPTY_PLAYER = b"\0\0\0\0\0\0\xff\0\0\xff"
PES6_UNSELECTED_TEAMS = b"\xff\xff" + bytes(5) + b"\xff\xff" + bytes(5)
PES6_PARTICIPATION_EMPTY = b"\0\0\0\0\0\xff"
DEFAULT_ROOM_PHASE = 1


def _pes6_room_entries(room: "Room") -> list[RoomPlayerRecord]:
    selection = room.team_selection
    return [
        RoomPlayerRecord(
            _profile_id(peer), int(peer is room.owner), int(peer is room.match_starter),
            selection.side(_profile_id(peer)) if selection else 0xff,
            int(peer.spectator), index, _participation(room, peer),
        )
        for index, peer in enumerate(room.players)
    ]


def _pes6_room_teams(room: "Room") -> RoomTeamsRecord:
    selection, match = room.team_selection, room.match6
    if selection is None:
        return RoomTeamsRecord(TeamGoalsRecord(0xffff, [0] * 5), TeamGoalsRecord(0xffff, [0] * 5))
    home = selection.home_team_id if selection.home_team_id is not None else 0xffff
    away = selection.away_team_id if selection.away_team_id is not None else 0xffff
    home_goals = match.home_goals if match else (0, 0, 0, 0, 0)
    away_goals = match.away_goals if match else (0, 0, 0, 0, 0)
    return RoomTeamsRecord(TeamGoalsRecord(home, list(home_goals)), TeamGoalsRecord(away, list(away_goals)))


def encode_pes6_room(room: "Room") -> bytes:
    """Encode the 131-byte PES6 room list/update record."""
    players, match = room.players, room.match6
    if len(players) > PES6_MAX_ROOM_PLAYERS:
        raise ValueError("PES6 room exceeds four players")
    slots = _pes6_room_entries(room) + [RoomPlayerRecord(0, 0, 0, 0xff, 0, 0, 0xff)] * (4 - len(players))
    return Pes6RoomRecord(
        room.id, room.phase, match.state if match else 0, room.name,
        match.clock if match else 0, slots, _pes6_room_teams(room), 0,
        int(room.locked), b"\0\x02\0\0",
    ).pack()


# TODO(0x4302 / 0x4306): verified against pes5.exe. Both packets use the SAME
# 65-byte room record (ParseLobbyRoomListResponse 0x0083bc10, ParseLobbyRoomUpdated
# 0x0083c480):
#   i32 room_id | u8 state | u8 locked | name[32] | u8 match_time_slot |
#   2 x { i32 player_id | u16 team_id | u8 x5 } | u8 | u8 | u8 | u8
# - state: BuildLobbyRoomUiList (0x004a6880) shows a room only for 1..8 (1-2 use
#   player 0 only, 3-7 use both players, 8 checks each player separately); any
#   other value hides it. A player whose lobby status is above 4, or whose lobby
#   room id differs from room_id, also hides the room. We always send 1 (see the
#   pes5_games_underway memory: 5-7 = games underway).
# - Every field is decoded from the original game layout. In short: the byte
#   after state is the current PERIOD (0..4, 5 = finished), not "locked"; per
#   player u16 team id + goals for 1H, 2H, ET1, ET2, PK; trailing bytes = extra
#   time on, no penalties, abnormal end, and (byte 4) the room's lock icon. So we
#   currently put room.locked in the period byte and never set the lock icon.
# BUGS in what we send:
#   1. The list form packs player ids with a 4-byte stride, but the client reads
#      11 bytes per player in BOTH packets. In 0x4302 the second player's id is
#      therefore read from zeros (bytes 15..18) and the first player's u16/5 bytes
#      come from the second id. Use the 11-byte update layout for both.
#   2. We send 87 bytes, the client reads 65. Its loop runs while unread bytes
#      remain, so the leftover 22 bytes are parsed as a SECOND, all-zero room in
#      every 0x4302 (state 0, so it is never drawn, but it takes one of the 100
#      slots). Send exactly 65 bytes per record (several records may share one
#      0x4302 packet).
def encode_pes5_room(room: "Room", *, update: bool = False) -> bytes:
    """Encode the 87-byte PES5 record with its list or update player stride."""
    players = room.players
    entries = b"".join(
        struct.pack("!i", _profile_id(peer)) + (bytes(7) if update else b"")
        for peer in players
    )
    entry_size = 11 if update else 4
    return Pes5RoomRecord(
        room.id, DEFAULT_ROOM_PHASE, int(room.locked), room.name, room.match_time // 5,
        entries + bytes(PES5_ROOM_PLAYERS_SIZE - len(players) * entry_size),
    ).pack()


def _profile_id(peer: "LobbyPeer") -> int:
    if peer.selected_profile is None:
        raise ValueError("Room player has no profile")
    return peer.selected_profile.id


def _peer_info(peer: "RoomContext", room: "Room", *, incoming: bool) -> bytes:
    return (
        bytes(36 if incoming else 32)
        + PeerRecord(peer.ip1, peer.udp_port1, peer.ip2, peer.udp_port2,
                     _profile_id(peer), 0, _participation(room, peer)).pack()
    )


def _participation(room: "Room", peer: "RoomContext") -> int:
    try:
        return room.participating_players.index(peer)
    except ValueError:
        return 0xff


def participation_status(room: "Room") -> bytes:
    """Serialize the fixed four-slot PES6 participation array."""
    players = room.players
    if len(players) > PES6_MAX_ROOM_PLAYERS:
        raise ValueError("Room exceeds PES6 player limit")
    slots = [ParticipationRecord(_profile_id(peer), index, _participation(room, peer))
             for index, peer in enumerate(players)]
    slots += [ParticipationRecord(0, 0, 0xff)] * (PES6_MAX_ROOM_PLAYERS - len(players))
    return ParticipationArray(slots).pack()
