from __future__ import annotations

from flask import Blueprint

# Stats blueprint — implemented in step-08
stats_bp = Blueprint('stats', __name__, url_prefix='/stats')
