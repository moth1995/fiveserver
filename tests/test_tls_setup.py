"""Verify admin TLS validation and the test-only certificate helper."""

import pytest

from fiveserver.server import _validate_admin_tls
from tls_helpers import generate_pair


@pytest.mark.parametrize("partial", [False, True])
def test_server_reports_first_run_guidance_for_missing_tls_files(tmp_path, partial):
    if partial:
        (tmp_path / "serverkey.pem").write_bytes(b"synthetic file")
    with pytest.raises(FileNotFoundError, match="FIRST-RUN HTTPS SETUP") as error:
        _validate_admin_tls({"KeysDirectory": str(tmp_path)})
    assert "servercert.pem" in str(error.value) and "README" in str(error.value)


def test_complete_pair_passes_admin_tls_validation(tmp_path):
    generate_pair(tmp_path)
    _validate_admin_tls({"KeysDirectory": str(tmp_path)})
