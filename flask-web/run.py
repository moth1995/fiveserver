"""Entry point: reads WebInterface config from fiveserver.yaml.

WebInterface.useSecure controls whether TLS is enabled:
  false  → plain HTTP on WebInterface.port only
  true   → HTTP redirect on WebInterface.port + HTTPS on WebInterface.securePort

Environment overrides (all optional):
  HTTP_PORT      plain-HTTP port
  HTTPS_PORT     HTTPS port
  CERT_FILE      path to TLS certificate
  KEY_FILE       path to TLS private key
"""

from __future__ import annotations

import os
import sys
import threading

# Allow running from the repo root: python flask-web/run.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, redirect, request
from werkzeug.serving import make_server

from app import create_app
from config import load_config


def _make_redirect_app(https_port: int) -> Flask:
    redirect_app = Flask("redirect_app")

    @redirect_app.route("/", defaults={"path": ""})
    @redirect_app.route("/<path:path>")
    def _do_redirect(path: str) -> object:
        host = request.host.split(":")[0]
        dest = f"https://{host}"
        if https_port != 443:
            dest += f":{https_port}"
        dest += request.full_path.rstrip("?")
        return redirect(dest, code=301)

    return redirect_app


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Read ports and TLS toggle from fiveserver.yaml (env vars take precedence)
    cfg = load_config(
        os.path.join(repo_root, "etc", "conf", "fiveserver.yaml"),
        os.path.join(repo_root, "etc", "conf", "admin.yaml"),
    )
    web_cfg: dict = cfg.get("WebInterface", {}) or {}

    http_port: int = int(os.environ.get("HTTP_PORT", web_cfg.get("port", 80)))
    https_port: int = int(os.environ.get("HTTPS_PORT", web_cfg.get("securePort", 443)))
    use_secure: bool = bool(web_cfg.get("useSecure", False))

    cert_file: str = os.environ.get(
        "CERT_FILE", os.path.join(repo_root, "etc", "keys", "servercert.pem")
    )
    key_file: str = os.environ.get(
        "KEY_FILE", os.path.join(repo_root, "etc", "keys", "serverkey.pem")
    )

    flask_app = create_app()

    if not use_secure:
        server = make_server("0.0.0.0", http_port, flask_app)
        print(f"[fiveserver-web] HTTP on :{http_port} (useSecure=false)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return

    # TLS mode
    ssl_context: tuple[str, str] | None = None
    if os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_context = (cert_file, key_file)
    else:
        print(
            f"[warn] TLS cert/key not found at {cert_file} / {key_file}. "
            "Running without TLS (development only)."
        )

    https_server = make_server(
        "0.0.0.0", https_port, flask_app, ssl_context=ssl_context
    )
    redirect_app = _make_redirect_app(https_port)
    http_server = make_server("0.0.0.0", http_port, redirect_app)

    print(f"[fiveserver-web] HTTP redirect on :{http_port}")
    print(f"[fiveserver-web] HTTPS app on :{https_port}")

    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()

    try:
        https_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http_server.shutdown()


if __name__ == "__main__":
    main()
