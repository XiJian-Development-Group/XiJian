"""Pywebview ``js_api`` bridge for the DevKit.

The DevKit window is a plain HTML page; the only way to talk to
Python from JavaScript is through ``window.pywebview.api`` — a
proxy created automatically by pywebview around an instance of this
:class:`DevKitApi` class.

Design contract
---------------

* Every public method on ``DevKitApi`` returns a **plain dict**.
  pywebview round-trips ``dict`` to the JS side as a JS object;
  exceptions raised in Python land in the JS ``Promise.catch`` as
  generic errors and lose useful context.  To keep semantics
  structured, every call is wrapped with :func:`serialize_call`:

  * success → ``{"ok": True, "data": <method return value>}``
  * failure → ``{"ok": False, "error": {...}, "status": <int>}``

* Methods are **synchronous** on the Python side; pywebview exposes
  them to JS as :js:class:`pywebview.api` functions whose return
  value is a :js:class:`Promise` resolving to the dict.  UI code
  can ``await api.submit(...)`` and get either a success record or
  a structured error object.

* Errors that surface from the orchestrator (:mod:`xijian_api.devkit`)
  all derive from :class:`xijian_api.devkit.DevKitError` (which
  derives from :class:`xijian_api.errors.ApiError`).  They carry the
  OAI-style ``status``/``type_``/``code`` triple and any extra
  kwargs; :func:`serialize_error` flattens them so JS can display a
  friendly message without learning the Python exception hierarchy.

Authentication model
--------------------

The DevKit does not implement a full auth flow (the function list
v2.2 says it ships without a private server).  It accepts whatever
``developer_id`` the user types in, persists it for the session, and
trusts that the receiving email server bounces obviously-bogus IDs.
A future patch can wire a PGP-signed challenge if needed.

Public API surface (exposed to JS)
----------------------------------

==============================  =============================================
JS call                          Purpose
==============================  =============================================
``api.whoami()``                 return static config (recipient, host, ...)
``api.ping()``                   lightweight liveness probe (``{"ok": True}``)
``api.login(developer_id)``      set the active developer for this window
``api.logout()``                 clear the active developer
``api.current_developer()``      return the active developer or ``null``
``api.target_kinds()``           return the list of acceptable ``target_kind``
``api.cooldown_for(developer_id)`` return seconds remaining (int >= 0)
``api.last_submit(developer_id)``  return last submission record or ``null``
``api.list_submissions(limit)``  return recent submission records
``api.preview_size(payload)``    compute cumulative bytes for a file list
``api.submit(...)``              run the full submission pipeline
``api.delete_local(id)``         remove a local archive (best-effort)
==============================  =============================================
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Any

from xijian_api.devkit import (
    ARCHIVE_FORMAT_7Z,
    TARGET_KINDS,
    DevKitError,
    DEV_SUBMIT_COOLDOWN_SECONDS,
    DEV_SUBMIT_MAX_ATTACHMENT_BYTES,
    preview_size_payload,
    DEV_SUBMIT_RECIPIENT,
    DEV_SUBMIT_SMTP_HOST,
    DEV_SUBMIT_SMTP_PORT,
    DEV_SUBMIT_SMTP_USE_TLS,
    DEV_SUBMIT_SMTP_USER,
    archive_name,
    check_archive_size,
    check_rate_limit,
    cooldown_remaining,
    delete_local_archive,
    get_submission,
    last_submit_for,
    list_submissions,
    submit,
)


_LOGGER = logging.getLogger("xijian_api.devkit.api")
_API_VERSION = "xijian.devkit.api/v1"


# ---------------------------------------------------------------------------
# Error → dict serialisation
# ---------------------------------------------------------------------------


def serialize_error(err: Exception) -> dict[str, Any]:
    """Convert any exception into the standard ``{"ok": False, ...}`` dict.

    Handles ``DevKitError`` (and its subclasses) by reading the OAI
    triple (status / type_ / code) plus all the original kwargs.
    Anything else becomes a generic 500.
    """
    if isinstance(err, DevKitError):
        return {
            "ok": False,
            "status": int(err.status),
            "type": err.type_,
            "code": err.code or "devkit_error",
            "message": err.message,
            "details": dict(err.extra or {}),
        }
    return {
        "ok": False,
        "status": 500,
        "type": "server_error",
        "code": "internal_error",
        "message": str(err) or type(err).__name__,
        "details": {"exception": type(err).__name__},
    }


def _ok(data: Any) -> dict[str, Any]:
    """Build a successful response dict."""
    return {"ok": True, "data": data}


def _serialize_call(method):
    """Decorator: catch every exception a public method might raise.

    Every public method on :class:`DevKitApi` is wrapped so an
    unhandled error never escapes into pywebview's machinery — the
    UI always receives a structured dict it can render.
    """

    def wrapper(self, *args, **kwargs):
        try:
            value = method(self, *args, **kwargs)
        except DevKitError as err:
            _LOGGER.warning(
                "DevKitApi.%s failed (%s): %s",
                method.__name__,
                getattr(err, "code", "?"),
                err.message,
            )
            return serialize_error(err)
        except Exception as exc:  # noqa: BLE001 — must not raise
            _LOGGER.exception(
                "DevKitApi.%s unexpected failure", method.__name__
            )
            return serialize_error(exc)
        return _ok(value)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# Public API class — bound to ``window.pywebview.api`` at runtime
# ---------------------------------------------------------------------------


class DevKitApi:
    """The Python side of the DevKit UI bridge.

    Thread-safety: pywebview invokes ``js_api`` methods on the GUI
    thread.  The underlying ``submit`` performs blocking I/O (file
    I/O + SMTP).  We accept that the UI freezes during a submission
    because the alternative (spawning a worker thread and yielding
    back via ``evaluate_js``) is significantly more code for a flow
    that runs at most once per hour.

    Configuration knobs are read **once** at import time from
    :mod:`xijian_api.devkit` module-level constants; tests can
    monkeypatch them before constructing the API.
    """

    def __init__(self) -> None:
        # Per-window session state.  Reset every time the window opens.
        self._lock = threading.Lock()
        self._active_developer: str | None = None

    # --- meta ----------------------------------------------------------

    @_serialize_call
    def whoami(self) -> dict[str, Any]:
        """Return static configuration the UI renders in the header."""
        return {
            "api_version": _API_VERSION,
            "cooldown_seconds": int(DEV_SUBMIT_COOLDOWN_SECONDS),
            "max_attachment_bytes": int(DEV_SUBMIT_MAX_ATTACHMENT_BYTES),
            "max_attachment_mb": int(DEV_SUBMIT_MAX_ATTACHMENT_BYTES) // 1_000_000,
            "smtp_host": DEV_SUBMIT_SMTP_HOST,
            "smtp_port": int(DEV_SUBMIT_SMTP_PORT),
            "smtp_use_tls": bool(DEV_SUBMIT_SMTP_USE_TLS),
            "smtp_user": DEV_SUBMIT_SMTP_USER,
            "recipient": DEV_SUBMIT_RECIPIENT,
            "target_kinds": list(TARGET_KINDS),
            "preferred_archive_format": ARCHIVE_FORMAT_7Z,
        }

    @_serialize_call
    def ping(self) -> dict[str, Any]:
        """Liveness probe (``{"ok": True, "data": {"pong": true}}``)."""
        return {"pong": True, "active_developer": self._active_developer}

    # --- session -------------------------------------------------------

    @_serialize_call
    def login(self, developer_id: Any) -> dict[str, Any]:
        """Set the active developer for this window."""
        if not isinstance(developer_id, str) or not developer_id.strip():
            raise DevKitError(
                400,
                "`developer_id` must be a non-empty string",
                code="missing_developer_id",
            )
        cleaned = developer_id.strip()
        with self._lock:
            self._active_developer = cleaned
        return {"developer_id": cleaned}

    @_serialize_call
    def logout(self) -> dict[str, Any]:
        """Clear the active developer."""
        with self._lock:
            previous = self._active_developer
            self._active_developer = None
        return {"previous": previous}

    @_serialize_call
    def current_developer(self) -> dict[str, Any]:
        """Return the active developer (or ``null``)."""
        return {"developer_id": self._active_developer}

    @_serialize_call
    def target_kinds(self) -> list[str]:
        """Return the list of ``target_kind`` the orchestrator accepts."""
        return list(TARGET_KINDS)

    # --- read-side -----------------------------------------------------

    @_serialize_call
    def cooldown_for(self, developer_id: Any) -> int:
        """Return seconds until ``developer_id`` can submit again (≥ 0)."""
        if not isinstance(developer_id, str) or not developer_id:
            raise DevKitError(
                400,
                "`developer_id` must be a non-empty string",
                code="missing_developer_id",
            )
        return cooldown_remaining(developer_id)

    @_serialize_call
    def last_submit(self, developer_id: Any) -> dict[str, Any] | None:
        """Return the most recent submission record for ``developer_id``.

        Returns ``null`` if the developer has never submitted.
        """
        if not isinstance(developer_id, str) or not developer_id:
            raise DevKitError(
                400,
                "`developer_id` must be a non-empty string",
                code="missing_developer_id",
            )
        return last_submit_for(developer_id)

    @_serialize_call
    def list_submissions(self, limit: Any = 50) -> list[dict[str, Any]]:
        """Return the most-recent submission records (newest first)."""
        try:
            n = int(limit) if limit is not None else 50
        except (TypeError, ValueError) as exc:
            raise DevKitError(
                400,
                "`limit` must be an integer",
                code="bad_limit",
            ) from exc
        return list_submissions(limit=n)

    @_serialize_call
    def get_submission(self, submission_id: Any) -> dict[str, Any] | None:
        """Return one submission record by id (or ``null``)."""
        if not isinstance(submission_id, str) or not submission_id:
            raise DevKitError(
                400,
                "`submission_id` must be a non-empty string",
                code="missing_submission_id",
            )
        return get_submission(submission_id)

    # --- pre-flight ----------------------------------------------------

    @_serialize_call
    def preview_size(self, file_entries: Any) -> dict[str, Any]:
        """Compute the cumulative bytes of ``file_entries``.

        Used by the UI to display "选定文件 X / 1200 MB" without
        round-tripping through the orchestrator on every selection.

        Note: the data-level ``ok`` flag here is *stricter* than the raw
        :func:`check_archive_size` helper — it returns ``False`` as soon
        as the payload reaches the cap, because the manifest + 7Z stream
        overhead would still push the archive over the SMTP attachment
        limit.  This is the safe behaviour the UI should surface to the
        user.
        """
        if not isinstance(file_entries, list):
            raise DevKitError(
                400,
                "`file_entries` must be a list",
                code="bad_file_entries",
            )
        total = 0
        for entry in file_entries:
            if isinstance(entry, Mapping):
                try:
                    total += int(entry.get("size") or 0)
                except (TypeError, ValueError):
                    continue
        ok, message = preview_size_payload(int(total))
        return {
            "total_bytes": int(total),
            "total_mb": round(int(total) / 1_000_000, 3),
            "max_bytes": int(DEV_SUBMIT_MAX_ATTACHMENT_BYTES),
            "max_mb": int(DEV_SUBMIT_MAX_ATTACHMENT_BYTES) // 1_000_000,
            "ok": bool(ok),
            "message": message,
        }

    # --- write-side ----------------------------------------------------

    @_serialize_call
    def submit(
        self,
        developer_id: Any = None,
        target_kind: Any = None,
        target_id: Any = None,
        payload: Any = None,
        file_entries: Any = None,
        smtp_send: Any = None,  # noqa: ARG002 — exposed for tests only
        archive_path: Any = None,  # noqa: ARG002 — exposed for tests only
    ) -> dict[str, Any]:
        """Run the full submission pipeline.

        ``developer_id`` defaults to the active one if omitted,
        matching the UI's "I'm logged in as someone" flow.

        Returns the new submission record on success, or a structured
        error dict (see :func:`serialize_error`).
        """
        # Fall back to the active developer for convenience.
        if developer_id is None or developer_id == "":
            developer_id = self._active_developer
        if not isinstance(developer_id, str) or not developer_id:
            raise DevKitError(
                400,
                "`developer_id` is required (or call api.login first)",
                code="missing_developer_id",
            )
        if not isinstance(target_kind, str) or not target_kind:
            raise DevKitError(
                400, "`target_kind` is required", code="missing_target_kind"
            )
        if not isinstance(target_id, str) or not target_id:
            raise DevKitError(
                400, "`target_id` is required", code="missing_target_id"
            )
        if file_entries is not None and not isinstance(file_entries, list):
            raise DevKitError(
                400,
                "`file_entries` must be a list when provided",
                code="bad_file_entries",
            )
        # Pre-flight rate-limit; raises RateLimitedError → serialize_error.
        check_rate_limit(developer_id)

        # pywebview serialises JS null/objects into Python None/dict,
        # so pass-through works for ``payload``.  We also expose the
        # injectable ``smtp_send`` / ``archive_path`` overrides for
        # tests; production callers leave them None.
        return submit(
            developer_id=developer_id,
            target_kind=target_kind,
            target_id=target_id,
            payload=payload if isinstance(payload, Mapping) else None,
            file_entries=list(file_entries) if file_entries else None,
            smtp_send=smtp_send if callable(smtp_send) else None,
            archive_path=archive_path if isinstance(archive_path, str) else None,
        )

    @_serialize_call
    def delete_local(self, submission_id: Any) -> dict[str, Any]:
        """Remove the local archive for ``submission_id`` (best-effort)."""
        if not isinstance(submission_id, str) or not submission_id:
            raise DevKitError(
                400,
                "`submission_id` must be a non-empty string",
                code="missing_submission_id",
            )
        ok = delete_local_archive(submission_id)
        return {"deleted": bool(ok), "submission_id": submission_id}

    # --- helpers not exposed via js_api --------------------------------

    def archive_name(self, developer_id: str, *, now=None) -> str:
        """Expose :func:`xijian_api.devkit.archive_name` (for tests)."""
        return archive_name(developer_id, now=now)


__all__ = [
    "DevKitApi",
    "serialize_error",
]
