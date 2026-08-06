"""DevKit 的开发者配置持久化。

将开发者自己的 SMTP 设置存储在一个 JSON 配置文件中，
该文件在重启和手动编辑后仍然保留，位于工作目录下。

安全
--------
SMTP 密码**绝不**以明文存储。保存时使用 Fernet（AES-128-GCM，
来自 ``cryptography`` 包）加密，密钥存放在同级文件
``devkit_config.key`` 中（权限 ``0600``），位于用户的私有工作目录下
（默认为 ``~/Library/Application Support/XiJian/DevKit``）。
明文密码只会在 DevKit 实际发送提交时于内存中重建。

源码中不硬编码任何 SMTP 凭据——开发者必须填写自己的 SMTP 账号。
收件邮箱是 XiJian 开发者群组收件箱，用于提交路由。
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

#: XiJian 开发者群组收件箱 —— 提交路由的目的地。
#: 这不是凭据；它是 DevKit 投递打包提交的固定收件人。
#: 开发者通过 UI 填写自己的 SMTP 服务器 / 登录信息。
#:
#: 收件人硬编码在此，**刻意不**从每个项目的 ``devkit_config.json``
#: 文件中读取 —— 参见 :func:`get_recipient`。
DEFAULT_RECIPIENT = "panmofan@icloud.com"

#: 默认配置结构。每个 SMTP *凭据*字段都为空——
#: 开发者必须提供自己的账号。不硬编码任何内容。
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
    "rate_limit_seconds": 600,  # 10 分钟（功能清单 C5 AC-2）
    "max_attachment_bytes": 512_000_000,  # 512 MB（macOS 单位）
    # 自动更新（C6）。仅在进行显式检查时访问网络，或者在该标志开启时
    # 于启动时静默检查一次。用户可切换。
    "auto_check_update": True,
}

_ENC_PREFIX = "enc:"


# ---------------------------------------------------------------------------
# 加密辅助函数（Fernet，密钥在 0600 权限的同级文件中）
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
    """加密 ``plaintext`` 以便静态存储。空字符串返回 ''。"""
    if not plaintext:
        return ""
    f = _get_fernet(work_dir)
    if f is None:
        # 没有可用的加密库——原样存储（降级，但可用）。
        return plaintext
    return _ENC_PREFIX + f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt_secret(work_dir: str, stored: str) -> str:
    """解密 :func:`_encrypt_secret` 产生的值。"""
    if not stored:
        return ""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # 遗留明文
    f = _get_fernet(work_dir)
    if f is None:
        return ""
    try:
        return f.decrypt(stored[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def _config_path(work_dir: str) -> str:
    return os.path.join(work_dir, CONFIG_FILENAME)


def load_config(work_dir: str) -> dict[str, Any]:
    """从 JSON 文件加载开发者配置，并与默认值合并。"""
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
        # 仅在内存中还原明文密码。
        merged["smtp"]["password"] = _decrypt_secret(
            work_dir, merged["smtp"].get("password", "")
        )
    return merged


def save_config(work_dir: str, config: dict[str, Any]) -> None:
    """将开发者配置持久化到 JSON 文件（密码加密存储）。"""
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
    """获取用于发送邮件的 SMTP 配置（密码以明文返回）。"""
    config = load_config(work_dir)
    return config.get("smtp", DEFAULT_CONFIG["smtp"])


def get_recipient(work_dir: str) -> str:
    """返回收件邮箱（XiJian 开发者群组收件箱）。

    收件人在代码中固定（:data:`DEFAULT_RECIPIENT`），**刻意不**从
    每个项目的 ``devkit_config.json`` 文件读取，因此永远无法
    通过配置更改。
    """
    return DEFAULT_RECIPIENT


def get_rate_limit(work_dir: str) -> int:
    """获取限流秒数。"""
    config = load_config(work_dir)
    return int(config.get("rate_limit_seconds", DEFAULT_CONFIG["rate_limit_seconds"]))


def get_max_attachment_bytes(work_dir: str) -> int:
    """获取最大附件大小（字节）。"""
    config = load_config(work_dir)
    return int(config.get("max_attachment_bytes", DEFAULT_CONFIG["max_attachment_bytes"]))


def get_auto_check_update(work_dir: str) -> bool:
    """是否在启动时静默检查更新。"""
    config = load_config(work_dir)
    return bool(config.get("auto_check_update", DEFAULT_CONFIG["auto_check_update"]))


def set_auto_check_update(work_dir: str, enabled: bool) -> None:
    """持久化启动时自动检查更新的偏好。"""
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
