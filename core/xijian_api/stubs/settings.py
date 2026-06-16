"""Stub settings service — global user-tunable prefs + permissions."""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


_DEFAULT_SETTINGS = {
    "language": "zh_CN",
    "nsfw_allowed": False,
    "rate_limit": {"enabled": False, "requests_per_minute": 60},
    "guard_level": "standard",
    "memory_top_k": 5,
    "auto_remember": True,
}

_DEFAULT_PERMISSIONS = (
    {"key": "notifications", "granted": True, "granted_at": None},
    {"key": "microphone", "granted": False, "granted_at": None},
    {"key": "camera", "granted": False, "granted_at": None},
    {"key": "files", "granted": True, "granted_at": None},
    {"key": "accessibility", "granted": False, "granted_at": None},
)


def seed_default() -> None:
    if state.protection.get("__settings_seeded__"):
        return
    state.protection.setdefault("settings", dict(_DEFAULT_SETTINGS))
    state.protection["__settings_seeded__"] = True


def get_settings() -> dict:
    seed_default()
    return dict(state.protection["settings"])


def patch_settings(patch: dict) -> dict:
    seed_default()
    settings = state.protection["settings"]
    for key, value in patch.items():
        settings[key] = value
    settings["updated_at"] = now_ts()
    return dict(settings)


def list_permissions() -> list[dict]:
    seed_default()
    items = []
    for perm in _DEFAULT_PERMISSIONS:
        items.append(
            {
                "key": perm["key"],
                "granted": perm["granted"],
                "granted_at": perm["granted_at"] or now_ts() if perm["granted"] else None,
            }
        )
    return items


__all__ = ["seed_default", "get_settings", "patch_settings", "list_permissions"]