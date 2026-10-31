"""Fixed-layout and encrypted protocol codecs shared by handlers."""


import binascii
from dataclasses import dataclass
from typing import Annotated

from Crypto.Cipher import Blowfish
import dataclasses_struct as dcs


# The supplied log proves 48/64-byte PES5 authentication. PES6's [58:74]
# roster offset implies a larger structure, but no verified PES6 payload exists;
# those observed lengths are accepted and a truncated roster is treated as absent.
AUTH_REQUEST_LENGTHS = frozenset((48, 64))
PES5_AUTH_ROSTER_START = 48
PES6_AUTH_ROSTER_START = 58
USER_HASH_START = 32
USER_HASH_END = 48
ROSTER_SIZE = 16
MATCH_DATE_SIZE = 19
PES5_NAME_SIZE = 16
CIPHER_KEY = bytes.fromhex(
    "27501fd04e6b82c831024dac5c6305221974deb9388a2190"
    "1d576cbbe2f377ef23d75486010f37819afe6c321a0146d2"
    "1544ec365bf7289a"
)


@dcs.dataclass_struct(size="std", byteorder="big", frozen=True)
class MatchResultRecord:
    """Fixed 46-byte PES5 match-result row inside 0x3072."""
    # TODO(0x3072): the pes5.exe parser (NetParseLast10MatchResults_0083df90)
    # reads this u8 first and the rest exactly as below (46 bytes, up to 10 rows).
    # UNUSED: FUN_004a3940 copies each row to a table at 0x00fb3048 (stride
    # 0x48), and the match-card builder FUN_004b96d0 reads only date, own team,
    # own score, opponent score, opponent team, opponent name and opponent id
    # through accessors 0x00477520..0x00477650; nothing reads table offset 0.
    # Win / draw / loss is computed from the two scores. Keep 0.
    unknown_zero: dcs.U8
    played_on: Annotated[bytes, MATCH_DATE_SIZE]
    opponent_name: Annotated[bytes, PES5_NAME_SIZE]
    opponent_id: dcs.I32
    my_score: dcs.U8
    opponent_score: dcs.U8
    my_team_id: dcs.U16
    opponent_team_id: dcs.U16


@dataclass(frozen=True)
class AuthenticationRequest:
    """Decoded credential hash and optional roster hash."""
    user_hash: str
    roster_hash: bytes | None


def decode_auth(payload: bytes, *, roster_start: int) -> AuthenticationRequest:
    """Decode a Blowfish-ECB encrypted 48- or 64-byte authentication payload.

    The user credential hash is taken from the raw encrypted bytes at
    ``[32:48]`` and hex-encoded without decryption, matching the original
    authentication service. Roster bytes are read from the corresponding
    game-specific offset in the decrypted payload; short slices are treated as
    an absent optional roster field.

    Raises:
        ValueError: If the payload length is not one of the verified layouts.
    """
    if len(payload) not in AUTH_REQUEST_LENGTHS:
        raise ValueError("Invalid authentication payload length")
    user_hash = binascii.hexlify(payload[USER_HASH_START:USER_HASH_END]).decode("ascii")
    decrypted = Blowfish.new(CIPHER_KEY, Blowfish.MODE_ECB).decrypt(payload)
    roster = decrypted[roster_start : roster_start + ROSTER_SIZE]
    return AuthenticationRequest(user_hash, roster if len(roster) == ROSTER_SIZE else None)


def decode_pes5_auth(payload: bytes) -> AuthenticationRequest:
    """Decode PES5-family authentication with the roster at decrypted ``[48:64]``."""
    return decode_auth(payload, roster_start=PES5_AUTH_ROSTER_START)


def decode_pes6_auth(payload: bytes) -> AuthenticationRequest:
    """Decode PES6-family authentication with the roster at decrypted ``[58:74]``.

    The original PES6 source uses this offset even though no verified 80-byte
    payload is present in the supplied logs; truncated slices remain optional.
    """
    return decode_auth(payload, roster_start=PES6_AUTH_ROSTER_START)
