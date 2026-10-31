"""PES TCP framing, using the original XOR and decoded-packet MD5 rules."""


import asyncio
import hashlib
import hmac
from dataclasses import dataclass

import dataclasses_struct as dcs


HEADER_SIZE = 8
DIGEST_SIZE = 16
MAX_PAYLOAD_SIZE = 0xFFFF  # Header length is an unsigned 16-bit field.
XOR_KEY = bytes.fromhex("a677957c")
HEARTBEAT_ID = 0x0005


class InvalidPacket(ValueError):
    """Raised when a frame is truncated, oversized, or fails integrity checks."""
    pass


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class PacketHeader:
    """Big-endian 8-byte packet ID, payload length, and counter."""
    packet_id: dcs.U16
    payload_length: dcs.U16
    counter: dcs.U32


@dataclass(frozen=True)
class Packet:
    """Decoded header plus payload bytes."""
    header: PacketHeader
    payload: bytes


def xor_data(data: bytes, offset: int = 0) -> bytes:
    """Apply the original repeating XOR key, optionally starting at an offset."""
    return bytes(value ^ XOR_KEY[(offset + index) % len(XOR_KEY)] for index, value in enumerate(data))


def encode_packet(packet_id: int, payload: bytes, counter: int) -> bytes:
    """Build the original 8-byte header, 16-byte MD5 digest, payload, and XOR envelope.

    The digest covers the header and payload before XOR. The first send counter
    is 1 because clients do not expect a zero sequence number.
    """
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise InvalidPacket("Payload exceeds the header length field")
    header = PacketHeader(packet_id, len(payload), counter).pack()
    decoded = header + hashlib.md5(header + payload).digest() + payload
    return xor_data(decoded)


def decode_packet(data: bytes) -> Packet:
    """Verify and decode one complete XORed game frame.

    Raises:
        InvalidPacket: If the frame is truncated, the declared length differs
            from the received length, or the MD5 digest does not match.
    """
    if len(data) < HEADER_SIZE + DIGEST_SIZE:
        raise InvalidPacket("Truncated packet")
    decoded = xor_data(data)
    header = PacketHeader.from_packed(decoded[:HEADER_SIZE])
    if len(decoded) != HEADER_SIZE + DIGEST_SIZE + header.payload_length:
        raise InvalidPacket("Packet length mismatch")
    payload = decoded[HEADER_SIZE + DIGEST_SIZE :]
    expected = hashlib.md5(decoded[:HEADER_SIZE] + payload).digest()
    if not hmac.compare_digest(decoded[HEADER_SIZE : HEADER_SIZE + DIGEST_SIZE], expected):
        raise InvalidPacket("Wrong MD5 checksum")
    return Packet(header, payload)


async def read_packet(reader: asyncio.StreamReader) -> Packet:
    """Read one complete frame from an asyncio stream.

    TCP may split a frame or combine several frames in one read; reads here
    remain frame-bounded and preserve the original 24-byte header overhead.
    """
    header_wire = await reader.readexactly(HEADER_SIZE)
    header = PacketHeader.from_packed(xor_data(header_wire))
    if header.payload_length > MAX_PAYLOAD_SIZE:
        raise InvalidPacket("Payload too large")
    rest = await reader.readexactly(DIGEST_SIZE + header.payload_length)
    return decode_packet(header_wire + rest)
