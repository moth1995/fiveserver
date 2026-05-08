"""Shared test utilities."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_FIXTURE_YAML = os.path.join(os.path.dirname(__file__), "fixtures", "fiveserver.yaml")
_ADMIN_FIXTURE_YAML = os.path.join(os.path.dirname(__file__), "fixtures", "admin.yaml")


def create_test_app():
    """Return a Flask test app backed by fixture YAMLs, never the real config."""
    from app import create_app

    app = create_app(config_path=_FIXTURE_YAML, admin_config_path=_ADMIN_FIXTURE_YAML)
    app.config["TESTING"] = True
    return app
