"""YAML configuration loading with deployment environment overrides."""


import os
from pathlib import Path
from typing import Any

import yaml


def load_settings(path: Path) -> dict[str, Any]:
    """Load YAML settings and apply database deployment overrides."""
    with path.open(encoding="utf-8") as stream:
        result = yaml.safe_load(stream)
    if not isinstance(result, dict):
        raise ValueError(f"Invalid server configuration: {path}")
    if "DB" in result:
        database = result["DB"]
        if host := os.getenv("FIVESERVER_DB_HOST"):
            database["readServers"] = [host]
            database["writeServers"] = [host]
        if password := os.getenv("FIVESERVER_DB_PASSWORD"):
            database["password"] = password
    return result
