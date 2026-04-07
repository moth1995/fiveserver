from __future__ import annotations

from flask import Blueprint

# Admin blueprint — implemented in step-06
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
