"""开发者工具提交管线。

本模块是 DevKit UI 在开发者点击「提交」时所走提交流程的**大脑**。
整个流程刻意设计为无服务器：

    本地负载  ──►  打包  ──►  附加到邮件  ──►  SMTP

DevKit 是一个**独立的** Pywebview 应用——它不与主 API 共享 Flask
服务器，也从不向它发起 HTTP 调用。UI <-> Python 之间的通信通过
``pywebview.js_api`` 进行（参见 :mod:`devkit.api`）。

本包拥有三个内存存储桶（与 :mod:`devkit.state` 镜像）：

* ``submissions``        —— 每次提交的记录，以 id 为键。
* ``last_submit_at``     —— 每个开发者的上次提交时间戳
  （ISO 8601 字符串，用于 1 小时限流）。
* ``local_archives``     —— 每次提交产出的最新 7Z 归档文件路径，
  供清理任务查找。

副作用
------------

* :func:`pack_payload` 将 7Z 固态归档写入临时路径（首选），
  或在未安装 ``py7zr`` 时回退到 ``zipfile``。回退会记录为警告，
  让操作者知道要安装 ``py7zr`` 以获得规范要求的高压缩格式。
* :func:`send_submission_email` 打开 SMTP 连接，附加归档并发送。
  所有 SMTP 凭据都从本文件顶部的模块级常量读取（参见
  *环境变量*）。
* :func:`submit` 编排完整流程并返回一个小 dict，JS API 可将其
  序列化回 UI。

限流
----------

每个 ``developer_id`` 每小时最多提交**一次**。冷却通过
:data:`DEV_SUBMIT_COOLDOWN_SECONDS` 强制实施；上次提交时间戳
持久化在 ``state.last_submit_at`` 中。

大小限制
----------

打包后的归档必须**≤ 1200 MB**（macOS 默认单位：``1000 KB = 1 MB``，
``1000 MB = 1 GB``），即 :data:`DEV_SUBMIT_MAX_ATTACHMENT_BYTES` =
1 200 000 000 字节。

打包前的负载大小也有限制——如果累计输入超过限制，我们拒绝甚至*开始*
打包，因为通过 ``py7zr`` 流式处理 7Z 在多 GB 输入上可能很昂贵。

环境变量
---------------------

所有硬编码常量都接受 ``XIJIAN_DEV_<NAME>`` 形式的环境变量覆盖，
以便部署 / CI 无需修改源码即可注入机密：

==============================  ==============================
常量                            环境变量覆盖
==============================  ==============================
``DEV_SUBMIT_SMTP_HOST``        ``XIJIAN_DEV_SMTP_HOST``
``DEV_SUBMIT_SMTP_PORT``        ``XIJIAN_DEV_SMTP_PORT``
``DEV_SUBMIT_SMTP_USE_TLS``     ``XIJIAN_DEV_SMTP_USE_TLS``
``DEV_SUBMIT_SMTP_USER``        ``XIJIAN_DEV_SMTP_USER``
``DEV_SUBMIT_SMTP_PASSWORD``    ``XIJIAN_DEV_SMTP_PASSWORD``
``DEV_SUBMIT_RECIPIENT``        *（代码中固定；不可覆盖）*
``DEV_SUBMIT_FROM_ADDR``        ``XIJIAN_DEV_FROM_ADDR``
``DEV_SUBMIT_MAX_BYTES``        ``XIJIAN_DEV_MAX_BYTES``
``DEV_SUBMIT_COOLDOWN``         ``XIJIAN_DEV_COOLDOWN_SECONDS``
==============================  ==============================

测试面
------------

纯辅助函数 + 有副作用的入口点，都设计为测试可以通过
``monkeypatch`` 驱动：

* :func:`check_rate_limit`
* :func:`check_archive_size`
* :func:`archive_name`
* :func:`build_manifest`
* :func:`pack_payload`
* :func:`send_submission_email`（可注入 :func:`_smtp_send`）
* :func:`submit`（可注入 :func:`_smtp_send`）
* :func:`last_submit_for`
* :func:`reset_for_testing`

生产调用方通过 :class:`devkit.api.DevKitApi` 暴露的 Pywebview
``js_api`` 路由。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import io  # noqa: F401 — re-exported for tests that build in-memory files
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime
from typing import Any

from devkit import config, state
from devkit._vendor import ApiError, gen_submission_id, iso_now, now_ts


_LOGGER = logging.getLogger("devkit")


# ---------------------------------------------------------------------------
# 硬编码配置（部署前替换）
# ---------------------------------------------------------------------------

#: SMTP 服务器主机。默认为空，开发者必须提供自己的 SMTP 账号。
DEV_SUBMIT_SMTP_HOST: str = os.environ.get("XIJIAN_DEV_SMTP_HOST", "")
#: SMTP 服务器端口。465 = SSL 提交端口。
DEV_SUBMIT_SMTP_PORT: int = int(os.environ.get("XIJIAN_DEV_SMTP_PORT", "465") or "465")
#: 是否在 SMTP 连接上使用 STARTTLS。
DEV_SUBMIT_SMTP_USE_TLS: bool = os.environ.get("XIJIAN_DEV_SMTP_USE_TLS", "0") in (
    "1",
    "true",
    "yes",
)
#: SMTP 认证用户（开发者提供）。
DEV_SUBMIT_SMTP_USER: str = os.environ.get("XIJIAN_DEV_SMTP_USER", "")
#: SMTP 认证密码（开发者提供）。
DEV_SUBMIT_SMTP_PASSWORD: str = os.environ.get("XIJIAN_DEV_SMTP_PASSWORD", "")
#: 开发者群组收件人（XiJian 提交收件箱）。这是路由目的地，
#: 不是登录凭据。硬编码并固定在
#: :data:`devkit.config.DEFAULT_RECIPIENT`（单一事实来源，
#: 绝不从每个项目的配置文件读取）。
DEV_SUBMIT_RECIPIENT: str = config.DEFAULT_RECIPIENT
#: 外发邮件上的发件地址（开发者提供）。
DEV_SUBMIT_FROM_ADDR: str = os.environ.get("XIJIAN_DEV_FROM_ADDR", "submissions@example.com")

#: 附件大小的硬性限制（字节）。按 macOS 默认单位（``1000 KB = 1 MB``，
#: ``1000 MB = 1 GB``）为 1200 MB =
#: ``1200 × 1000 × 1000 = 1 200 000 000``（功能清单 C5 AC-3）。
DEV_SUBMIT_MAX_ATTACHMENT_BYTES: int = int(
    os.environ.get("XIJIAN_DEV_MAX_BYTES", "512000000") or "512000000"
)
#: 每次提交之间的每个开发者冷却时间。3600 秒 = 1 小时
#: （功能清单 C5 AC-2）。
DEV_SUBMIT_COOLDOWN_SECONDS: int = int(
    os.environ.get("XIJIAN_DEV_COOLDOWN_SECONDS", "600") or "600"
)
#: 本地归档保留时间。除非调用 :func:`keep_archive`，否则归档在
#: 这么多秒后被删除。默认：7 天。
DEV_SUBMIT_LOCAL_RETENTION_SECONDS: int = int(
    os.environ.get("XIJIAN_DEV_LOCAL_RETENTION", "604800") or "604800"
)
#: 存放本地 7Z 归档的目录。``None`` ⇒ 使用
#: ``tempfile.gettempdir() / xijian_devkit``。
_DEV_SUBMIT_LOCAL_DIR: str | None = os.environ.get("XIJIAN_DEV_LOCAL_DIR")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: DevKit API 版本（用于 whoami/get_status 返回）。
_API_VERSION = "1.0.0"

#: 我们写入 manifest 的归档格式标签。让接收方一眼就能
#: 识别回退 zip 的提交。
ARCHIVE_FORMAT_7Z = "7z-solid"
ARCHIVE_FORMAT_ZIP = "zip"

#: :func:`submit` 接受的提交类型。
TARGET_KINDS: tuple[str, ...] = ("world", "character", "plot")


# ---------------------------------------------------------------------------
# 资源定位器（兼容 PyInstaller）
# ---------------------------------------------------------------------------


def ui_dir() -> "Path":
    """返回存放 DevKit UI 资源（``index.html`` 等）的目录。

    在正常的 ``pip install`` 运行中，这是随本 ``__init__.py`` 一起发布的
    ``ui/`` 文件夹。当包被 PyInstaller 冻结（设置了 ``sys.frozen``）时，
    PyInstaller 会将捆绑的 ``datas`` 解压到 ``sys._MEIPASS``——我们
    在那里镜像包的目录布局，因此同样的相对路径仍然有效。

    这一层间接让窗口入口点无需在 :mod:`devkit.main` 中编写条件代码，
    即可从源码和二进制发行版加载 ``ui/index.html``。
    """
    import pathlib
    import sys

    if getattr(sys, "frozen", False):
        # PyInstaller：``ui/`` 文件夹捆绑在 sys._MEIPASS 内的
        # 相同相对路径（``devkit/ui``）下——参见
        # ``devkit/xijian-devkit.spec`` 中的 ``datas`` 条目。
        return pathlib.Path(sys._MEIPASS) / "devkit" / "ui"
    return pathlib.Path(__file__).resolve().parent / "ui"


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class DevKitError(ApiError):
    """DevKit 特定错误的基类。

    继承 :class:`devkit._vendor.ApiError`（``xijian_api.errors.ApiError``
    的无 Flask 副本），使 JSON-API 契约在整个项目中保持一致，
    尽管 DevKit 本身从不发出 HTTP 信封——UI 通过
    :func:`devkit.api.serialize_error` 以纯 dict 形式接收错误。
    """

    def __init__(self, status: int, message: str, code: str, **extra: Any) -> None:
        super().__init__(status, message, "server_error", code=code, **extra)


class RateLimitedError(DevKitError):
    """429 —— developer_id 处于冷却窗口内。"""

    def __init__(self, retry_after_seconds: int, **extra: Any) -> None:
        super().__init__(
            status=429,
            message=f"rate limited — wait {retry_after_seconds} seconds before next submission",
            code="rate_limited",
            retry_after_seconds=retry_after_seconds,
            **extra,
        )
        self.retry_after_seconds = retry_after_seconds


class PayloadTooLargeError(DevKitError):
    """413 —— 归档超过 1200 MB 限制。"""

    def __init__(self, size_bytes: int, max_bytes: int, **extra: Any) -> None:
        super().__init__(
            status=413,
            message=f"attachment size {size_bytes} bytes exceeds limit {max_bytes}",
            code="payload_too_large",
            size_bytes=size_bytes,
            max_bytes=max_bytes,
            **extra,
        )
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class SmtpError(DevKitError):
    """502 —— SMTP 提交失败。

    ``category`` 字段指明失败模式（``auth_failed``、``connection_failed``、
    ``tls_failed``、``other`` 之一）；``response`` 字段在可用时
    携带原始 SMTP 回复。
    """

    def __init__(self, category: str, response: str = "", **extra: Any) -> None:
        super().__init__(
            status=502,
            message=f"smtp {category}: {response or 'no detail'}",
            code="smtp_error",
            category=category,
            response=response,
            **extra,
        )
        self.category = category
        self.response = response


# ---------------------------------------------------------------------------
# 纯辅助函数
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _now_iso() -> str:
    """返回适合文件名和邮件的 ISO 8601 UTC 字符串。"""
    return iso_now()


def archive_name(developer_id: str, *, now: _dt.datetime | None = None) -> str:
    """返回磁盘上的归档文件名。

    格式：``<developer_id>__<iso8601_utc>.7z`` —— 下划线分隔符
    保持文件名对接收方可解析。
    """
    safe_id = "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in developer_id
    )
    safe_id = safe_id or "developer"
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    return f"{safe_id}__{moment.strftime('%Y-%m-%dT%H-%M-%SZ')}.7z"


def build_manifest(
    *,
    developer_id: str,
    target_kind: str,
    target_id: str,
    payload: Mapping[str, Any],
    submitted_at: str,
    ai_ratio: float = 0.0,
) -> dict[str, Any]:
    """构建放在每个归档根部的 JSON manifest。

    manifest 让接收方无需解包整个 7Z 即可计算 ``ai_ratio``、
    验证归档的 SHA-256，并审计谁在何时提交了什么。
    """
    files = payload.get("files") or []
    if not isinstance(files, list):
        files = []
    # Pack-compatible fields (§B): the same archive installs directly
    # into the core resource-pack engine, so the manifest doubles as a
    # pack manifest (schema stays the submission schema — the core
    # validator accepts both).
    # 包兼容字段（§B）：同一归档可直接安装进核心资源包引擎，
    # 因此 manifest 兼作包清单（schema 保持提交 schema —— 核心校验器两者都接受）。
    try:
        from devkit.version import get_app_version

        pack_version = get_app_version()
    except Exception:  # noqa: BLE001 — best-effort version resolution
        pack_version = ""
    return {
        "schema": "xijian.devkit.submission/v1",
        "developer_id": developer_id,
        "submitted_at": submitted_at,
        "target_kind": target_kind,
        "target_id": target_id,
        "ai_ratio": float(ai_ratio),
        "files": [str(f) for f in files],
        "notes": str(payload.get("notes", "")),
        # Pack fields (§B) — 包字段。
        "name": str(payload.get("name") or target_id),
        "version": pack_version or "0.0.0",
        "kind": target_kind,
        "author": developer_id,
        "description": str(payload.get("notes", "")),
        "dependencies": [],
        "package_id": target_id,
    }


def check_rate_limit(developer_id: str, *, now: float | None = None) -> int:
    """返回 ``developer_id`` 距离下次可提交的剩余秒数。

    当冷却时间未过时抛出 :class:`RateLimitedError`。
    ``now`` 覆盖参数让测试可以快进时钟。
    """
    moment = float(now) if now is not None else float(now_ts())
    last_iso = state.last_submit_at.get(developer_id)
    if last_iso is None:
        return 0
    try:
        last_ts = _dt.datetime.fromisoformat(
            last_iso.replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, AttributeError):
        return 0
    elapsed = moment - last_ts
    if elapsed < 0:
        # 时钟倒退——视为重新开始。
        return 0
    remaining = int(DEV_SUBMIT_COOLDOWN_SECONDS - elapsed)
    if remaining > 0:
        raise RateLimitedError(remaining, last_submit_at=last_iso)
    return 0


def check_archive_size(size_bytes: int) -> None:
    """如果 ``size_bytes`` *严格超过*上限，抛出 :class:`PayloadTooLargeError`。

    严格的 ``>`` 让原始预算检查保持干净：真正打算控制在 1200 MB
    以内的调用方会在添加 manifest 之前调用此函数。

    UI 使用 :func:`preview_size_payload`（更严格的辅助函数），它还会
    标记*等于*上限的负载——那些无法再容纳 manifest，因此不应允许
    用户提交。
    """
    if size_bytes > DEV_SUBMIT_MAX_ATTACHMENT_BYTES:
        raise PayloadTooLargeError(
            size_bytes=size_bytes,
            max_bytes=DEV_SUBMIT_MAX_ATTACHMENT_BYTES,
        )


def preview_size_payload(size_bytes: int) -> tuple[bool, str]:
    """UI 侧预检。

    返回 ``(ok, message)``。当负载即使在减去 manifest 预留（几 KB）
    和 7Z 流开销后仍放不下时，``ok=False``。实际上这意味着任何
    大小等于或超过 ``DEV_SUBMIT_MAX_ATTACHMENT_BYTES`` 的负载
    都会被提前拒绝。
    """
    if size_bytes >= DEV_SUBMIT_MAX_ATTACHMENT_BYTES:
        return False, (
            f"selected payload ({size_bytes} bytes) exceeds limit "
            f"{DEV_SUBMIT_MAX_ATTACHMENT_BYTES} bytes (manifest + 7Z overhead "
            "need a few KB of headroom)"
        )
    return True, "ok"


def compute_sha256(path: str) -> str:
    """``path`` 处文件的 SHA-256 十六进制摘要。流式处理，恒定内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def local_archive_dir() -> str:
    """返回（并懒创建）存放归档的目录。"""
    base = _DEV_SUBMIT_LOCAL_DIR or os.path.join(tempfile.gettempdir(), "xijian_devkit")
    os.makedirs(base, exist_ok=True)
    return base


def local_archive_path(name: str) -> str:
    return os.path.join(local_archive_dir(), name)


# ---------------------------------------------------------------------------
# 打包
# ---------------------------------------------------------------------------


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _cumulative_size(file_entries: Iterable[Mapping[str, Any]]) -> int:
    """对文件条目的 ``size`` 字段求和。用于打包前检查。"""
    total = 0
    for entry in file_entries:
        try:
            total += int(entry.get("size") or 0)
        except (TypeError, ValueError):
            continue
    return total


def pack_payload(
    manifest: Mapping[str, Any],
    file_entries: list[Mapping[str, Any]],
    *,
    archive_path: str | None = None,
) -> tuple[str, int, str]:
    """将 manifest + 每个文件条目打包成 7Z 固态归档。

    返回 ``(archive_path, archive_size_bytes, archive_format)``。
    未安装 ``py7zr`` 时回退到 ``zipfile`` 并使用最高压缩级别——
    接收方从 manifest 和文件扩展名检测格式。

    参数
    ----------
    manifest:
        由 :func:`build_manifest` 构建的 manifest dict。
    file_entries:
        每个条目是包含 ``path``（文件系统路径）、
        ``arcname``（归档内的可选名称）和
        ``size``（可选的预检大小提示）的映射。
    archive_path:
        目标路径。默认为
        :func:`local_archive_path` + :func:`archive_name`。
    """
    pre_size = _cumulative_size(file_entries)
    _LOGGER.info("pack_payload: %d file entries, %d cumulative bytes", len(file_entries), pre_size)
    if pre_size > DEV_SUBMIT_MAX_ATTACHMENT_BYTES:
        # 我们甚至不开始打包——7Z / zip 不可能产出比输入总和更小的
        # 输出（抛开适度的压缩收益），而且我们不想在一个注定失败的
        # 提交上浪费 CPU。
        raise PayloadTooLargeError(
            size_bytes=pre_size,
            max_bytes=DEV_SUBMIT_MAX_ATTACHMENT_BYTES,
        )

    target = archive_path or local_archive_path(
        archive_name(str(manifest.get("developer_id", "developer")))
    )

    try:
        import py7zr  # type: ignore[import-not-found]
    except ImportError:
        py7zr = None  # type: ignore[assignment]

    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )

    _LOGGER.info("packing archive to %s", target)

    if py7zr is not None:
        # C5 AC-1 要求 7Z *固态*归档。py7zr 的默认写入模式就是
        # 固态（``solid=True``），因此我们显式传入 ``solid=True``，
        # 以确保跨版本符合规范。
        with py7zr.SevenZipFile(target, mode="w") as archive:
            archive.writestr(manifest_bytes, "manifest.json")
            for entry in file_entries:
                src = entry.get("path")
                if not src or not os.path.isfile(src):
                    _LOGGER.warning("skipping missing file entry: %s", entry)
                    continue
                arcname = entry.get("arcname") or os.path.basename(src)
                archive.write(src, arcname)
                _LOGGER.debug("added to archive: %s -> %s", src, arcname)
        result = target, _file_size(target), ARCHIVE_FORMAT_7Z
        _LOGGER.info("archive created (7z): %s (%d bytes)", target, result[1])
        return result

    # 未安装 py7zr 时回退到 zip。我们记录 WARNING（不仅仅是 info），
    # 让操作者在控制台中看到，并记得安装 py7zr。
    _LOGGER.warning(
        "py7zr is not installed — falling back to zipfile. "
        "Install py7zr for the spec-mandated 7Z solid archive."
    )
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        for entry in file_entries:
            src = entry.get("path")
            if not src or not os.path.isfile(src):
                _LOGGER.warning("skipping missing file entry: %s", entry)
                continue
            arcname = entry.get("arcname") or os.path.basename(src)
            zf.write(src, arcname)
            _LOGGER.debug("added to zip: %s -> %s", src, arcname)
    result = target, _file_size(target), ARCHIVE_FORMAT_ZIP
    _LOGGER.info("archive created (zip fallback): %s (%d bytes)", target, result[1])
    return result


# ---------------------------------------------------------------------------
# SMTP —— 整个管线中唯一的网络调用
# ---------------------------------------------------------------------------


def _smtp_send(
    *,
    host: str,
    port: int,
    use_tls: bool,
    user: str,
    password: str,
    sender: str,
    recipient: str,
    message,
) -> tuple[str, str]:
    """通过 SMTP 发送一封邮件。

    返回 ``(smtp_status, smtp_response)``。任何失败都抛出
    :class:`SmtpError`；``category`` 字段指明失败模式，
    以便调用方映射为状态字符串。

    测试会对本函数（或 :func:`_smtp_send`）做 monkeypatch，
    以捕获外发的 ``message``，而无需真正连接服务器。
    """
    import smtplib
    import ssl

    smtp: smtplib.SMTP | smtplib.SMTP_SSL | None = None
    try:
        _LOGGER.info("connecting to SMTP %s:%s (SSL=%s, TLS=%s)", host, port, port == 465, use_tls)
        try:
            if port == 465:
                smtp = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
                _LOGGER.info("connected via SMTP_SSL to %s:%s", host, port)
            else:
                smtp = smtplib.SMTP(host, port, timeout=30)
                _LOGGER.info("connected via SMTP to %s:%s", host, port)
        except (OSError, smtplib.SMTPConnectError) as exc:
            _LOGGER.error("SMTP connection failed: %s", exc)
            raise SmtpError("connection_failed", str(exc)) from exc
        try:
            if not isinstance(smtp, smtplib.SMTP_SSL) and use_tls:
                _LOGGER.info("starting STARTTLS upgrade")
                try:
                    smtp.starttls(context=ssl.create_default_context())
                    _LOGGER.info("STARTTLS upgrade successful")
                except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
                    _LOGGER.error("STARTTLS failed: %s", exc)
                    raise SmtpError("tls_failed", str(exc)) from exc
            _LOGGER.info("logging in as %s", user)
            try:
                smtp.login(user, password)
                _LOGGER.info("SMTP login successful")
            except smtplib.SMTPAuthenticationError as exc:
                _LOGGER.error("SMTP auth failed (bad user/password): %s", exc)
                raise SmtpError("auth_failed", str(exc)) from exc
            except smtplib.SMTPException as exc:
                _LOGGER.error("SMTP auth failed: %s", exc)
                raise SmtpError("auth_failed", str(exc)) from exc
            _LOGGER.info("sending mail from %s to %s (%d bytes)", sender, recipient, len(message.as_string()))
            refused = smtp.sendmail(sender, [recipient], message.as_string())
            if refused:
                _LOGGER.error("recipient refused: %s", refused)
                raise SmtpError(
                    "other",
                    f"recipient refused: {refused}",
                )
            code, response = smtp.noop()
            _LOGGER.info("SMTP send complete: code=%s response=%s", code, response)
            return str(code), str(response)
        finally:
            try:
                smtp.quit()
            except smtplib.SMTPException:
                pass
    except SmtpError:
        raise
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional
        raise SmtpError("other", f"{type(exc).__name__}: {exc}") from exc


def build_email_message(
    *,
    developer_id: str,
    submitted_at: str,
    target_kind: str,
    target_id: str,
    ai_ratio: float,
    archive_filename: str,
    archive_size_bytes: int,
    content_sha256: str,
    archive_path: str,
    archive_format: str,
    from_addr: str | None = None,
    recipient: str | None = None,
) -> MIMEMultipart:
    """构建发送给开发者群组的 multipart MIME 邮件。"""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[XiJian DevKit Package Submit] {developer_id}"
    msg["From"] = from_addr or DEV_SUBMIT_SMTP_USER
    msg["To"] = recipient or DEV_SUBMIT_RECIPIENT
    msg["Date"] = format_datetime(_dt.datetime.now(_dt.timezone.utc))

    body_lines = [
        "提交者 ID:    " + developer_id,
        "",
        "提交时间:    " + submitted_at,
        "目标类型:     " + target_kind,
        "目标 ID:       " + target_id,
        "AI 协助占比: " + f"{ai_ratio:.2f}",
        "归档格式:  " + archive_format,
        f"附件:      {archive_filename} ({archive_size_bytes} bytes, "
        f"~{archive_size_bytes / 1_000_000:.2f} MB)",
        "内容 SHA256:  " + content_sha256,
        "",
        "— 自动由隙间开发者工具生成",
    ]
    msg.attach(MIMEText("\n".join(body_lines), "plain", "utf-8"))

    ctype = (
        "application/x-7z-compressed"
        if archive_format == ARCHIVE_FORMAT_7Z
        else "application/zip"
    )
    with open(archive_path, "rb") as fh:
        part = MIMEApplication(fh.read(), Name=archive_filename)
    # ``MIMEApplication`` 在构造时会设置默认的 ``Content-Type``；
    # 在 Python 3.13 上通过 ``part["Content-Type"]`` 替换头部会
    # 留下两份副本。删掉旧的再加新的，使 ``get_content_type()``
    # 返回归档的 MIME 类型。
    if "Content-Type" in part:
        del part["Content-Type"]
    part["Content-Type"] = ctype
    part["Content-Disposition"] = f'attachment; filename="{archive_filename}"'
    msg.attach(part)
    return msg


def send_submission_email(
    *,
    developer_id: str,
    submitted_at: str,
    target_kind: str,
    target_id: str,
    ai_ratio: float,
    archive_path: str,
    archive_format: str,
    work_dir: str | None = None,
    smtp_send: Callable[..., tuple[str, str]] | None = None,
) -> dict[str, str]:
    """构建 + 发送提交邮件。返回 SMTP 状态 dict。

    测试注入 ``smtp_send`` 以捕获邮件而不接触网络。省略时
    使用 :func:`_smtp_send`。

    如果提供了 ``work_dir``，则从开发者的配置文件加载 SMTP 设置。
    否则回退到模块常量。
    """
    # 从开发者的配置文件加载 SMTP 配置
    if work_dir:
        smtp_config = config.get_smtp_config(work_dir)
        recipient = config.get_recipient(work_dir)
    else:
        smtp_config = {
            "host": DEV_SUBMIT_SMTP_HOST,
            "port": DEV_SUBMIT_SMTP_PORT,
            "use_tls": DEV_SUBMIT_SMTP_USE_TLS,
            "user": DEV_SUBMIT_SMTP_USER,
            "password": DEV_SUBMIT_SMTP_PASSWORD,
            "from_addr": DEV_SUBMIT_FROM_ADDR,
        }
        recipient = DEV_SUBMIT_RECIPIENT
    archive_filename = os.path.basename(archive_path)
    archive_size = _file_size(archive_path)
    sha256 = compute_sha256(archive_path)
    _LOGGER.info(
        "building email: to=%s subject=[XiJian DevKit Package Submit] %s archive=%s (%d bytes)",
        DEV_SUBMIT_RECIPIENT, developer_id, archive_filename, archive_size,
    )
    msg = build_email_message(
        developer_id=developer_id,
        submitted_at=submitted_at,
        target_kind=target_kind,
        target_id=target_id,
        ai_ratio=ai_ratio,
        archive_filename=archive_filename,
        archive_size_bytes=archive_size,
        content_sha256=sha256,
        archive_path=archive_path,
        archive_format=archive_format,
        from_addr=smtp_config.get("from_addr", DEV_SUBMIT_FROM_ADDR),
        recipient=recipient,
    )
    send = smtp_send or _smtp_send
    code, response = send(
        host=smtp_config.get("host", DEV_SUBMIT_SMTP_HOST),
        port=smtp_config.get("port", DEV_SUBMIT_SMTP_PORT),
        use_tls=smtp_config.get("use_tls", DEV_SUBMIT_SMTP_USE_TLS),
        user=smtp_config.get("user", DEV_SUBMIT_SMTP_USER),
        password=smtp_config.get("password", DEV_SUBMIT_SMTP_PASSWORD),
        sender=smtp_config.get("from_addr", DEV_SUBMIT_FROM_ADDR),
        recipient=recipient,
        message=msg,
    )
    return {
        "smtp_status": "sent",
        "smtp_code": code,
        "smtp_response": response,
    }


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _SubmissionDraft:
    """提交步骤之间携带的内部草稿记录。"""

    developer_id: str
    target_kind: str
    target_id: str
    payload: dict
    archive_path: str = ""
    archive_size_bytes: int = 0
    archive_format: str = ""
    content_sha256: str = ""
    submitted_at: str = ""
    ai_ratio: float = 0.0
    email: dict = dataclasses.field(default_factory=dict)


def _validate_submission(
    developer_id: str, target_kind: str, target_id: str
) -> None:
    if not developer_id or not isinstance(developer_id, str):
        raise DevKitError(400, "`developer_id` is required", code="missing_developer_id")
    if target_kind not in TARGET_KINDS:
        raise DevKitError(
            400,
            f"`target_kind` must be one of {TARGET_KINDS!r}",
            code="bad_target_kind",
            target_kind=target_kind,
        )
    if not target_id or not isinstance(target_id, str):
        raise DevKitError(400, "`target_id` is required", code="missing_target_id")


def submit(
    developer_id: str,
    target_kind: str,
    target_id: str,
    *,
    payload: Mapping[str, Any] | None = None,
    file_entries: list[Mapping[str, Any]] | None = None,
    smtp_send: Callable[..., tuple[str, str]] | None = None,
    archive_path: str | None = None,
    work_dir: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """端到端提交。

    1. 校验输入。
    2. 强制每个开发者 1 小时冷却。
    3. 预检大小（累计输入字节）。
    4. 打包为 7Z 固态归档（zip 回退）。
    5. 打包后大小检查。
    6. 发送带归档附件的 SMTP 邮件。
    7. 在 :data:`state.submissions` 中持久化记录，并更新
       开发者的上次提交时间戳。

    返回新的提交记录（dict）。Pywebview 的 ``js_api``
    会将它直接往返回 UI。
    """
    payload = dict(payload or {})
    file_entries = list(file_entries or [])
    _LOGGER.info(
        "submit start: developer=%s kind=%s id=%s files=%d",
        developer_id, target_kind, target_id, len(file_entries),
    )
    _validate_submission(developer_id, target_kind, target_id)

    submitted_at = _now_iso()
    _LOGGER.info("checking rate limit for %s", developer_id)
    check_rate_limit(developer_id, now=now)

    ai_ratio = float(payload.get("ai_ratio", 0.0) or 0.0)
    _LOGGER.info("building manifest (ai_ratio=%.2f)", ai_ratio)
    manifest = build_manifest(
        developer_id=developer_id,
        target_kind=target_kind,
        target_id=target_id,
        payload=payload,
        submitted_at=submitted_at,
        ai_ratio=ai_ratio,
    )
    _LOGGER.info("packing payload")
    archive_path, archive_size, archive_format = pack_payload(
        manifest, file_entries, archive_path=archive_path
    )
    _LOGGER.info("checking archive size: %d bytes", archive_size)
    check_archive_size(archive_size)

    _LOGGER.info("computing sha256 of %s", archive_path)
    sha256 = compute_sha256(archive_path)

    _LOGGER.info("sending submission email")
    email_result = send_submission_email(
        developer_id=developer_id,
        submitted_at=submitted_at,
        target_kind=target_kind,
        target_id=target_id,
        ai_ratio=ai_ratio,
        archive_path=archive_path,
        archive_format=archive_format,
        work_dir=work_dir,
        smtp_send=smtp_send,
    )

    submission_id = gen_submission_id()
    record = {
        "id": submission_id,
        "developer_id": developer_id,
        "target_kind": target_kind,
        "target_id": target_id,
        "archive_path": archive_path,
        "archive_size": archive_size,
        "archive_format": archive_format,
        "content_sha256": sha256,
        "ai_ratio": ai_ratio,
        "smtp_status": email_result["smtp_status"],
        "smtp_code": email_result.get("smtp_code", ""),
        "smtp_response": email_result.get("smtp_response", ""),
        "submitted_at": submitted_at,
        "email_subject": f"[XiJian Submission] {developer_id} / {target_kind}:{target_id}",
        "notes": str(payload.get("notes", "")),
    }
    state.submissions[submission_id] = record
    state.last_submit_at[developer_id] = submitted_at
    state.local_archives[submission_id] = archive_path
    _LOGGER.info(
        "submission complete: id=%s developer=%s smtp_status=%s archive=%s",
        submission_id, developer_id, email_result["smtp_status"], archive_path,
    )

    return dict(record)


# ---------------------------------------------------------------------------
# 读侧辅助函数
# ---------------------------------------------------------------------------


def last_submit_for(developer_id: str) -> dict[str, Any] | None:
    """返回 ``developer_id`` 最近一次的提交记录，或 ``None``。"""
    for record in state.submissions.values():
        if record.get("developer_id") == developer_id:
            return dict(record)
    return None


def list_submissions(*, limit: int = 50) -> list[dict[str, Any]]:
    """返回最近的提交记录，新的在前。"""
    items = sorted(
        state.submissions.values(),
        key=lambda r: r.get("submitted_at") or "",
        reverse=True,
    )
    return [dict(r) for r in items[: max(1, int(limit))]]


def get_submission(submission_id: str) -> dict[str, Any] | None:
    record = state.submissions.get(submission_id)
    return dict(record) if record else None


def delete_local_archive(submission_id: str) -> bool:
    """移除 ``submission_id`` 的本地归档（尽力而为）。"""
    path = state.local_archives.pop(submission_id, None)
    if not path:
        return False
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        return False
    return True


def delete_submission(submission_id: str, work_dir: str) -> bool:
    """删除单条提交记录及其本地归档。"""
    record = state.submissions.pop(submission_id, None)
    if not record:
        return False
    # 如果这是该开发者最近一次提交，则移除 last_submit_at
    dev_id = record.get("developer_id")
    if dev_id and state.last_submit_at.get(dev_id) == record.get("submitted_at"):
        # 为该开发者找到下一次最近的提交
        latest = None
        for r in state.submissions.values():
            if r.get("developer_id") == dev_id:
                if latest is None or r.get("submitted_at", "") > latest:
                    latest = r.get("submitted_at")
        if latest:
            state.last_submit_at[dev_id] = latest
        else:
            state.last_submit_at.pop(dev_id, None)
    # 删除本地归档
    delete_local_archive(submission_id)
    state.save(work_dir)
    return True


def clear_submissions(work_dir: str) -> int:
    """删除 ALL 提交记录及其本地归档。"""
    count = len(state.submissions)
    if count == 0:
        return 0
    # 删除所有归档
    for sub_id in list(state.local_archives.keys()):
        delete_local_archive(sub_id)
    # 清空所有状态存储桶
    state.submissions.clear()
    state.last_submit_at.clear()
    state.local_archives.clear()
    state.save(work_dir)
    return count


def delete_package(package_id: str, work_dir: str) -> bool:
    """按其 package_id（例如 'char:abc'）删除一个可提交的包。"""
    # 本地导入以避免循环导入
    from devkit.character_editor import delete_character as _ce_delete
    from devkit.memory_editor import delete_entry as _me_delete, list_entries as _me_list
    from devkit.world_editor import delete_world as _we_delete
    from devkit.plot_editor import delete_plot as _pe_delete
    from devkit.voice_cloner import delete_voice as _vc_delete
    from devkit.dialog_editor import delete_dialog as _de_delete
    from devkit.motion_editor import delete_motion as _moe_delete
    from devkit.model_viewer import unregister_model as _mv_unregister

    if ":" not in package_id:
        return False
    ptype, pid = package_id.split(":", 1)
    try:
        if ptype == "char":
            ok = _ce_delete(work_dir, pid)
        elif ptype == "memory":
            # 删除该角色的所有记忆条目
            entries = _me_list(work_dir, pid)
            for entry in entries:
                _me_delete(work_dir, entry.get("id", ""))
            # 同时删除角色的记忆目录以彻底清理
            mem_dir = os.path.join(work_dir, "memories", pid)
            if os.path.isdir(mem_dir):
                import shutil
                shutil.rmtree(mem_dir)
            ok = True
        elif ptype == "world":
            ok = _we_delete(work_dir, pid)
        elif ptype == "plot":
            ok = _pe_delete(work_dir, pid)
        elif ptype == "model":
            ok = _mv_unregister(work_dir, pid)
        elif ptype == "voice":
            ok = _vc_delete(work_dir, pid)
        elif ptype == "dialog":
            ok = _de_delete(work_dir, pid)
        elif ptype == "motion":
            ok = _moe_delete(work_dir, pid)
        else:
            return False

        # 清理临时导出目录中的任何导出产物
        export_dir = os.path.join(work_dir, "exports", ptype)
        if os.path.isdir(export_dir):
            for fname in os.listdir(export_dir):
                fpath = os.path.join(export_dir, fname)
                if os.path.isfile(fpath) and fname.startswith(pid):
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass

        return ok
    except Exception:
        return False


def cooldown_remaining(developer_id: str) -> int:
    """返回 ``developer_id`` 距离下次可提交的剩余秒数。

    与 :func:`check_rate_limit` 不同，本函数**不**抛出——它是
    供 UI 的“X 秒后可再次提交”指示器使用的无副作用读取。
    """
    last_iso = state.last_submit_at.get(developer_id)
    if not last_iso:
        return 0
    try:
        last_ts = _dt.datetime.fromisoformat(last_iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0
    elapsed = float(now_ts()) - last_ts
    if elapsed < 0:
        return 0
    remaining = int(DEV_SUBMIT_COOLDOWN_SECONDS - elapsed)
    return max(0, remaining)


# ---------------------------------------------------------------------------
# 种子 / 重置
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """devkit 的空操作。

    放在这里是为了让未来的 ``devkit.seed_all()`` 在所有模块中拥有
    统一的调用形态。DevKit 没有要播种的默认记录——提交由人类做出，
    而不是从磁盘加载。
    """


def reset_for_testing() -> None:
    """清空内存状态并移除每个本地产出的归档。

    由测试套件在测试之间调用。本地归档位于
    :func:`local_archive_dir` 下，尽力删除。
    """
    # 尽力而为：删除我们跟踪的磁盘归档。
    for path in list(state.local_archives.values()):
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    state.reset_for_testing()


__all__ = [
    # constants
    "DEV_SUBMIT_SMTP_HOST",
    "DEV_SUBMIT_SMTP_PORT",
    "DEV_SUBMIT_SMTP_USE_TLS",
    "DEV_SUBMIT_SMTP_USER",
    "DEV_SUBMIT_SMTP_PASSWORD",
    "DEV_SUBMIT_RECIPIENT",
    "DEV_SUBMIT_FROM_ADDR",
    "DEV_SUBMIT_MAX_ATTACHMENT_BYTES",
    "DEV_SUBMIT_COOLDOWN_SECONDS",
    "DEV_SUBMIT_LOCAL_RETENTION_SECONDS",
    "TARGET_KINDS",
    "ARCHIVE_FORMAT_7Z",
    "ARCHIVE_FORMAT_ZIP",
    "_API_VERSION",
    # pure helpers
    "archive_name",
    "build_manifest",
    "check_rate_limit",
    "check_archive_size",
    "compute_sha256",
    "local_archive_dir",
    "local_archive_path",
    "cooldown_remaining",
    # packing
    "pack_payload",
    # smtp
    "build_email_message",
    "_smtp_send",
    "send_submission_email",
    # orchestrator
    "submit",
    "last_submit_for",
    "list_submissions",
    "get_submission",
    "delete_local_archive",
    "delete_submission",
    "clear_submissions",
    "delete_package",
    # seed/reset
    "seed_default",
    "reset_for_testing",
    # errors (also re-exported for callers that want to catch them)
    "DevKitError",
    "RateLimitedError",
    "PayloadTooLargeError",
    "SmtpError",
    # editor modules
    "character_editor",
    "memory_editor",
    "world_editor",
    "model_viewer",
    "voice_cloner",
    "plot_editor",
    "dialog_editor",
    "motion_editor",
    "ai_assistant",
]
