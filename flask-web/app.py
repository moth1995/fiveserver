"""Flask application factory for the fiveserver web layer."""
from __future__ import annotations

import os
import secrets

from flask import Flask

from config import load_config, make_fast_banned_list
from db import teardown_db


def create_app(
    config_path: str | None = None,
    admin_config_path: str | None = None,
) -> Flask:
    """Create and configure the Flask application.

    config_path and admin_config_path default to the repo's etc/conf/ files.
    Pass explicit paths in tests to avoid touching the real config.
    """
    app = Flask(__name__)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_config(
        config_path or os.path.join(repo_root, 'etc', 'conf', 'fiveserver.yaml'),
        admin_config_path or os.path.join(repo_root, 'etc', 'conf', 'admin.yaml'),
    )
    app.config['FS_CONFIG'] = cfg
    app.config['ADMIN_USER'] = os.environ.get('ADMIN_USER', cfg.get('AdminUser', 'fives'))
    app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', cfg.get('AdminPassword', 'fives'))

    # Banned IP list for registration endpoint
    banned_specs: list[str] = []
    try:
        import yaml as _yaml  # local import to avoid polluting namespace
        banned_file: str = cfg.get('BannedList', '')
        if banned_file:
            if not os.path.isabs(banned_file):
                banned_file = os.path.join(repo_root, banned_file)
            if os.path.exists(banned_file):
                with open(banned_file, encoding='utf-8') as f:
                    banned_data = _yaml.safe_load(f) or {}
                banned_specs = banned_data.get('Banned', [])
    except Exception:
        pass
    app.config['BANNED_LIST'] = make_fast_banned_list(banned_specs)

    # Flask secret key (for sessions / CSRF in Phase 2)
    app.secret_key = os.environ.get('FLASK_SECRET', cfg.get('FlaskSecretKey', secrets.token_hex(32)))

    # ------------------------------------------------------------------
    # Template filters
    # ------------------------------------------------------------------
    @app.template_filter('format_duration')
    def _format_duration(seconds: int) -> str:
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f'{h}h {m}m {s}s'
        if m:
            return f'{m}m {s}s'
        return f'{s}s'

    # ------------------------------------------------------------------
    # DB teardown
    # ------------------------------------------------------------------
    app.teardown_appcontext(teardown_db)

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    from blueprints.register import register_bp
    from blueprints.admin import admin_bp
    from blueprints.stats import stats_bp

    app.register_blueprint(register_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(stats_bp)

    return app
