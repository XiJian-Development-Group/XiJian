"""System-keychain backed secret storage for Core (macOS Keychain).

Core 的机密存储 —— 直接使用操作系统钥匙串（macOS Keychain）。

Why / 为什么
------------

AI-backend API keys must survive restarts **and** never sit in
plaintext inside the SQLite store.  On macOS the login keychain is the
platform-blessed solution: encrypted at rest, user-scoped, unlocked at
login, and accessible from any process running as the user — which is
exactly the trust model of the bundled Core subprocess.

AI 后端的 API Key 既要跨重启持久，又绝不能以明文躺在 SQLite 里。
macOS 上登录钥匙串是平台标准方案：静态加密、用户作用域、登录即解锁，
同用户的任何进程均可访问——这正是内嵌 Core 子进程的信任模型。

Implementation / 实现
---------------------

Thin wrapper over the ``security`` CLI (no third-party deps):

对 ``security`` 命令行的轻量封装（零第三方依赖）：

* :func:`set_secret`   — ``security add-generic-password … -U`` (upsert)
* :func:`get_secret`   — ``security find-generic-password … -w``
* :func:`delete_secret`— ``security delete-generic-password``
* :func:`available`    — True when the platform supports the CLI

Naming convention: one generic-password item per secret, service
:data:`SERVICE_NAME`, account ``backend:<backend_id>:api_key``.

命名约定：每个机密一条通用密码项，服务名 :data:`SERVICE_NAME`，
账户名 ``backend:<backend_id>:api_key``。

Non-macOS fallback: :func:`available` returns False and callers keep
the legacy behaviour (store in DB) with a logged warning.  Windows
DPAPI support can slot in here later.

非 macOS 平台回退：:func:`available` 返回 False，调用方保持旧行为
（存入数据库）并记录警告。Windows DPAPI 可在此处后续接入。
"""

from __future__ import annotations

import logging
import sys

_LOGGER = logging.getLogger("xijian_api.keychain")


#: Keychain service name shared by every XiJian Core secret.
#: 所有 XiJian Core 机密共用的钥匙串服务名。
SERVICE_NAME = "com.skyc8266.xijian.core"


def available() -> bool:
    """True when the OS provides a supported keychain CLI.

    当前操作系统是否提供受支持的钥匙串命令行工具。
    """
    return sys.platform == "darwin"


def _account_for(backend_id: str) -> str:
    """Keychain account name for a backend's api_key."""
    return f"backend:{backend_id}:api_key"


def set_secret(backend_id: str, value: str) -> bool:
    """Store (or update) an API key in the keychain.  Returns success.

    将 API Key 存入（或更新到）钥匙串。返回是否成功。
    """
    if not available():
        _LOGGER.warning(
            "keychain unavailable on %s; secret for %s kept in database",
            sys.platform, backend_id,
        )
        return False
    import subprocess

    try:
        proc = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", SERVICE_NAME,
                "-a", _account_for(backend_id),
                "-w", value,
                "-U",  # upsert: update if the item already exists
            ],
            capture_output=True,
            timeout=10,
        )
        if proc.returncode != 0:
            _LOGGER.error(
                "security add-generic-password failed (%d): %s",
                proc.returncode, proc.stderr.decode(errors="replace").strip(),
            )
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LOGGER.error("keychain write failed for %s: %s", backend_id, exc)
        return False


def get_secret(backend_id: str) -> str | None:
    """Read an API key from the keychain; ``None`` when absent/error.

    从钥匙串读取 API Key；不存在或出错时返回 ``None``。
    """
    if not available():
        return None
    import subprocess

    try:
        proc = subprocess.run(
            [
                "security", "find-generic-password",
                "-s", SERVICE_NAME,
                "-a", _account_for(backend_id),
                "-w",
            ],
            capture_output=True,
            timeout=10,
        )
        if proc.returncode != 0:
            # errSecItemNotFound (-25300) is the normal "absent" case.
            # errSecItemNotFound (-25300) 是正常的"不存在"情况。
            return None
        value = proc.stdout.decode().strip()
        return value or None
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LOGGER.error("keychain read failed for %s: %s", backend_id, exc)
        return None


def delete_secret(backend_id: str) -> bool:
    """Remove an API key from the keychain.  Returns success.

    从钥匙串删除 API Key。返回是否成功（条目本就不存在也算成功）。
    """
    if not available():
        return False
    import subprocess

    try:
        proc = subprocess.run(
            [
                "security", "delete-generic-password",
                "-s", SERVICE_NAME,
                "-a", _account_for(backend_id),
            ],
            capture_output=True,
            timeout=10,
        )
        # 44 == errSecItemNotFound on modern macOS — treat as success.
        # 现代 macOS 上 44 对应 errSecItemNotFound——视为成功。
        return proc.returncode in (0, 44)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LOGGER.error("keychain delete failed for %s: %s", backend_id, exc)
        return False


__all__ = [
    "SERVICE_NAME",
    "available",
    "set_secret",
    "get_secret",
    "delete_secret",
]
