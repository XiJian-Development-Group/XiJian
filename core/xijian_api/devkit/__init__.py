"""Developer Kit submission pipeline (C5 in the function list v2.2).

This module is the **brain** of the submission path the DevKit UI
takes when a developer clicks 「提交」.  The whole flow is
intentionally server-less:

    local payload  ──►  pack  ──►  attach to email  ──►  SMTP

The DevKit is a **standalone** Pywebview app — it does not share a
Flask server with the main API and never makes an HTTP call against
it.  UI <-> Python talks happen through ``pywebview.js_api`` (see
:mod:`xijian_api.devkit.api`).

This package owns three in-memory buckets (mirrored in
:mod:`xijian_api.devkit.state`):

* ``submissions``        — per-submission record, keyed by id.
* ``last_submit_at``     — per-developer last-submit timestamp
  (ISO 8601 string, used for the 1-hour rate-limit).
* ``local_archives``     — file-path of the most recent 7Z archive
  produced for each submission, so the cleanup job can find it.

Side effects
------------

* :func:`pack_payload` writes a 7Z solid archive to a temporary path
  (preferred) or falls back to ``zipfile`` if ``py7zr`` is not
  installed.  The fallback is logged as a warning so operators know
  to install ``py7zr`` for the spec-mandated high-compression format.
* :func:`send_submission_email` opens an SMTP connection, attaches
  the archive and sends.  All SMTP credentials are read from
  module-level constants at the top of this file (see
  *Environment variables* below).
* :func:`submit` orchestrates the full flow and returns a small
  dict the JS API can serialize back to the UI.

Rate limit
----------

Each ``developer_id`` may submit at most **once per hour**.  The
cooldown is enforced via :data:`DEV_SUBMIT_COOLDOWN_SECONDS`; the
last-submit timestamp is persisted in ``state.last_submit_at``.

Size limit
----------

The archive (after packing) must be **≤ 1200 MB** by macOS
default units (``1000 KB = 1 MB``, ``1000 MB = 1 GB``), i.e.
:data:`DEV_SUBMIT_MAX_ATTACHMENT_BYTES` = 1 200 000 000 bytes.

The pre-pack payload size is also bounded — we refuse to even
*start* packing if the cumulative input exceeds the limit, because
7Z streaming through ``py7zr`` can be expensive on multi-GB inputs.

Environment variables
---------------------

All hard-coded constants accept an env-var override of the form
``XIJIAN_DEV_<NAME>`` so deployments / CI can inject secrets
without editing the source:

==============================  ==============================
Constant                        Env override
==============================  ==============================
``DEV_SUBMIT_SMTP_HOST``        ``XIJIAN_DEV_SMTP_HOST``
``DEV_SUBMIT_SMTP_PORT``        ``XIJIAN_DEV_SMTP_PORT``
``DEV_SUBMIT_SMTP_USE_TLS``     ``XIJIAN_DEV_SMTP_USE_TLS``
``DEV_SUBMIT_SMTP_USER``        ``XIJIAN_DEV_SMTP_USER``
``DEV_SUBMIT_SMTP_PASSWORD``    ``XIJIAN_DEV_SMTP_PASSWORD``
``DEV_SUBMIT_RECIPIENT``        ``XIJIAN_DEV_RECIPIENT``
``DEV_SUBMIT_FROM_ADDR``        ``XIJIAN_DEV_FROM_ADDR``
``DEV_SUBMIT_MAX_BYTES``        ``XIJIAN_DEV_MAX_BYTES``
``DEV_SUBMIT_COOLDOWN``         ``XIJIAN_DEV_COOLDOWN_SECONDS``
==============================  ==============================

Test surface
------------

Pure helpers + side-effecting entry points, all written so a test
can drive them with ``monkeypatch``:

* :func:`check_rate_limit`
* :func:`check_archive_size`
* :func:`archive_name`
* :func:`build_manifest`
* :func:`pack_payload`
* :func:`send_submission_email`  (with injectable :func:`_smtp_send`)
* :func:`submit`                 (with injectable :func:`_smtp_send`)
* :func:`last_submit_for`
* :func:`reset_for_testing`

Production callers route through the Pywebview ``js_api`` exposed
by :class:`xijian_api.devkit.api.DevKitApi`.
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

from xijian_api.devkit import state
from xijian_api.errors import ApiError
from xijian_api.utils.ids import gen_submission_id
from xijian_api.utils.time import iso_now, now_ts


_LOGGER = logging.getLogger("xijian_api.devkit")


# ---------------------------------------------------------------------------
# Hard-coded configuration (replace before deploy)
# ---------------------------------------------------------------------------

#: SMTP server host.  Placeholder — **must be replaced before deploy**.
DEV_SUBMIT_SMTP_HOST: str = os.environ.get("XIJIAN_DEV_SMTP_HOST", "smtp.example.com")
#: SMTP server port.  587 = STARTTLS submission port.
DEV_SUBMIT_SMTP_PORT: int = int(os.environ.get("XIJIAN_DEV_SMTP_PORT", "587") or "587")
#: Whether to use STARTTLS on the SMTP connection.
DEV_SUBMIT_SMTP_USE_TLS: bool = os.environ.get("XIJIAN_DEV_SMTP_USE_TLS", "1") not in (
    "0",
    "false",
    "no",
)
#: SMTP authentication user.
DEV_SUBMIT_SMTP_USER: str = os.environ.get(
    "XIJIAN_DEV_SMTP_USER", "xijian-dev@example.com"
)
#: SMTP authentication password.  **Replace before deploy.**
DEV_SUBMIT_SMTP_PASSWORD: str = os.environ.get(
    "XIJIAN_DEV_SMTP_PASSWORD", "REPLACE_BEFORE_DEPLOY"
)
#: Hard-coded developer-group recipient (no server, no discovery).
DEV_SUBMIT_RECIPIENT: str = os.environ.get(
    "XIJIAN_DEV_RECIPIENT", "xijian-submissions@example.com"
)
#: From address on the outgoing email (usually same as SMTP user).
DEV_SUBMIT_FROM_ADDR: str = os.environ.get(
    "XIJIAN_DEV_FROM_ADDR", DEV_SUBMIT_SMTP_USER
)

#: Hard limit on attachment size in bytes.  1200 MB by macOS
#: default units (``1000 KB = 1 MB``, ``1000 MB = 1 GB``) =
#: ``1200 × 1000 × 1000 = 1 200 000 000``.
DEV_SUBMIT_MAX_ATTACHMENT_BYTES: int = int(
    os.environ.get("XIJIAN_DEV_MAX_BYTES", "1200000000") or "1200000000"
)
#: Per-developer cooldown between submissions.  3600s = 1 hour.
DEV_SUBMIT_COOLDOWN_SECONDS: int = int(
    os.environ.get("XIJIAN_DEV_COOLDOWN_SECONDS", "3600") or "3600"
)
#: Local archive retention.  Archives are deleted after this many
#: seconds unless :func:`keep_archive` is called.  Default: 7 days.
DEV_SUBMIT_LOCAL_RETENTION_SECONDS: int = int(
    os.environ.get("XIJIAN_DEV_LOCAL_RETENTION", "604800") or "604800"
)
#: Directory holding local 7Z archives.  ``None`` ⇒ use
#: ``tempfile.gettempdir() / xijian_devkit``.
_DEV_SUBMIT_LOCAL_DIR: str | None = os.environ.get("XIJIAN_DEV_LOCAL_DIR")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Archive format tag we write into the manifest.  Lets the receiver
#: detect a fallback-zip submission at a glance.
ARCHIVE_FORMAT_7Z = "7z-solid"
ARCHIVE_FORMAT_ZIP = "zip"

#: Submission kinds accepted by :func:`submit`.
TARGET_KINDS: tuple[str, ...] = ("world", "character", "plot")


# ---------------------------------------------------------------------------
# Resource locators (PyInstaller-aware)
# ---------------------------------------------------------------------------


def ui_dir() -> "Path":
    """Return the directory holding the DevKit UI assets (``index.html`` etc.).

    In normal ``pip install`` runs this is the ``ui/`` folder shipped
    alongside this ``__init__.py``.  When the package is frozen by
    PyInstaller (``sys.frozen`` set), PyInstaller extracts bundled
    ``datas`` to ``sys._MEIPASS`` — and we mirror the package layout
    there, so the same relative path works.

    This indirection lets the window entry point load ``ui/index.html``
    from both source and binary distributions without conditional code
    in :mod:`xijian_api.devkit.main`.
    """
    import pathlib
    import sys

    if getattr(sys, "frozen", False):
        # PyInstaller: data was bundled under the same relative path
        # inside sys._MEIPASS (see ``xijian-devkit.spec``).
        return pathlib.Path(sys._MEIPASS) / "xijian_api" / "devkit" / "ui"
    return pathlib.Path(__file__).resolve().parent / "ui"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DevKitError(ApiError):
    """Base for DevKit-specific errors.

    Inherits :class:`xijian_api.errors.ApiError` so the JSON-API
    contract is consistent across the project, even though the DevKit
    itself never emits HTTP envelopes — the UI receives the error as
    a plain dict via :func:`xijian_api.devkit.api.serialize_error`.
    """

    def __init__(self, status: int, message: str, code: str, **extra: Any) -> None:
        super().__init__(status, message, "server_error", code=code, **extra)


class RateLimitedError(DevKitError):
    """429 — developer_id is within the cooldown window."""

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
    """413 — archive exceeds the 1200 MB limit."""

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
    """502 — SMTP submission failed.

    The ``category`` field names the failure mode (one of
    ``auth_failed``, ``connection_failed``, ``tls_failed``,
    ``other``); the ``response`` field carries the raw SMTP reply
    when available.
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
# Pure helpers
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
    """Return an ISO 8601 UTC string suitable for filenames and emails."""
    return iso_now()


def archive_name(developer_id: str, *, now: _dt.datetime | None = None) -> str:
    """Return the on-disk archive filename.

    Format: ``<developer_id>__<iso8601_utc>.7z`` — the underscore
    separator keeps filenames parseable for the receiving end.
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
    """Build the JSON manifest that goes at the root of every archive.

    The manifest lets the receiving end compute ``ai_ratio``, verify
    the SHA-256 of the archive, and audit who-submitted-what-when
    without unpacking the whole 7Z.
    """
    files = payload.get("files") or []
    if not isinstance(files, list):
        files = []
    return {
        "schema": "xijian.devkit.submission/v1",
        "developer_id": developer_id,
        "submitted_at": submitted_at,
        "target_kind": target_kind,
        "target_id": target_id,
        "ai_ratio": float(ai_ratio),
        "files": [str(f) for f in files],
        "notes": str(payload.get("notes", "")),
    }


def check_rate_limit(developer_id: str, *, now: float | None = None) -> int:
    """Return the seconds remaining before ``developer_id`` can submit again.

    Raises :class:`RateLimitedError` when the cooldown has not elapsed.
    The ``now`` override lets tests fast-forward the clock.
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
        # Clock went backwards — treat as a fresh start.
        return 0
    remaining = int(DEV_SUBMIT_COOLDOWN_SECONDS - elapsed)
    if remaining > 0:
        raise RateLimitedError(remaining, last_submit_at=last_iso)
    return 0


def check_archive_size(size_bytes: int) -> None:
    """Raise :class:`PayloadTooLargeError` if ``size_bytes`` is *strictly over* the cap.

    Strict ``>`` keeps the raw budget check clean: callers that genuinely
    intend to fit under 1200 MB call this before adding the manifest.

    The UI uses :func:`preview_size_payload` (a stricter helper) which
    also flags payloads that *equal* the cap — those can't fit a manifest
    on top, so the user shouldn't be allowed to submit them.
    """
    if size_bytes > DEV_SUBMIT_MAX_ATTACHMENT_BYTES:
        raise PayloadTooLargeError(
            size_bytes=size_bytes,
            max_bytes=DEV_SUBMIT_MAX_ATTACHMENT_BYTES,
        )


def preview_size_payload(size_bytes: int) -> tuple[bool, str]:
    """UI-side pre-flight check.

    Returns ``(ok, message)``.  ``ok=False`` whenever the payload would not
    fit even after subtracting the manifest reservation (a few KB) and
    the 7Z stream overhead.  Practically this means any size at or
    above ``DEV_SUBMIT_MAX_ATTACHMENT_BYTES`` is rejected up-front.
    """
    if size_bytes >= DEV_SUBMIT_MAX_ATTACHMENT_BYTES:
        return False, (
            f"selected payload ({size_bytes} bytes) exceeds limit "
            f"{DEV_SUBMIT_MAX_ATTACHMENT_BYTES} bytes (manifest + 7Z overhead "
            "need a few KB of headroom)"
        )
    return True, "ok"


def compute_sha256(path: str) -> str:
    """SHA-256 hex digest of the file at ``path``.  Streamed, constant memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def local_archive_dir() -> str:
    """Return (and lazily create) the directory that holds archives."""
    base = _DEV_SUBMIT_LOCAL_DIR or os.path.join(tempfile.gettempdir(), "xijian_devkit")
    os.makedirs(base, exist_ok=True)
    return base


def local_archive_path(name: str) -> str:
    return os.path.join(local_archive_dir(), name)


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _cumulative_size(file_entries: Iterable[Mapping[str, Any]]) -> int:
    """Sum the ``size`` fields of file entries.  Used for the pre-pack check."""
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
    """Pack the manifest + every file entry into a 7Z solid archive.

    Returns ``(archive_path, archive_size_bytes, archive_format)``.
    When ``py7zr`` is not installed we fall back to ``zipfile`` with
    the highest compression level — the receiving end detects the
    format from the manifest and from the file extension.

    Parameters
    ----------
    manifest:
        The manifest dict (built by :func:`build_manifest`).
    file_entries:
        Each entry is a mapping with ``path`` (filesystem path),
        ``arcname`` (optional name inside the archive) and
        ``size`` (optional pre-flight size hint).
    archive_path:
        Destination path.  Defaults to
        :func:`local_archive_path` + :func:`archive_name`.
    """
    pre_size = _cumulative_size(file_entries)
    if pre_size > DEV_SUBMIT_MAX_ATTACHMENT_BYTES:
        # We don't even start packing — there's no way 7Z / zip can
        # produce a smaller output than the sum of inputs (modest
        # compression gains aside) and we don't want to burn CPU on
        # a doomed submission.
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

    if py7zr is not None:
        with py7zr.SevenZipFile(target, mode="solid") as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for entry in file_entries:
                src = entry.get("path")
                if not src or not os.path.isfile(src):
                    continue
                arcname = entry.get("arcname") or os.path.basename(src)
                archive.write(src, arcname)
        return target, _file_size(target), ARCHIVE_FORMAT_7Z

    # Fallback to zip when py7zr is not installed.  We log a
    # WARNING (not just info) so operators see it in their console
    # and remember to install py7zr.
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
                continue
            arcname = entry.get("arcname") or os.path.basename(src)
            zf.write(src, arcname)
    return target, _file_size(target), ARCHIVE_FORMAT_ZIP


# ---------------------------------------------------------------------------
# SMTP — the only network call in the entire pipeline
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
    """Send a single email via SMTP.

    Returns ``(smtp_status, smtp_response)``.  Raises
    :class:`SmtpError` on any failure; the ``category`` field
    names the failure mode so callers can map it to a status string.

    Tests monkeypatch this function (or :func:`_smtp_send`) to
    capture the outgoing ``message`` without actually talking to
    a server.
    """
    import smtplib
    import ssl

    smtp: smtplib.SMTP | None = None
    try:
        try:
            smtp = smtplib.SMTP(host, port, timeout=30)
        except (OSError, smtplib.SMTPConnectError) as exc:
            raise SmtpError("connection_failed", str(exc)) from exc
        try:
            if use_tls:
                try:
                    smtp.starttls(context=ssl.create_default_context())
                except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
                    raise SmtpError("tls_failed", str(exc)) from exc
            try:
                smtp.login(user, password)
            except smtplib.SMTPAuthenticationError as exc:
                raise SmtpError("auth_failed", str(exc)) from exc
            except smtplib.SMTPException as exc:
                raise SmtpError("auth_failed", str(exc)) from exc
            refused = smtp.sendmail(sender, [recipient], message.as_string())
            if refused:
                raise SmtpError(
                    "other",
                    f"recipient refused: {refused}",
                )
            code, response = smtp.noop()
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
) -> MIMEMultipart:
    """Build the multipart MIME message sent to the developer group."""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[XiJian Submission] {developer_id} / {target_kind}:{target_id}"
    msg["From"] = DEV_SUBMIT_FROM_ADDR
    msg["To"] = DEV_SUBMIT_RECIPIENT
    msg["Date"] = format_datetime(_dt.datetime.now(_dt.timezone.utc))

    body_lines = [
        "developer_id:    " + developer_id,
        "submitted_at:    " + submitted_at,
        "target_kind:     " + target_kind,
        "target_id:       " + target_id,
        "ai_ratio:        " + f"{ai_ratio:.2f}",
        "archive_format:  " + archive_format,
        f"attachment:      {archive_filename} ({archive_size_bytes} bytes, "
        f"~{archive_size_bytes / 1_000_000:.2f} MB)",
        "content_sha256:  " + content_sha256,
        "",
        "— 自动由隙间开发工具生成",
    ]
    msg.attach(MIMEText("\n".join(body_lines), "plain", "utf-8"))

    ctype = (
        "application/x-7z-compressed"
        if archive_format == ARCHIVE_FORMAT_7Z
        else "application/zip"
    )
    with open(archive_path, "rb") as fh:
        part = MIMEApplication(fh.read(), Name=archive_filename)
    # ``MIMEApplication`` sets a default ``Content-Type`` during
    # construction; replacing the header via ``part["Content-Type"]``
    # leaves two copies on Python 3.13.  Drop the old one and add the
    # new one so ``get_content_type()`` returns the archive's MIME.
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
    smtp_send: Callable[..., tuple[str, str]] | None = None,
) -> dict[str, str]:
    """Build + send the submission email.  Returns the SMTP status dict.

    Tests inject ``smtp_send`` to capture the email without touching
    the network.  When omitted, :func:`_smtp_send` is used.
    """
    archive_filename = os.path.basename(archive_path)
    archive_size = _file_size(archive_path)
    sha256 = compute_sha256(archive_path)
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
    )
    send = smtp_send or _smtp_send
    code, response = send(
        host=DEV_SUBMIT_SMTP_HOST,
        port=DEV_SUBMIT_SMTP_PORT,
        use_tls=DEV_SUBMIT_SMTP_USE_TLS,
        user=DEV_SUBMIT_SMTP_USER,
        password=DEV_SUBMIT_SMTP_PASSWORD,
        sender=DEV_SUBMIT_FROM_ADDR,
        recipient=DEV_SUBMIT_RECIPIENT,
        message=msg,
    )
    return {
        "smtp_status": "sent",
        "smtp_code": code,
        "smtp_response": response,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _SubmissionDraft:
    """Internal scratch record carried between submit steps."""

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
    now: float | None = None,
) -> dict[str, Any]:
    """End-to-end submission.

    1. Validate inputs.
    2. Enforce per-developer 1-hour cooldown.
    3. Pre-flight size check (cumulative input bytes).
    4. Pack into 7Z solid archive (zip fallback).
    5. Post-pack size check.
    6. Send SMTP email with the archive attached.
    7. Persist a record in :data:`state.submissions` and bump the
       developer's last-submit timestamp.

    Returns the new submission record (dict).  Pywebview's ``js_api``
    round-trips this straight back to the UI.
    """
    payload = dict(payload or {})
    file_entries = list(file_entries or [])
    _validate_submission(developer_id, target_kind, target_id)

    submitted_at = _now_iso()
    check_rate_limit(developer_id, now=now)

    ai_ratio = float(payload.get("ai_ratio", 0.0) or 0.0)
    manifest = build_manifest(
        developer_id=developer_id,
        target_kind=target_kind,
        target_id=target_id,
        payload=payload,
        submitted_at=submitted_at,
        ai_ratio=ai_ratio,
    )
    archive_path, archive_size, archive_format = pack_payload(
        manifest, file_entries, archive_path=archive_path
    )
    check_archive_size(archive_size)

    sha256 = compute_sha256(archive_path)

    email_result = send_submission_email(
        developer_id=developer_id,
        submitted_at=submitted_at,
        target_kind=target_kind,
        target_id=target_id,
        ai_ratio=ai_ratio,
        archive_path=archive_path,
        archive_format=archive_format,
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

    return dict(record)


# ---------------------------------------------------------------------------
# Read-side helpers
# ---------------------------------------------------------------------------


def last_submit_for(developer_id: str) -> dict[str, Any] | None:
    """Return the most recent submission record for ``developer_id`` or ``None``."""
    for record in state.submissions.values():
        if record.get("developer_id") == developer_id:
            return dict(record)
    return None


def list_submissions(*, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent submission records, newest first."""
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
    """Remove the local archive for ``submission_id`` (best-effort)."""
    path = state.local_archives.pop(submission_id, None)
    if not path:
        return False
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        return False
    return True


def cooldown_remaining(developer_id: str) -> int:
    """Return seconds remaining until ``developer_id`` can submit again.

    Unlike :func:`check_rate_limit` this does **not** raise — it's a
    non-mutating read for the UI's "X 秒后可再次提交" indicator.
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
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """No-op for the devkit.

    Lives here so a future ``devkit.seed_all()`` has a uniform call
    shape across all modules.  The DevKit has no default records to
    seed — submissions are made by humans, not loaded from disk.
    """


def reset_for_testing() -> None:
    """Wipe in-memory state and remove every locally produced archive.

    Called by the test suite between tests.  Local archives live
    under :func:`local_archive_dir` and are best-effort deleted.
    """
    # Best-effort: delete the on-disk archives we tracked.
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
    # seed/reset
    "seed_default",
    "reset_for_testing",
    # errors (also re-exported for callers that want to catch them)
    "DevKitError",
    "RateLimitedError",
    "PayloadTooLargeError",
    "SmtpError",
]
