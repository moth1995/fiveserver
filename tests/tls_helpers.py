"""Test-only helpers for creating temporary TLS certificates with host OpenSSL."""

import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys

import pytest


def openssl() -> str:
    executable = shutil.which("openssl")
    git_openssl = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/usr/bin/openssl.exe"
    if executable is None and sys.platform == "win32" and git_openssl.is_file():
        executable = str(git_openssl)
    if executable is None:
        pytest.skip("OpenSSL is required for certificate tests")
    return executable


def generate_pair(directory: Path, *, days: int = 1) -> tuple[Path, Path]:
    """Create a temporary localhost key/certificate pair for tests."""
    directory.mkdir(parents=True, exist_ok=True)
    key, certificate = directory / "serverkey.pem", directory / "servercert.pem"
    result = subprocess.run([
        openssl(), "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-days", str(days), "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-keyout", str(key), "-out", str(certificate),
    ], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"OpenSSL certificate generation failed: {result.stderr.strip()}")
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(certificate, key)
    return key, certificate
