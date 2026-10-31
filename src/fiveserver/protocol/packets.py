"""Typed request packets with feature-local binary codecs."""


from dataclasses import dataclass

import dataclasses_struct as dcs

from .records import ChatHeader, Pes5RoomCreateHeader, Pes6RoomCreateHeader


PES5_ROOM_NAME_SIZE = 32
PES5_ROOM_FLAG_OFFSET = 33
PES5_ROOM_PASSWORD_OFFSET = 34
PES5_ROOM_PASSWORD_SIZE = 16
PES6_ROOM_NAME_SIZE = 64
PES6_ROOM_FLAG_OFFSET = 64
PES6_ROOM_PASSWORD_OFFSET = 65
PES6_ROOM_PASSWORD_SIZE = 15
ROOM_JOIN_MIN_SIZE = 4
ROOM_JOIN_PASSWORD_OFFSET = 4
ROOM_JOIN_PASSWORD_SIZE = 15
CHAT_HEADER_SIZE = 10
CHAT_RECIPIENT_OFFSET = 6
PROFILE_ID_SIZE = 4
TEAM_ID_SIZE = 2


@dataclass(frozen=True)
class RoomCreateRequest:
    """Decoded PES5/PES6 room name, lock flag, and optional password."""
    name: bytes
    locked: bool
    password: bytes | None


@dataclass(frozen=True)
class RoomJoinRequest:
    """Decoded PES6 room ID and password."""
    room_id: int
    password: bytes


@dataclass(frozen=True)
class ChatRequest:
    """Decoded chat kind, special field, recipient, and text."""
    kind: bytes
    special: bytes
    recipient_id: int | None
    message: bytes


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class ProfileIdRequest:
    """Decoded four-byte profile identifier."""
    profile_id: dcs.I32


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class TeamSelectRequest:
    """Decoded two-byte team identifier."""
    team_id: dcs.U16


def _c_string(value: bytes) -> bytes:
    return value.split(b"\0", 1)[0]


def decode_pes5_room_create(payload: bytes) -> RoomCreateRequest:
    """Decode a PES5 creation prefix and optional 16-byte password."""
    if len(payload) < PES5_ROOM_PASSWORD_OFFSET:
        raise ValueError("Short PES5 room creation packet")
    header = Pes5RoomCreateHeader.from_packed(payload[:PES5_ROOM_PASSWORD_OFFSET])
    locked = header.locked == 1
    password = _c_string(payload[PES5_ROOM_PASSWORD_OFFSET:PES5_ROOM_PASSWORD_OFFSET + PES5_ROOM_PASSWORD_SIZE]) if locked else None
    return RoomCreateRequest(_c_string(header.name), locked, password)


def decode_pes6_room_create(payload: bytes) -> RoomCreateRequest:
    """Decode a PES6 creation prefix and optional 15-byte password."""
    if len(payload) < PES6_ROOM_PASSWORD_OFFSET:
        raise ValueError("Short PES6 room creation packet")
    header = Pes6RoomCreateHeader.from_packed(payload[:PES6_ROOM_PASSWORD_OFFSET])
    locked = header.locked == 1
    password = _c_string(payload[PES6_ROOM_PASSWORD_OFFSET:PES6_ROOM_PASSWORD_OFFSET + PES6_ROOM_PASSWORD_SIZE]) if locked else None
    return RoomCreateRequest(_c_string(header.name), locked, password)


def decode_room_join(payload: bytes) -> RoomJoinRequest:
    """Decode a PES6 room ID and optional password."""
    if len(payload) < ROOM_JOIN_MIN_SIZE:
        raise ValueError("Short room join request")
    room_id = ProfileIdRequest.from_packed(payload[:ROOM_JOIN_MIN_SIZE]).profile_id
    password = _c_string(payload[ROOM_JOIN_PASSWORD_OFFSET:ROOM_JOIN_PASSWORD_OFFSET + ROOM_JOIN_PASSWORD_SIZE])
    return RoomJoinRequest(room_id, password)


def decode_chat(payload: bytes) -> ChatRequest:
    """Decode the 10-byte chat header and null-terminated message."""
    if len(payload) < CHAT_HEADER_SIZE:
        raise ValueError("Invalid chat packet")
    header = ChatHeader.from_packed(payload[:CHAT_HEADER_SIZE])
    recipient_id = header.recipient_id if header.kind == b"\x00\x02" else None
    return ChatRequest(header.kind, header.special, recipient_id, _c_string(payload[CHAT_HEADER_SIZE:]))


def decode_profile_id(payload: bytes) -> ProfileIdRequest:
    """Decode a signed 32-bit profile identifier."""
    if len(payload) < PROFILE_ID_SIZE:
        raise ValueError("Profile ID requires four bytes")
    return ProfileIdRequest.from_packed(payload[:PROFILE_ID_SIZE])


def decode_team_select(payload: bytes) -> TeamSelectRequest:
    """Decode an unsigned 16-bit team identifier."""
    if len(payload) < TEAM_ID_SIZE:
        raise ValueError("Team selection requires two bytes")
    return TeamSelectRequest.from_packed(payload[:TEAM_ID_SIZE])
