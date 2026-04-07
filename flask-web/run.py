"""Entry point: HTTP on port 80 (redirect) + HTTPS on port 443 (full app).

Environment overrides:
  HTTP_PORT      plain-HTTP redirect port  (default 80)
  HTTPS_PORT     HTTPS application port    (default 443)
  CERT_FILE      path to TLS certificate   (default etc/keys/servercert.pem)
  KEY_FILE       path to TLS private key   (default etc/keys/serverkey.pem)
"""
from __future__ import annotations

import os
import threading

from flask import Flask, redirect, request
from werkzeug.serving import make_server

from app import create_app


# ---------------------------------------------------------------------------
# Minimal HTTP redirect app
# ---------------------------------------------------------------------------

def _make_redirect_app(https_port: int) -> Flask:
    """Return a tiny Flask app that redirects all requests to HTTPS."""
    redirect_app = Flask('redirect_app')

    @redirect_app.route('/', defaults={'path': ''})
    @redirect_app.route('/<path:path>')
    def _do_redirect(path: str) -> object:
        host = request.host.split(':')[0]  # strip any port
        dest = f'https://{host}'
        if https_port != 443:
            dest += f':{https_port}'
        dest += request.full_path.rstrip('?')
        return redirect(dest, code=301)

    return redirect_app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    http_port: int = int(os.environ.get('HTTP_PORT', 80))
    https_port: int = int(os.environ.get('HTTPS_PORT', 443))

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cert_file: str = os.environ.get(
        'CERT_FILE',
        os.path.join(repo_root, 'etc', 'keys', 'servercert.pem'),
    )
    key_file: str = os.environ.get(
        'KEY_FILE',
        os.path.join(repo_root, 'etc', 'keys', 'serverkey.pem'),
    )

    flask_app = create_app()

    # HTTPS server (full application)
    ssl_context: tuple[str, str] | None = None
    if os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_context = (cert_file, key_file)
    else:
        print(
            f'[warn] TLS cert/key not found at {cert_file} / {key_file}. '
            'Running HTTPS without TLS (development only).'
        )

    https_server = make_server(
        '0.0.0.0',
        https_port,
        flask_app,
        ssl_context=ssl_context,
    )

    # HTTP redirect server
    redirect_app = _make_redirect_app(https_port)
    http_server = make_server('0.0.0.0', http_port, redirect_app)

    print(f'[fiveserver-web] HTTP redirect on :{http_port}')
    print(f'[fiveserver-web] HTTPS app on :{https_port}')

    # Run redirect server in a daemon thread; HTTPS server in main thread.
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()

    try:
        https_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http_server.shutdown()


if __name__ == '__main__':
    main()
