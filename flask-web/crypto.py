"""Cryptographic helpers for the fiveserver web layer (Blowfish password hashing)."""

from __future__ import annotations

import binascii

from Crypto.Cipher import Blowfish

# Cipher key — hardcoded in FiveServerConfig (lib/fiveserver/config.py line 163)
_CIPHER_KEY_HEX: str = (
    "27501fd04e6b82c831024dac5c6305221974deb9388a2190"
    "1d576cbbe2f377ef23d75486010f37819afe6c321a0146d2"
    "1544ec365bf7289a"
)
_CIPHER_KEY: bytes = binascii.a2b_hex(_CIPHER_KEY_HEX)


def blowfish_encrypt(hex_hash: str) -> str:
    """
    Encrypt a 32-char hex MD5 string with Blowfish ECB.
    Mirrors register.py line 137:
      hash = binascii.b2a_hex(cipher.encrypt(binascii.a2b_hex(hash)))
    Returns a 32-char hex string.
    """
    cipher = Blowfish.new(_CIPHER_KEY, Blowfish.MODE_ECB)
    raw: bytes = binascii.a2b_hex(hex_hash)
    encrypted: bytes = cipher.encrypt(raw)
    return binascii.b2a_hex(encrypted).decode("ascii")


def blowfish_decrypt(hex_encrypted: str) -> str:
    """
    Inverse of blowfish_encrypt. Used for round-trip tests only.
    Returns a 32-char hex string.
    """
    cipher = Blowfish.new(_CIPHER_KEY, Blowfish.MODE_ECB)
    raw: bytes = binascii.a2b_hex(hex_encrypted)
    decrypted: bytes = cipher.decrypt(raw)
    return binascii.b2a_hex(decrypted).decode("ascii")
