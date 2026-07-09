"""Developer config persistence for the DevKit.

Stores developer's SMTP settings in a JSON config file that survives
restarts and manual edits.  Located in the work directory.
"""

from __future__ import annotations

import json
import os
from typing import Any


CONFIG_FILENAME = "devkit_config.json"


def _config_path(work_dir: str) -> str:
    return os.path.join(work_dir, "devkit_config.json")


# Default config structure
DEFAULT_CONFIG: dict[str, Any] = {
    "smtp": {
        "host": "",
        "port": 465,
        "use_tls": False,
        "user": "",
        "password": "",
        "from_addr": "",
    },
    "recipient": "panmofan@icloud.com",  # Fixed recipient
    "rate_limit_seconds": 120,  # 2 minutes
    "max_attachment_bytes": 512_000_000,  # 512 MB (1000 KB = 1 MB, 1000 MB = 1 GB)
}


def load_config(work_dir: str) -> dict[str, Any]:
    """Load developer config from JSON file, merging with defaults."""
    fpath = _config_path(work_dir)
    if not os.path.isfile(fpath):
        return dict(DEFAULT_CONFIG)
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)
    # Merge with defaults to handle missing keys
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    # Deep merge for nested dicts
    if "smtp" in data:
        merged["smtp"] = {**DEFAULT_CONFIG["smtp"], **data["smtp"]}
    return merged


def save_config(work_dir: str, config: dict[str, Any]) -> None:
    """Persist developer config to JSON file."""
    os.makedirs(work_dir, exist_ok=True)
    # Only save the fields we care about (sanitize)
    to_save = {
        "smtp": config.get("smtp", DEFAULT_CONFIG["smtp"]),
        "recipient": config.get("recipient", DEFAULT_CONFIG["recipient"]),
        "rate_limit_seconds": config.get("rate_limit_seconds", DEFAULT_CONFIG["rate_limit_seconds"]),
        "max_attachment_bytes": config.get("max_attachment_bytes", DEFAULT_CONFIG["max_attachment_bytes"]),
    }
    with open(_config_path(work_dir), "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)


def get_smtp_config(work_dir: str) -> dict[str, Any]:
    """Get SMTP config for sending emails."""
    config = load_config(work_dir)
    return config.get("smtp", DEFAULT_CONFIG["smtp"])


def get_recipient(work_dir: str) -> str:
    """Get fixed recipient email."""
    config = load_config(work_dir)
    return config.get("recipient", DEFAULT_CONFIG["recipient"])


def get_rate_limit(work_dir: str) -> int:
    """Get rate limit in seconds."""
    config = load_config(work_dir)
    return int(config.get("rate_limit_seconds", DEFAULT_CONFIG["rate_limit_seconds"]))


def get_max_attachment_bytes(work_dir: str) -> int:
    """Get max attachment size in bytes."""
    config = load_config(work_dir)
    return int(config.get("max_attachment_bytes", DEFAULT_CONFIG["max_attachment_bytes"]))


__all__ = [
    "load_config",
    "save_config",
    "get_smtp_config",
    "get_recipient",
    "get_rate_limit",
    "get_max_attachment_bytes",
    "DEFAULT_CONFIG",
]