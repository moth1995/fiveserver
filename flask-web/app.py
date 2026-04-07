from __future__ import annotations

from flask import Flask


def create_app() -> Flask:
    """Application factory."""
    app = Flask(__name__)
    return app
