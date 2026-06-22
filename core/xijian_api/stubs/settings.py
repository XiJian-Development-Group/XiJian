"""Stub settings service — global user-tunable prefs + permissions.

Settings container is created lazily on first read/write so the
service ships with no pre-populated demo values.  Operators configure
defaults through ``PATCH /v1/xijian/settings``.
"""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


# Permissions are a fixed OS-level catalogue — not user data — so they
# are returned as the static catalogue every call.  Granted state is
# reflected via ``granted_at`` being non-null only after the user
# actually grants a permission through the system.
_DEFAULT_PERMISSIONS: tuple[str, ...] = (
    "notifications",
    "microphone",
    "camera",
    "files",
    "accessibility",
)


def seed_default() -> None:
    """No-op — settings container is created lazily on first read/write."""
    return None


def _settings_bucket() -> dict:
    """Return the settings dict, creating an empty one on first use."""
    return state.protection.setdefault("settings", {})


def get_settings() -> dict:
    return dict(_settings_bucket())


def patch_settings(patch: dict) -> dict:
    settings = _settings_bucket()
    for key, value in patch.items():
        settings[key] = value
    settings["updated_at"] = now_ts()
    return dict(settings)


def list_permissions() -> list[dict]:
    items = []
    for key in _DEFAULT_PERMISSIONS:
        items.append(
            {
                "key": key,
                "granted": False,
                "granted_at": None,
            }
        )
    return items


__all__ = ["seed_default", "get_settings", "patch_settings", "list_permissions"]