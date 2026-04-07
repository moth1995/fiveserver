Implement crypto helpers in flask-web/crypto.py.

Read first:
- lib/fiveserver/register.py   (blowfish encrypt/decrypt usage)
- lib/fiveserver/config.py     (makeFastBannedList, isBanned logic)
- etc/conf/fiveserver.yaml     (CipherKey value)

Create branch `web-flask/step-03-crypto` from `web-flask` before making any changes.

## What to implement

`flask-web/crypto.py` — pure Python, no Flask dependency, fully statically typed.

### Blowfish

```python
CIPHER_KEY: bytes  # decoded from the 96-hex-char CipherKey in fiveserver.yaml

def blowfish_encrypt(hex_hash: str) -> str:
    """Encrypt a 32-char hex MD5 string with Blowfish ECB. Returns hex string."""

def blowfish_decrypt(hex_encrypted: str) -> str:
    """Inverse of blowfish_encrypt. Used in tests for round-trip verification."""
```

Use `from Crypto.Cipher import Blowfish` (pycryptodome). Mode: `Blowfish.MODE_ECB`.
The input is a 32-char hex string (16 bytes). The output is a 32-char hex string.

### IP ban list

```python
def make_fast_banned_list(banned_specs: list[str]) -> list[tuple[int, int]]:
    """
    Convert a list of IP/network specs (e.g. ['192.168.1.0/24', '10.0.0.1'])
    into (masked_ip_int, mask_int) tuples for fast O(n) checking.
    Port makeFastBannedList from lib/fiveserver/config.py exactly.
    """

def is_banned(ip: str, fast_list: list[tuple[int, int]]) -> bool:
    """Return True if ip matches any entry in fast_list."""
```

## Type annotations

`from __future__ import annotations` at top. All functions fully annotated.

## Unit tests

In `flask-web/tests/test_crypto.py` (new file), `unittest.TestCase` with:
1. `test_blowfish_round_trip`: assert `blowfish_decrypt(blowfish_encrypt(known_hex)) == known_hex`
2. `test_blowfish_known_value`: use a hardcoded input/output pair derived by running the Twisted `register.py` logic manually — this is the critical correctness test
3. `test_is_banned_cidr`: assert `is_banned('192.168.1.5', make_fast_banned_list(['192.168.1.0/24']))` is True
4. `test_is_not_banned`: assert `is_banned('10.0.0.1', make_fast_banned_list(['192.168.1.0/24']))` is False

## Verification

`python -m unittest flask-web/tests/test_crypto.py` — all tests pass, especially the known-value test.

After verification, merge `web-flask/step-03-crypto` → `web-flask`.
