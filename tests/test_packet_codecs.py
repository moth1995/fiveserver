import struct

import pytest

from fiveserver.protocol.packets import (
    decode_chat, decode_profile_id, decode_pes5_room_create, decode_pes6_room_create, decode_room_join, decode_team_select,
)


def test_room_create_codecs_preserve_padding_and_optional_password():
    five = b"room".ljust(32, b"\0") + b"\0" + b"\x01" + b"secret".ljust(16, b"\0") + bytes(30)
    request = decode_pes5_room_create(five)
    assert request == decode_pes5_room_create(five[:50])
    assert (request.name, request.locked, request.password) == (b"room", True, b"secret")

    six = b"six".ljust(64, b"\0") + b"\x01" + b"pass".ljust(15, b"\0") + bytes(10)
    request = decode_pes6_room_create(six)
    assert (request.name, request.locked, request.password) == (b"six", True, b"pass")

    with pytest.raises(ValueError):
        decode_pes5_room_create(b"short")
    with pytest.raises(ValueError):
        decode_pes6_room_create(b"short")


def test_room_join_and_simple_typed_codecs():
    join = decode_room_join(struct.pack("!i", 9) + b"pw".ljust(15, b"\0"))
    assert (join.room_id, join.password) == (9, b"pw")
    assert decode_profile_id(struct.pack("!i", -4)).profile_id == -4
    assert decode_team_select(struct.pack("!H", 123)).team_id == 123
    with pytest.raises(ValueError):
        decode_room_join(b"\0\0")
    with pytest.raises(ValueError):
        decode_profile_id(b"\0")
    with pytest.raises(ValueError):
        decode_team_select(b"\0")


def test_chat_codec_extracts_kind_special_recipient_and_message():
    private = b"\x00\x02" + b"\x01\x02\x03\x04" + struct.pack("!i", 55) + b"hello\x00tail"
    request = decode_chat(private)
    assert (request.kind, request.special, request.recipient_id, request.message) == (
        b"\x00\x02", b"\x01\x02\x03\x04", 55, b"hello",
    )
    lobby = b"\x00\x01" + bytes(4) + bytes(4) + b"hi\x00"
    assert decode_chat(lobby).recipient_id is None
    with pytest.raises(ValueError):
        decode_chat(b"short")
