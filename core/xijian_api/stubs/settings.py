"""Stub settings service — global user-tunable prefs + permissions.
存根设置服务 — 全局用户可调偏好 + 权限。

Settings container is created lazily on first read/write so the
service ships with no pre-populated demo values.  Operators configure
defaults through ``PATCH /v1/xijian/settings``.

设置容器在首次读/写时惰性创建，因此服务在初始时没有预填充的演示值。
运营人员通过 ``PATCH /v1/xijian/settings`` 配置默认值。
"""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


# Permissions are a fixed OS-level catalogue — not user data — so they
# are returned as the static catalogue every call.  Granted state is
# reflected via ``granted_at`` being non-null only after the user
# actually grants a permission through the system.
# 权限是固定的操作系统级目录（非用户数据），因此每次调用返回静态目录。
# 授予状态通过 ``granted_at`` 非空来反映，仅在用户实际通过系统授予权限后。
_DEFAULT_PERMISSIONS: tuple[str, ...] = (
    "notifications",
    "microphone",
    "camera",
    "files",
    "accessibility",
)


def seed_default() -> None:
    """No-op — settings container is created lazily on first read/write.
    空操作 — 设置容器在首次读/写时惰性创建。
    """
    return None


def _settings_bucket() -> dict:
    """Return the settings record, creating an empty one on first use.

    Backed by the persistent ``app_settings`` DictDB so user settings
    survive Core restarts (previously this lived in the in-memory
    ``safety_state`` and was wiped on every restart).
    返回设置记录，首次使用时创建。改由持久化的 ``app_settings``
    DictDB 承载，使设置跨 Core 重启保留（此前存于内存
    ``safety_state``，每次重启即丢）。
    """
    rec = state.app_settings.get("settings")
    if not isinstance(rec, dict):
        rec = {}
        state.app_settings["settings"] = rec
    return rec


def get_settings() -> dict:
    """Return all settings.
    返回所有设置。
    """
    return dict(_settings_bucket())


def patch_settings(patch: dict) -> dict:
    """Apply a partial update to settings (write-through to storage).
    对设置应用部分更新（写透到持久存储）。
    """
    settings = _settings_bucket()
    for key, value in patch.items():
        settings[key] = value
    settings["updated_at"] = now_ts()
    state.app_settings["settings"] = settings
    return dict(settings)


def list_permissions() -> list[dict]:
    """List all available system permissions.
    列出所有可用的系统权限。
    """
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
