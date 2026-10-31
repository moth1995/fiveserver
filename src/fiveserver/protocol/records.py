"""Verified fixed wire records; sizes exclude status prefixes and TCP framing.

Layouts follow legacy PES5/PES6 formatProfileInfo, formatRoomInfo and
formatRoomParticipationStatus. Reserved bytes remain explicit, not C strings.
"""

from typing import Annotated

import dataclasses_struct as dcs


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ResultsRecord:
    """Twelve-byte counters common to both profile detail layouts."""
    wins: dcs.U16
    losses: dcs.U16
    draws: dcs.U16
    streak_current: dcs.U16
    streak_best: dcs.U16
    disconnects: dcs.U16


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5ProfileRecord:
    """57-byte PES5 profile detail, without the four-byte response status."""
    profile_id: dcs.I32
    name: Annotated[bytes, 16]
    division: dcs.U8
    points: dcs.I32
    games: dcs.U16
    results: ResultsRecord
    # TODO(0x4103): each reserved pair + the u16 after it is one i32 in the client
    # parser; see the TODO above encode_pes5_profile in handlers/profile_info.py.
    reserved_scored: Annotated[bytes, 2]
    goals_scored: dcs.U16
    reserved_allowed: Annotated[bytes, 2]
    goals_allowed: dcs.U16
    favourite_team: dcs.U16
    favourite_player: dcs.I32
    rank: dcs.I32


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes6ProfileRecord:
    """418-byte PES6 detail with five recent team IDs at offset 408."""
    profile_id: dcs.I32
    name: Annotated[bytes, 48]
    group_id: dcs.I32
    group_name: Annotated[bytes, 48]
    member_status: dcs.U8
    division: dcs.U8
    points: dcs.I32
    rating: dcs.U16
    games: dcs.U16
    results: ResultsRecord
    goals_scored: dcs.I32
    goals_allowed: dcs.I32
    comment: Annotated[bytes, 256]
    rank: dcs.I32
    medals_language_reserved: Annotated[bytes, 14]
    recent_teams: Annotated[list[dcs.U16], 5]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class RoomPlayerRecord:
    """Ten-byte PES6 room slot, including the unassigned-side sentinel."""
    profile_id: dcs.I32
    owner: dcs.U8
    match_starter: dcs.U8
    side: dcs.U8
    spectator: dcs.U8
    position: dcs.U8
    participation: dcs.U8


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class TeamGoalsRecord:
    """Seven-byte team ID and five per-period goal counts."""
    team_id: dcs.U16
    goals: Annotated[list[dcs.U8], 5]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class RoomTeamsRecord:
    """Fourteen-byte home and away team/goal records."""
    home: TeamGoalsRecord
    away: TeamGoalsRecord


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes6RoomRecord:
    """131-byte PES6 room listing/update, with four nested player slots."""
    room_id: dcs.I32
    phase: dcs.U8
    match_state: dcs.U8
    name: Annotated[bytes, 64]
    clock: dcs.U8
    players: Annotated[list[RoomPlayerRecord], 4]
    teams: RoomTeamsRecord
    reserved: dcs.U8
    locked: dcs.U8
    flags: Annotated[bytes, 4]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5RoomRecord:
    """87-byte PES5 room; list/update player regions have different strides."""
    room_id: dcs.I32
    phase: dcs.U8
    locked: dcs.U8
    name: Annotated[bytes, 32]
    match_time_slot: dcs.U8
    players: Annotated[bytes, 48]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ParticipationRecord:
    """Six-byte PES6 participation slot."""
    profile_id: dcs.I32
    position: dcs.U8
    participation: dcs.U8


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ParticipationArray:
    """24-byte four-slot PES6 participation update."""
    players: Annotated[list[ParticipationRecord], 4]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class PeerRecord:
    """43-byte peer endpoint/participation suffix after direction-specific padding."""
    ip1: Annotated[bytes, 16]
    port1: dcs.U16
    ip2: Annotated[bytes, 16]
    port2: dcs.U16
    profile_id: dcs.I32
    reserved: dcs.U16
    participation: dcs.U8


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class PlayerSettingRecord:
    """Eight-byte PES6 side selection; three unknown bytes are preserved."""
    profile_id: dcs.I32
    away: dcs.U8
    reserved: Annotated[bytes, 3]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class PlayerSettingsRecord:
    """32-byte known prefix of PES6 player settings; extensions stay opaque."""
    players: Annotated[list[PlayerSettingRecord], 4]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ChatHeader:
    """Ten-byte variable-chat header with a signed recipient identifier."""
    kind: Annotated[bytes, 2]
    special: Annotated[bytes, 4]
    recipient_id: dcs.I32


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5RoomCreateHeader:
    """34-byte PES5 room-creation prefix, before the optional password."""
    name: Annotated[bytes, 32]
    # TODO(0x4310): the pes5.exe builder (0x0083bda0, called from 0x004a5aa0)
    # always writes 1 here; locked is 1 when a password was typed.
    reserved: dcs.U8
    locked: dcs.U8


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes6RoomCreateHeader:
    """65-byte PES6 room-creation prefix, before the optional password."""
    name: Annotated[bytes, 64]
    locked: dcs.U8


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class LoginProfileState:
    """Nine-byte play-time, division and points suffix in login listings."""
    seconds_played: dcs.I32
    division: dcs.U8
    points: dcs.I32


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5LoginProfile:
    """32-byte PES5 login-list profile row."""
    index: dcs.U8
    profile_id: dcs.I32
    name: Annotated[bytes, 16]
    state: LoginProfileState
    games: dcs.U16


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes6LoginProfile:
    """66-byte PES6 login-list profile row."""
    index: dcs.U8
    profile_id: dcs.I32
    name: Annotated[bytes, 48]
    state: LoginProfileState
    rating: dcs.U16
    games: dcs.U16


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5PlayerRecord:
    """31-byte PES5 lobby-presence record."""
    profile_id: dcs.I32
    name: Annotated[bytes, 16]
    in_room: dcs.U8
    room_id: dcs.I32
    no_lobby_chat: dcs.I32
    # TODO(0x4212): u8 bMemberStatus + u8 status2, the first one picks the chat
    # colour; see the TODO above encode_pes5_player in handlers/lobbies.py.
    reserved: Annotated[bytes, 2]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes6PlayerRecord:
    """127-byte PES6 lobby-presence record; not the menu profile layout."""
    profile_id: dcs.I32
    name: Annotated[bytes, 48]
    group: Annotated[bytes, 52]
    reserved: dcs.U8
    division: dcs.U8
    room_id: dcs.I32
    points: dcs.I32
    rating: dcs.U16
    games: dcs.U16
    wins: dcs.U16
    losses: dcs.U16
    draws: dcs.U16
    flags: Annotated[bytes, 3]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ReadyUpdateRecord:
    """Five-byte profile/ready-state broadcast."""
    profile_id: dcs.I32
    state: dcs.U8


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class KickoffRecord:
    """37-byte PES6 kickoff: phase byte and padded participant-ID region."""
    phase: dcs.U8
    participants: Annotated[bytes, 36]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class PostMatchPlayerRecord:
    """22-byte PES6 post-match row; unimplemented points/rating stay zero."""
    profile_id: dcs.I32
    points_rating_reserved: Annotated[bytes, 18]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class PingRecord:
    """44-byte successful peer lookup with two UDP endpoints."""
    status: dcs.U32
    ip1: Annotated[bytes, 16]
    port1: dcs.U16
    ip2: Annotated[bytes, 16]
    port2: dcs.U16
    profile_id: dcs.I32


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5ChatPrefix:
    """25-byte PES5 outgoing chat prefix; message text is variable length."""
    kind: Annotated[bytes, 1]
    special: Annotated[bytes, 4]
    profile_id: dcs.I32
    name: Annotated[bytes, 16]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes6ChatPrefix:
    """58-byte PES6 outgoing chat prefix; kind and name differ from PES5."""
    kind: Annotated[bytes, 2]
    special: Annotated[bytes, 4]
    profile_id: dcs.I32
    name: Annotated[bytes, 48]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class NewsHeaderRecord:
    """89-byte news prefix before variable greeting or fixed 512-byte text."""
    status: dcs.U32
    flags: Annotated[bytes, 2]
    date: Annotated[bytes, 19]
    title: Annotated[bytes, 64]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5RoomPeerNames:
    """48-byte PES5 join/leave notification containing player and room names."""
    player_name: Annotated[bytes, 16]
    room_name: Annotated[bytes, 32]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes5ChallengeRecord:
    """90-byte challenge: padded profile field, ping byte and reserved trailer."""
    profile: Annotated[bytes, 87]
    ping: Annotated[bytes, 1]
    reserved: Annotated[bytes, 2]


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class Pes6RoomRenameHeader:
    """65-byte rename prefix; unlike creation, byte 63 is not a name byte."""
    name: Annotated[bytes, 63]
    reserved: dcs.U8
    locked: dcs.U8
