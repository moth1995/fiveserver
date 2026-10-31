"""Server address resolution shared by startup and the admin panel."""

import asyncio
import logging
import os
import socket
import urllib.request
from typing import Any


LOGGER = logging.getLogger(__name__)


async def resolve_server_ip(settings: dict[str, Any]) -> str:
    """Resolve the public server IP from env, YAML, the detector, or local hostname."""
    override = os.getenv("FIVESERVER_PUBLIC_IP")
    if override:
        return override
    configured = str(settings.get("ServerIP", "auto"))
    if configured.lower() != "auto":
        return configured
    uri = str(settings.get("IpDetectUri", ""))
    if uri:
        try:
            def fetch() -> str:
                with urllib.request.urlopen(uri, timeout=5) as response:
                    return response.read(64).decode("ascii").strip()

            return await asyncio.to_thread(fetch)
        except (OSError, UnicodeError, ValueError):
            LOGGER.warning("Server IP detection failed; using local hostname")
    return socket.gethostbyname(socket.gethostname())
