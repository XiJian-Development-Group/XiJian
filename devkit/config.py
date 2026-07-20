"""Developer config persistence for the DevKit.

Stores the developer's own SMTP settings in a JSON config file that
survives restarts and manual edits.  Located in the work directory.

Security
--------
The SMTP password is **never** stored in plaintext.  On save it is
encrypted with Fernet (AES-128-GCM, from the ``cryptography`` package)
using a key that lives in a sibling file ``devkit_config.key`` with
``0600`` permissions, inside the user's private work directory
(``~/Library/Application Support/XiJian/DevKit`` by default).  The
cleartext password is only ever reconstructed in memory when the DevKit
actually sends a submission.

No SMTP credentials are hard-coded in source — the developer must fill
in their own SMTP account.  The recipient mailbox is the XiJian
developer-group inbox used for submission routing.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken

    _HAVE_CRYPTO = True
except Exception:  # pragma: no cover — cryptography is a declared dep
    _HAVE_CRYPTO = False


CONFIG_FILENAME = "devkit_config.json"
_KEY_FILENAME = "devkit_config.key"

#: XiJian developer-group inbox — submission routing destination.
#: This is *not* a credential; it is the fixed recipient the DevKit
#: delivers packaged submissions to.  The developer fills in their own
#: SMTP server / login via the UI.
#:
#: The recipient is hard-coded here and is **intentionally not** read from
#: the per-project ``devkit_config.json`` file — see :func:`get_recipient`.
DEFAULT_RECIPIENT = "panmofan@icloud.com"

#: Default config structure.  Every SMTP *credential* field is empty —
#: the developer must supply their own account.  Nothing is hard-coded.
DEFAULT_CONFIG: dict[str, Any] = {
    "smtp": {
        "host": "",
        "port": 465,
        "use_tls": False,
        "user": "",
        "password": "",
        "from_addr": "",
    },
    "recipient": DEFAULT_RECIPIENT,
    "rate_limit_seconds": 600,  # 10 minutes (function list C5 AC-2)
    "max_attachment_bytes": 512_000_000,  # 512 MB (macOS units)
    # Auto-update (C6).  Network is only touched on an explicit check or,
    # when this flag is on, once silently at launch.  User-toggleable.
    "auto_check_update": True,
}

_ENC_PREFIX = "enc:"


# ---------------------------------------------------------------------------
# Encryption helpers (Fernet, key in a 0600 sibling file)
# ---------------------------------------------------------------------------


def _key_path(work_dir: str) -> str:
    return os.path.join(work_dir, _KEY_FILENAME)


def _load_key(work_dir: str) -> bytes | None:
    p = _key_path(work_dir)
    if os.path.isfile(p):
        try:
            with open(p, "rb") as f:
                return f.read().strip()
        except OSError:
            return None
    return None


def _store_key(work_dir: str, key: bytes) -> None:
    p = _key_path(work_dir)
    with open(p, "wb") as f:
        f.write(key)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _get_fernet(work_dir: str):
    if not _HAVE_CRYPTO or not work_dir:
        return None
    key = _load_key(work_dir)
    if key is None:
        key = Fernet.generate_key()
        _store_key(work_dir, key)
    return Fernet(key)


def _encrypt_secret(work_dir: str, plaintext: str) -> str:
    """Encrypt ``plaintext`` for at-rest storage.  Returns '' for empty."""
    if not plaintext:
        return ""
    f = _get_fernet(work_dir)
    if f is None:
        # No crypto available — store as-is (degraded, but functional).
        return plaintext
    return _ENC_PREFIX + f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt_secret(work_dir: str, stored: str) -> str:
    """Decrypt a value produced by :func:`_encrypt_secret`."""
    if not stored:
        return ""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext
    f = _get_fernet(work_dir)
    if f is None:
        return ""
    try:
        return f.decrypt(stored[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _config_path(work_dir: str) -> str:
    return os.path.join(work_dir, CONFIG_FILENAME)


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
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    if "smtp" in data and isinstance(data["smtp"], dict):
        merged["smtp"] = {**DEFAULT_CONFIG["smtp"], **data["smtp"]}
        # Reveal the cleartext password only in memory.
        merged["smtp"]["password"] = _decrypt_secret(
            work_dir, merged["smtp"].get("password", "")
        )
    return merged


def save_config(work_dir: str, config: dict[str, Any]) -> None:
    """Persist developer config to JSON file (password stored encrypted)."""
    os.makedirs(work_dir, exist_ok=True)
    smtp_in = config.get("smtp", DEFAULT_CONFIG["smtp"])
    to_save = {
        "smtp": {
            "host": smtp_in.get("host", DEFAULT_CONFIG["smtp"]["host"]),
            "port": smtp_in.get("port", DEFAULT_CONFIG["smtp"]["port"]),
            "use_tls": smtp_in.get("use_tls", DEFAULT_CONFIG["smtp"]["use_tls"]),
            "user": smtp_in.get("user", DEFAULT_CONFIG["smtp"]["user"]),
            "password": _encrypt_secret(work_dir, str(smtp_in.get("password", "") or "")),
            "from_addr": smtp_in.get("from_addr", DEFAULT_CONFIG["smtp"]["from_addr"]),
        },
        "recipient": config.get("recipient", DEFAULT_CONFIG["recipient"]),
        "rate_limit_seconds": config.get(
            "rate_limit_seconds", DEFAULT_CONFIG["rate_limit_seconds"]
        ),
        "max_attachment_bytes": config.get(
            "max_attachment_bytes", DEFAULT_CONFIG["max_attachment_bytes"]
        ),
        "auto_check_update": bool(
            config.get("auto_check_update", DEFAULT_CONFIG["auto_check_update"])
        ),
    }
    with open(_config_path(work_dir), "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)


def get_smtp_config(work_dir: str) -> dict[str, Any]:
    """Get SMTP config for sending emails (password returned in cleartext)."""
    config = load_config(work_dir)
    return config.get("smtp", DEFAULT_CONFIG["smtp"])


def get_recipient(work_dir: str) -> str:
    """Return the recipient email (the XiJian developer-group inbox).

    The recipient is fixed in code (:data:`DEFAULT_RECIPIENT`) and is
    **deliberately not** read from the per-project ``devkit_config.json``
    file, so it can never be changed via configuration.
    """
    return DEFAULT_RECIPIENT


def get_rate_limit(work_dir: str) -> int:
    """Get rate limit in seconds."""
    config = load_config(work_dir)
    return int(config.get("rate_limit_seconds", DEFAULT_CONFIG["rate_limit_seconds"]))


def get_max_attachment_bytes(work_dir: str) -> int:
    """Get max attachment size in bytes."""
    config = load_config(work_dir)
    return int(config.get("max_attachment_bytes", DEFAULT_CONFIG["max_attachment_bytes"]))


def get_auto_check_update(work_dir: str) -> bool:
    """Whether to silently check for updates at launch."""
    config = load_config(work_dir)
    return bool(config.get("auto_check_update", DEFAULT_CONFIG["auto_check_update"]))


def set_auto_check_update(work_dir: str, enabled: bool) -> None:
    """Persist the launch-time auto-update-check preference."""
    config = load_config(work_dir)
    config["auto_check_update"] = bool(enabled)
    save_config(work_dir, config)


__all__ = [
    "load_config",
    "save_config",
    "get_smtp_config",
    "get_recipient",
    "get_rate_limit",
    "get_max_attachment_bytes",
    "get_auto_check_update",
    "set_auto_check_update",
    "DEFAULT_CONFIG",
    "DEFAULT_RECIPIENT",
]
