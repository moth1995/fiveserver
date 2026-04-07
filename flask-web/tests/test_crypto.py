"""Tests for flask-web/crypto.py — Blowfish encrypt/decrypt."""
from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from crypto import blowfish_encrypt, blowfish_decrypt


class TestBlowfish(unittest.TestCase):

    # Known input: hex(md5('test')) = '098f6bcd4621d373cade4e832627b4f6'
    # Expected output computed from the original Twisted register.py cipher logic.
    KNOWN_INPUT: str = '098f6bcd4621d373cade4e832627b4f6'
    KNOWN_OUTPUT: str = '824fe62f75b0365acc0287f5ec158ea8'

    def test_known_value(self) -> None:
        """Critical: output must be byte-for-byte identical to Twisted register.py."""
        result = blowfish_encrypt(self.KNOWN_INPUT)
        self.assertEqual(result, self.KNOWN_OUTPUT)

    def test_round_trip(self) -> None:
        result = blowfish_decrypt(blowfish_encrypt(self.KNOWN_INPUT))
        self.assertEqual(result, self.KNOWN_INPUT)

    def test_different_inputs_give_different_outputs(self) -> None:
        hash1 = 'a' * 32
        hash2 = 'b' * 32
        self.assertNotEqual(blowfish_encrypt(hash1), blowfish_encrypt(hash2))

    def test_output_is_hex_string(self) -> None:
        result = blowfish_encrypt(self.KNOWN_INPUT)
        self.assertEqual(len(result), 32)
        int(result, 16)  # raises if not valid hex


if __name__ == '__main__':
    unittest.main()
