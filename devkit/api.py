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

* Errors that surface from the orchestrator (:mod:`devkit`)
  all derive from :class:`devkit.DevKitError` (which
  derives from :class:`devkit._vendor.ApiError`).  They carry the
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
import os
import threading
from collections.abc import Mapping
from typing import Any

from devkit import (
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
    clear_submissions,
    cooldown_remaining,
    delete_local_archive,
    delete_package,
    delete_submission,
    get_submission,
    last_submit_for,
    list_submissions,
    state,
    submit,
)
from devkit import config
from devkit import version as _version
from devkit import updater as _updater
from devkit.character_editor import (
    list_characters as _ce_list,
    get_character as _ce_get,
    save_character as _ce_save,
    delete_character as _ce_delete,
    export_character_for_submit as _ce_export,
    import_persona as _ce_import_persona,
    check_initial_memory_minimum as _ce_check_min,
    get_persona_templates as _ce_persona_templates,
    get_character_config_schema as _ce_config_schema,
    validate_character_config as _ce_validate_config,
    auto_fill_character_config as _ce_autofill,
)
from devkit.memory_editor import (
    list_entries as _me_list,
    list_characters_with_memories as _me_chars,
    get_entry as _me_get,
    save_entry as _me_save,
    delete_entry as _me_delete,
    export_entries_for_submit as _me_export,
)
from devkit.world_editor import (
    list_worlds as _we_list,
    get_world as _we_get,
    save_world as _we_save,
    delete_world as _we_delete,
    export_world_for_submit as _we_export,
    get_world_config as _we_get_config,
    save_world_config as _we_save_config,
    validate_world_config as _we_validate_config,
    list_world_events as _we_list_events,
    save_world_event as _we_save_event,
    delete_world_event as _we_delete_event,
    validate_event_trigger as _we_validate_trigger,
    lint_world_doc as _we_lint_doc,
    get_world_doc_templates as _we_templates,
)
from devkit.model_viewer import (
    list_models as _mv_list,
    register_model as _mv_register,
    unregister_model as _mv_unregister,
    get_model_info as _mv_info,
    read_model_bytes as _mv_read,
    export_model_for_submit as _mv_export,
    generate_model_from_text as _mv_generate,
    validate_model_format as _mv_validate,
)
from devkit.voice_cloner import (
    list_voices as _vc_list,
    list_characters_with_voices as _vc_chars,
    get_voice as _vc_get,
    save_voice as _vc_save,
    delete_voice as _vc_delete,
    list_engines as _vc_engines,
    export_voice_for_submit as _vc_export,
    generate_voice_from_text as _vc_generate_text,
    clone_voice_from_file as _vc_clone_file,
    generate_singing as _vc_sing,
)
from devkit.plot_editor import (
    list_plots as _pe_list,
    get_plot as _pe_get,
    save_plot as _pe_save,
    delete_plot as _pe_delete,
    get_plot_nodes as _pe_nodes,
    save_plot_node as _pe_node_save,
    delete_plot_node as _pe_node_delete,
    get_plot_edges as _pe_edges,
    save_plot_edge as _pe_edge_save,
    delete_plot_edge as _pe_edge_delete,
    validate_plot_bindings as _pe_validate_bindings,
    export_plot_for_submit as _pe_export,
)
from devkit.dialog_editor import (
    list_dialog_characters as _de_chars,
    list_dialogs as _de_list,
    get_dialog as _de_get,
    save_dialog as _de_save,
    delete_dialog as _de_delete,
    check_dialog_minimum as _de_minimum,
    export_dialogs_for_submit as _de_export,
)
from devkit.motion_editor import (
    list_motion_characters as _moe_chars,
    list_motions as _moe_list,
    get_motion as _moe_get,
    save_motion as _moe_save,
    delete_motion as _moe_delete,
    import_motion_file as _moe_import,
    export_motions_for_submit as _moe_export,
)
from devkit.ai_assistant import (
    log_assist_event as _aa_log,
    list_assist_log as _aa_list,
    get_assist_stats as _aa_stats,
    calculate_ai_ratio as _aa_ratio,
    check_ai_threshold as _aa_threshold,
    auto_suggest as _aa_suggest,
    suggest_with_questions as _aa_suggest_questions,
)


_LOGGER = logging.getLogger("devkit.api")
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
    :mod:`devkit` module-level constants; tests can
    monkeypatch them before constructing the API.
    """

    def __init__(self) -> None:
        # Session state.  Restored from disk so a restart never silently
        # drops the login and resets the per-developer submit cooldown.
        self._lock = threading.Lock()
        self._active_developer: str | None = state.session.get("developer_id")

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
    def get_smtp_config(self) -> dict[str, Any]:
        """Return the developer's SMTP configuration from the config file."""
        work_dir = self._work_dir()
        if not work_dir:
            return config.DEFAULT_CONFIG["smtp"]
        return config.get_smtp_config(work_dir)

    @_serialize_call
    def get_status(self) -> dict[str, Any]:
        """Spec ``DevKitApi.get_status`` — alias of :meth:`whoami`.

        Returns the static configuration the UI renders in the header
        (recipient, SMTP host, cooldown, size limit, target kinds).
        """
        return self.whoami()

    @_serialize_call
    def save_smtp_config(self, smtp_config: Any) -> dict[str, Any]:
        """Save SMTP configuration to the config file."""
        work_dir = self._work_dir()
        if not work_dir:
            raise DevKitError(400, "work directory not set", code="missing_work_dir")
        if not isinstance(smtp_config, dict):
            raise DevKitError(400, "smtp_config must be a dict", code="invalid_smtp_config")
        # Validate required fields
        required = ["host", "port", "user", "password", "from_addr"]
        for field in required:
            if not smtp_config.get(field):
                raise DevKitError(400, f"missing required field: {field}", code="missing_field")
        # Load existing config and update SMTP section
        existing = config.load_config(work_dir)
        existing["smtp"] = smtp_config
        config.save_config(work_dir, existing)
        return {"ok": True}

    @_serialize_call
    def get_submission_config(self) -> dict[str, Any]:
        """Return the full submission config (recipient, rate limit, size limit)."""
        work_dir = self._work_dir()
        if not work_dir:
            return {
                "recipient": config.DEFAULT_CONFIG["recipient"],
                "rate_limit_seconds": config.DEFAULT_CONFIG["rate_limit_seconds"],
                "max_attachment_bytes": config.DEFAULT_CONFIG["max_attachment_bytes"],
            }
        return {
            "recipient": config.get_recipient(work_dir),
            "rate_limit_seconds": config.get_rate_limit(work_dir),
            "max_attachment_bytes": config.get_max_attachment_bytes(work_dir),
        }

    @_serialize_call
    def save_submission_config(self, submission_config: Any) -> dict[str, Any]:
        """Save submission config (recipient, rate limit, size limit)."""
        work_dir = self._work_dir()
        if not work_dir:
            raise DevKitError(400, "work directory not set", code="missing_work_dir")
        if not isinstance(submission_config, dict):
            raise DevKitError(400, "submission_config must be a dict", code="invalid_config")
        existing = config.load_config(work_dir)
        existing.update(submission_config)
        config.save_config(work_dir, existing)
        return {"ok": True}

    # --- auto-update (C6) ----------------------------------------------

    @_serialize_call
    def get_update_settings(self) -> dict[str, Any]:
        """Return version + update-source info for the Settings UI."""
        src = _version.get_update_source()
        work_dir = self._work_dir()
        auto = (
            config.get_auto_check_update(work_dir)
            if work_dir else config.DEFAULT_CONFIG["auto_check_update"]
        )
        return {
            "current_version": _version.get_app_version(),
            "auto_check": bool(auto),
            "configured": bool(src["api_url"]),
            "github_owner": src["owner"],
            "github_repo": src["repo"],
        }

    @_serialize_call
    def set_auto_check_update(self, enabled: Any) -> dict[str, Any]:
        """Persist the launch-time auto-check preference."""
        work_dir = self._work_dir()
        if not work_dir:
            raise DevKitError(400, "work directory not set", code="missing_work_dir")
        config.set_auto_check_update(work_dir, bool(enabled))
        return {"auto_check": bool(enabled)}

    @_serialize_call
    def check_for_update(self) -> dict[str, Any]:
        """Check GitHub Releases for a newer version (network)."""
        return _updater.check_for_update()

    @_serialize_call
    def download_update(self, asset_url: Any, asset_name: Any) -> dict[str, Any]:
        """Download a release asset into the internal Updates folder (network)."""
        if not isinstance(asset_url, str) or not asset_url:
            raise DevKitError(400, "asset_url is required", code="missing_asset_url")
        if not isinstance(asset_name, str) or not asset_name:
            raise DevKitError(400, "asset_name is required", code="missing_asset_name")
        result = _updater.download_update(asset_url, asset_name)
        if "error" in result:
            raise DevKitError(502, result["error"], code="download_failed")
        return result

    @_serialize_call
    def apply_update(self, file_path: Any) -> dict[str, Any]:
        """Install a downloaded update and schedule a relaunch."""
        if not isinstance(file_path, str) or not file_path:
            raise DevKitError(400, "file_path is required", code="missing_file_path")
        result = _updater.apply_update(file_path)
        if "error" in result:
            raise DevKitError(500, result["error"], code="apply_failed")
        return result

    @_serialize_call
    def open_external(self, url: Any) -> dict[str, Any]:
        """Open a URL in the user's default browser (update-page fallback)."""
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise DevKitError(400, "invalid url", code="invalid_url")
        import webbrowser
        webbrowser.open(url)
        return {"ok": True}

    @_serialize_call
    def quit_app(self) -> dict[str, Any]:
        """Close the DevKit window (used to hand off to the update helper)."""
        try:
            import webview  # type: ignore[import-not-found]
            for win in list(getattr(webview, "windows", []) or []):
                win.destroy()
        except Exception:  # pragma: no cover — depends on GUI runtime
            _LOGGER.warning("quit_app: failed to destroy window", exc_info=True)
        return {"ok": True}

    @_serialize_call
    def open_file_dialog(
        self,
        dialog_type: Any = "open",
        file_types: Any = None,
        allow_multiple: Any = False,
    ) -> dict[str, Any]:
        """Open a native file dialog (single source of truth).

        pywebview only exposes the file dialog from the Python side
        (``Window.create_file_dialog``); the JS-side
        ``window.pywebview.create_file_dialog`` is frequently reported
        as "not ready" in some runtimes.  This method drives the dialog
        on the live window and returns the chosen path(s).

        Returns ``{"paths": [str, ...]}`` (empty list when cancelled).
        """
        try:
            import webview  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            raise DevKitError(500, f"webview 不可用：{exc}", code="webview_unavailable")

        windows = list(getattr(webview, "windows", []) or [])
        if not windows:
            raise DevKitError(500, "窗口尚未就绪，无法打开文件对话框", code="window_not_ready")
        win = windows[-1]

        kind_str = str(dialog_type or "open").lower()
        kind = getattr(webview, "FileDialog", None)
        if kind is None:  # pragma: no cover — ancient pywebview
            kind = webview.OPEN_DIALOG
        dialog_kind = {
            "open": kind.OPEN,
            "folder": kind.FOLDER,
            "save": kind.SAVE,
        }.get(kind_str, kind.OPEN)

        file_types_arg = ()
        if file_types and kind_str == "open":
            exts = [str(e) for e in file_types if e] if isinstance(file_types, list) else [str(file_types)]
            # pywebview expects a tuple of strings in format:
            # "Description (*.ext1;*.ext2;...)"
            if exts:
                # Ensure each extension starts with dot
                normalized = [e if e.startswith(".") else f".{e}" for e in exts]
                pattern = ";".join(f"*{e}" for e in normalized)
                file_types_arg = (f"允许的格式 ({pattern})",)

        try:
            result = win.create_file_dialog(
                dialog_kind,
                file_types=file_types_arg,
                allow_multiple=bool(allow_multiple),
            )
        except Exception as exc:  # pragma: no cover — depends on GUI runtime
            raise DevKitError(500, f"打开文件对话框失败：{exc}", code="dialog_failed")

        # pywebview returns a str (single) or list[str]; normalise to list.
        if result is None:
            paths = []
        elif isinstance(result, str):
            paths = [result]
        elif isinstance(result, (list, tuple)):
            paths = [str(p) for p in result]
        else:
            paths = []
        return {"paths": paths}

    def _work_dir(self) -> str | None:
        """Return the current work directory (if set)."""
        # The work directory is set via api.set_work_dir() in main.py
        return getattr(self, "_work_dir_path", None)

    def set_work_dir(self, path: str) -> None:
        """Set the work directory for config persistence."""
        self._work_dir_path = path

    def _save_session(self) -> None:
        """Persist login/session state to disk (best-effort)."""
        try:
            state.save(self._work_dir())
        except Exception:  # pragma: no cover — persistence is best-effort
            _LOGGER.warning("failed to persist DevKit session state", exc_info=True)

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
            state.session["developer_id"] = cleaned
            self._save_session()
        return {"developer_id": cleaned}

    @_serialize_call
    def logout(self) -> dict[str, Any]:
        """Clear the active developer."""
        with self._lock:
            previous = self._active_developer
            self._active_developer = None
            state.session["developer_id"] = None
            self._save_session()
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

    @_serialize_call
    def delete_submission(self, submission_id: Any) -> dict[str, Any]:
        """Delete a single submission record and its local archive."""
        if not isinstance(submission_id, str) or not submission_id:
            raise DevKitError(
                400,
                "`submission_id` must be a non-empty string",
                code="missing_submission_id",
            )
        work_dir = self._work_dir()
        ok = delete_submission(submission_id, work_dir)
        return {"deleted": bool(ok), "submission_id": submission_id}

    @_serialize_call
    def clear_submissions(self) -> dict[str, Any]:
        """Delete ALL submission records and their local archives."""
        work_dir = self._work_dir()
        count = clear_submissions(work_dir)
        return {"deleted": count}

    @_serialize_call
    def delete_package(self, package_id: Any) -> dict[str, Any]:
        """Delete a submittable package by its ``package_id`` (e.g. ``char:abc``)."""
        if not isinstance(package_id, str) or not package_id:
            raise DevKitError(
                400,
                "`package_id` must be a non-empty string",
                code="missing_package_id",
            )
        work_dir = self._work_dir()
        ok = delete_package(package_id, work_dir)
        return {"deleted": bool(ok), "package_id": package_id}

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
        package_ids: Any = None,
        smtp_send: Any = None,  # noqa: ARG002 — exposed for tests only
        archive_path: Any = None,  # noqa: ARG002 — exposed for tests only
    ) -> dict[str, Any]:
        """Run the full submission pipeline.

        ``developer_id`` defaults to the active one if omitted,
        matching the UI's "I'm logged in as someone" flow.

        Accepts either ``file_entries`` (legacy) or ``package_ids``
        (new flow).  When ``package_ids`` is provided, each package
        is resolved to its file entries via the editor export
        functions and aggregated into a single submission.

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

        # --- resolve package_ids → file_entries + target_kind/target_id ---
        if package_ids is not None:
            if not isinstance(package_ids, list):
                raise DevKitError(
                    400,
                    "`package_ids` must be a list of strings",
                    code="bad_package_ids",
                )
            _LOGGER.info("resolving %d package(s) for submission: %s", len(package_ids), package_ids)
            resolved_all = self._resolve_packages(package_ids)
            target_kind = resolved_all["target_kind"]
            target_id = resolved_all["target_id"]
            file_entries = resolved_all["file_entries"]
            payload = payload or resolved_all["payload"]
            _LOGGER.info(
                "resolved packages → kind=%s id=%s files=%d",
                target_kind, target_id, len(file_entries),
            )
        else:
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
        result = submit(
            developer_id=developer_id,
            target_kind=target_kind,
            target_id=target_id,
            payload=payload if isinstance(payload, Mapping) else None,
            file_entries=list(file_entries) if file_entries else None,
            smtp_send=smtp_send if callable(smtp_send) else None,
            archive_path=archive_path if isinstance(archive_path, str) else None,
            work_dir=self._work_dir(),
        )
        state.save(self._work_dir())
        return result

    def _resolve_packages(
        self, package_ids: list[str]
    ) -> dict[str, Any]:
        """Resolve package IDs to file entries and derive submission metadata."""
        all_files: list[dict[str, Any]] = []
        notes_parts: list[str] = []
        first_kind: str | None = None
        first_id: str | None = None
        payload_files: list[str] = []

        for pkg_id in package_ids:
            if not isinstance(pkg_id, str) or ":" not in pkg_id:
                raise DevKitError(
                    400, f"无效的包 ID: {pkg_id}", code="bad_package_id"
                )
            ptype, pid = pkg_id.split(":", 1)
            if not pid:
                raise DevKitError(
                    400, f"无效的包 ID: {pkg_id}", code="bad_package_id"
                )

            if ptype == "char":
                export = _ce_export(self._work_dir(), pid)
            elif ptype == "memory":
                export = _me_export(self._work_dir(), pid)
            elif ptype == "world":
                export = _we_export(self._work_dir(), pid)
            elif ptype == "plot":
                export = _pe_export(self._work_dir(), pid)
            elif ptype == "voice":
                export = _vc_export(self._work_dir(), pid)
            elif ptype == "model":
                export = _mv_export(self._work_dir(), pid)
            elif ptype == "dialog":
                export = _de_export(self._work_dir(), pid)
            elif ptype == "motion":
                export = _moe_export(self._work_dir(), pid)
            else:
                raise DevKitError(
                    400, f"未知的包类型: {ptype}", code="unknown_package_type"
                )

            if first_kind is None:
                first_kind = export["target_kind"]
                first_id = pid
            all_files.extend(export.get("files", []))
            notes_parts.append(export.get("payload", {}).get("notes", ""))
            payload_files.extend(export.get("payload", {}).get("files", []))

        return {
            "target_kind": first_kind or "character",
            "target_id": first_id or "",
            "file_entries": all_files,
            "payload": {
                "notes": " | ".join(notes_parts) if notes_parts else "",
                "files": payload_files,
            },
        }

    @_serialize_call
    def list_submit_packages(self) -> list[dict[str, Any]]:
        """Return all exportable items (characters, memory packs, worlds)
        as selectable packages for submission."""
        work_dir = self._work_dir()
        packages: list[dict[str, Any]] = []

        # Characters
        for char in _ce_list(work_dir):
            char_id = char.get("id", "")
            name = char.get("display_name") or char.get("name", "?")
            packages.append({
                "package_id": f"char:{char_id}",
                "package_type": "character",
                "target_kind": "character",
                "target_id": char_id,
                "name": name,
                "description": f"角色: {name}",
            })

        # Memory packs (one per character that has entries)
        for char_id in _me_chars(work_dir):
            char = _ce_get(work_dir, char_id) or {}
            name = char.get("display_name") or char.get("name", char_id)
            packages.append({
                "package_id": f"memory:{char_id}",
                "package_type": "memory_pack",
                "target_kind": "character",
                "target_id": char_id,
                "name": f"{name} 的记忆包",
                "description": f"记忆条目: {char_id}",
            })

        # Worlds
        for world in _we_list(work_dir):
            world_id = world.get("id", "")
            name = world.get("name", "?")
            packages.append({
                "package_id": f"world:{world_id}",
                "package_type": "world",
                "target_kind": "world",
                "target_id": world_id,
                "name": name,
                "description": f"世界观: {name}",
            })

        # Plots
        for plot in _pe_list(work_dir):
            plot_id = plot.get("id", "")
            name = plot.get("name", "?")
            packages.append({
                "package_id": f"plot:{plot_id}",
                "package_type": "plot",
                "target_kind": "plot",
                "target_id": plot_id,
                "name": name,
                "description": f"剧情: {name}",
            })

        # Models (registered 3D)
        for model in _mv_list(work_dir):
            model_id = model.get("id", "")
            name = model.get("name", "?")
            packages.append({
                "package_id": f"model:{model_id}",
                "package_type": "model",
                "target_kind": "character",
                "target_id": model_id,
                "name": f"3D模型: {name}",
                "description": f"3D模型: {name}",
            })

        # Voices (per character)
        for char_id in _vc_chars(work_dir):
            voices = _vc_list(work_dir, char_id)
            for voice in voices:
                vid = voice.get("id", "")
                vname = voice.get("name", "?")
                char = _ce_get(work_dir, char_id) or {}
                cname = char.get("display_name") or char.get("name", char_id)
                packages.append({
                    "package_id": f"voice:{vid}",
                    "package_type": "voice",
                    "target_kind": "character",
                    "target_id": char_id,
                    "name": f"{cname} 的声音: {vname}",
                    "description": f"声音样本: {vname} ({voice.get('engine', '')})",
                })

        return packages

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

    # --- work directory ------------------------------------------------

    def _work_dir(self) -> str:
        import os
        return os.environ.get(
            "XIJIAN_DEV_WORK_DIR",
            os.path.join(os.path.expanduser("~"), "Library", "Application Support", "XiJian", "DevKit"),
        )

    # --- character editor ----------------------------------------------

    @_serialize_call
    def list_characters(self) -> list[dict[str, Any]]:
        return _ce_list(self._work_dir())

    @_serialize_call
    def get_character(self, char_id: Any) -> dict[str, Any] | None:
        if not isinstance(char_id, str) or not char_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _ce_get(self._work_dir(), char_id)

    @_serialize_call
    def save_character(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        return _ce_save(self._work_dir(), data)

    @_serialize_call
    def delete_character(self, char_id: Any) -> dict[str, Any]:
        if not isinstance(char_id, str) or not char_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        ok = _ce_delete(self._work_dir(), char_id)
        return {"deleted": ok}

    @_serialize_call
    def export_character(self, char_id: Any) -> dict[str, Any]:
        if not isinstance(char_id, str) or not char_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _ce_export(self._work_dir(), char_id)

    @_serialize_call
    def import_persona(self, char_id: Any, file_path: Any) -> dict[str, Any]:
        if not isinstance(char_id, str) or not char_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        if not isinstance(file_path, str) or not file_path:
            raise DevKitError(400, "文件路径不能为空", code="missing_file_path")
        result = _ce_import_persona(self._work_dir(), char_id, file_path)
        return {"imported": True, "message": result}

    @_serialize_call
    def check_initial_memory(self, char_id: Any, min_count: Any = None) -> dict[str, Any]:
        """C2.5 — report whether a character meets the initial-memory minimum."""
        if not isinstance(char_id, str) or not char_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        n = int(min_count) if min_count is not None else None
        if n is None:
            return _ce_check_min(self._work_dir(), char_id)
        return _ce_check_min(self._work_dir(), char_id, min_count=n)

    @_serialize_call
    def get_persona_templates(self) -> dict[str, str]:
        """C2.4 — return built-in persona-doc markdown templates."""
        return _ce_persona_templates()

    @_serialize_call
    def get_character_config_schema(self) -> dict[str, Any]:
        """C2.3 — return the character config JSON schema definition."""
        return _ce_config_schema()

    @_serialize_call
    def validate_character_config(self, config: Any) -> dict[str, Any]:
        """C2.3 — schema-validate a character config dict."""
        if not isinstance(config, dict):
            raise DevKitError(400, "配置必须是对象", code="bad_data")
        ok, errors = _ce_validate_config(config)
        return {"ok": ok, "errors": errors}

    @_serialize_call
    def auto_fill_config(self, char_id: Any) -> dict[str, Any]:
        """C2.3 + C4 — auto-fill a character's config from schema defaults.

        Marked ``source='ai_suggested'`` so the 30% audit accounts for it;
        the developer must review every field before enabling the character.
        """
        if not isinstance(char_id, str) or not char_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _ce_autofill(self._work_dir(), char_id, apply=True)

    # --- memory editor -------------------------------------------------

    @_serialize_call
    def list_memory_entries(self, character_id: Any) -> list[dict[str, Any]]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _me_list(self._work_dir(), character_id)

    @_serialize_call
    def list_memory_characters(self) -> list[str]:
        return _me_chars(self._work_dir())

    @_serialize_call
    def get_memory_entry(self, entry_id: Any) -> dict[str, Any] | None:
        if not isinstance(entry_id, str) or not entry_id:
            raise DevKitError(400, "条目 ID 不能为空", code="missing_entry_id")
        return _me_get(self._work_dir(), entry_id)

    @_serialize_call
    def save_memory_entry(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        return _me_save(self._work_dir(), data)

    @_serialize_call
    def delete_memory_entry(self, entry_id: Any) -> dict[str, Any]:
        if not isinstance(entry_id, str) or not entry_id:
            raise DevKitError(400, "条目 ID 不能为空", code="missing_entry_id")
        ok = _me_delete(self._work_dir(), entry_id)
        return {"deleted": ok}

    @_serialize_call
    def export_memory_entries(self, character_id: Any) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _me_export(self._work_dir(), character_id)

    # --- world editor --------------------------------------------------

    @_serialize_call
    def list_worlds(self) -> list[dict[str, Any]]:
        return _we_list(self._work_dir())

    @_serialize_call
    def get_world(self, world_id: Any) -> dict[str, Any] | None:
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        return _we_get(self._work_dir(), world_id)

    @_serialize_call
    def save_world(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        return _we_save(self._work_dir(), data)

    @_serialize_call
    def delete_world(self, world_id: Any) -> dict[str, Any]:
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        ok = _we_delete(self._work_dir(), world_id)
        return {"deleted": ok}

    @_serialize_call
    def export_world(self, world_id: Any) -> dict[str, Any]:
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        return _we_export(self._work_dir(), world_id)

    # --- world structured config (C1.3) --------------------------------

    @_serialize_call
    def get_world_config(self, world_id: Any) -> dict[str, Any]:
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        return _we_get_config(self._work_dir(), world_id)

    @_serialize_call
    def save_world_config(self, world_id: Any, config: Any) -> dict[str, Any]:
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        if not isinstance(config, dict):
            raise DevKitError(400, "配置必须是对象", code="bad_data")
        return _we_save_config(self._work_dir(), world_id, config)

    @_serialize_call
    def validate_world_config(self, config: Any) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise DevKitError(400, "配置必须是对象", code="bad_data")
        ok, errors = _we_validate_config(config)
        return {"ok": ok, "errors": errors}

    # --- world custom events (C1.1) -------------------------------------

    @_serialize_call
    def list_world_events(self, world_id: Any) -> list[dict[str, Any]]:
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        return _we_list_events(self._work_dir(), world_id)

    @_serialize_call
    def save_world_event(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        world_id = data.get("world_id")
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        return _we_save_event(self._work_dir(), world_id, data)

    @_serialize_call
    def delete_world_event(self, world_id: Any, event_id: Any) -> dict[str, Any]:
        if not isinstance(world_id, str) or not world_id:
            raise DevKitError(400, "世界观 ID 不能为空", code="missing_world_id")
        if not isinstance(event_id, str) or not event_id:
            raise DevKitError(400, "事件 ID 不能为空", code="missing_event_id")
        ok = _we_delete_event(self._work_dir(), world_id, event_id)
        return {"deleted": ok}

    @_serialize_call
    def validate_event_trigger(self, trigger: Any) -> dict[str, Any]:
        if not isinstance(trigger, dict):
            raise DevKitError(400, "触发器必须是对象", code="bad_data")
        ok, errors = _we_validate_trigger(trigger)
        return {"ok": ok, "errors": errors}

    @_serialize_call
    def lint_world_doc(self, doc: Any) -> dict[str, Any]:
        if not isinstance(doc, str):
            raise DevKitError(400, "文档必须是字符串", code="bad_data")
        return _we_lint_doc(doc)

    @_serialize_call
    def get_world_doc_templates(self) -> dict[str, str]:
        """C1.2 — return built-in world-doc markdown templates."""
        return _we_templates()

    # --- 3D model viewer -----------------------------------------------

    @_serialize_call
    def list_models(self) -> list[dict[str, Any]]:
        return _mv_list(self._work_dir())

    @_serialize_call
    def register_model(self, path: Any) -> dict[str, Any]:
        if not isinstance(path, str) or not path:
            raise DevKitError(400, "文件路径不能为空", code="missing_path")
        return _mv_register(self._work_dir(), path)

    @_serialize_call
    def unregister_model(self, model_id: Any) -> dict[str, Any]:
        if not isinstance(model_id, str) or not model_id:
            raise DevKitError(400, "模型 ID 不能为空", code="missing_model_id")
        ok = _mv_unregister(self._work_dir(), model_id)
        return {"deleted": ok}

    @_serialize_call
    def get_model_info(self, model_id: Any) -> dict[str, Any] | None:
        if not isinstance(model_id, str) or not model_id:
            raise DevKitError(400, "模型 ID 不能为空", code="missing_model_id")
        return _mv_info(self._work_dir(), model_id)

    @_serialize_call
    def read_model_bytes(self, model_id: Any) -> dict[str, Any] | None:
        """Return the model's file bytes (base64) + MIME for the 3D viewer.

        pywebview's WKWebView will not ``fetch()`` a ``file://`` URL,
        so the JS previewer asks Python to hand it the bytes instead
        of trying to load the file path directly.  Returns ``null``
        if the model id is unknown or the file vanished.
        """
        if not isinstance(model_id, str) or not model_id:
            raise DevKitError(400, "模型 ID 不能为空", code="missing_model_id")
        return _mv_read(self._work_dir(), model_id)

    @_serialize_call
    def validate_model(self, model_id: Any) -> dict[str, Any]:
        """C2.8 AC-4 — validate a registered model against the VRM 1.0 spec."""
        if not isinstance(model_id, str) or not model_id:
            raise DevKitError(400, "模型 ID 不能为空", code="missing_model_id")
        return _mv_validate(self._work_dir(), model_id)

    # --- voice clone ---------------------------------------------------

    @_serialize_call
    def list_voice_engines(self) -> list[str]:
        return list(_vc_engines())

    @_serialize_call
    def list_voices(self, character_id: Any) -> list[dict[str, Any]]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _vc_list(self._work_dir(), character_id)

    @_serialize_call
    def list_voice_characters(self) -> list[str]:
        return _vc_chars(self._work_dir())

    @_serialize_call
    def get_voice(self, voice_id: Any) -> dict[str, Any] | None:
        if not isinstance(voice_id, str) or not voice_id:
            raise DevKitError(400, "声音 ID 不能为空", code="missing_voice_id")
        return _vc_get(self._work_dir(), voice_id)

    @_serialize_call
    def save_voice(
        self,
        character_id: Any = None,
        name: Any = None,
        sample_path: Any = None,
        engine: Any = None,
        audio_data_b64: Any = None,
    ) -> dict[str, Any]:
        """Create or update a voice sample.

        Two ways to supply the audio source:

        * ``sample_path`` — path to an existing audio file on disk
          (used by the "选择文件" button, which resolves to a real path
          via pywebview's file dialog).
        * ``audio_data_b64`` — base64-encoded raw bytes from an in-page
          recording (``MediaRecorder`` blob → ``FileReader.readAsDataURL``
          → strip the prefix).  Required for the "录制样本" button since
          the browser has no file-system path to hand to us.

        Exactly one of the two must be provided.  ``engine`` defaults to
        ``"melo-tts"`` if omitted.
        """
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        if not isinstance(name, str) or not name:
            raise DevKitError(400, "声音名称不能为空", code="missing_name")

        # Validate the audio source — exactly one of the two.
        has_path = isinstance(sample_path, str) and bool(sample_path)
        has_data = isinstance(audio_data_b64, str) and bool(audio_data_b64)
        if has_path and has_data:
            raise DevKitError(
                400,
                "sample_path 与 audio_data_b64 二选一，不要同时传",
                code="ambiguous_audio_source",
            )
        if not has_path and not has_data:
            raise DevKitError(
                400,
                "需要 sample_path（文件路径）或 audio_data_b64（录制数据）之一",
                code="missing_audio_source",
            )

        # Decode the recording.  The UI may pass either a raw base64
        # string or a full data URL (``data:audio/webm;base64,XXXXX``)
        # because FileReader.readAsDataURL includes the prefix.
        audio_bytes: bytes | None = None
        if has_data:
            assert isinstance(audio_data_b64, str)
            raw = audio_data_b64
            # Strip the data-URL prefix if present.
            if raw.startswith("data:") and ";base64," in raw:
                raw = raw.split(",", 1)[1]
            import base64
            try:
                audio_bytes = base64.b64decode(raw, validate=False)
            except Exception as exc:
                raise DevKitError(
                    400,
                    f"audio_data_b64 不是有效的 base64：{exc}",
                    code="bad_audio_base64",
                ) from exc
            if not audio_bytes:
                raise DevKitError(
                    400, "录制数据为空", code="empty_audio_data",
                )

        return _vc_save(
            self._work_dir(),
            character_id,
            name,
            sample_path=sample_path if has_path else None,
            audio_data=audio_bytes,
            engine=engine if isinstance(engine, str) else "melo-tts",
        )

    @_serialize_call
    def delete_voice(self, voice_id: Any) -> dict[str, Any]:
        if not isinstance(voice_id, str) or not voice_id:
            raise DevKitError(400, "声音 ID 不能为空", code="missing_voice_id")
        ok = _vc_delete(self._work_dir(), voice_id)
        return {"deleted": ok}

    # --- voice generation (TTS + clone) ---------------------------------

    @_serialize_call
    def generate_voice_from_text(
        self,
        character_id: Any = None,
        name: Any = None,
        text: Any = None,
        engine: Any = None,
        params: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        if not isinstance(name, str) or not name:
            raise DevKitError(400, "声音名称不能为空", code="missing_name")
        if not isinstance(text, str) or not text.strip():
            raise DevKitError(400, "文本内容不能为空", code="empty_text")
        engine_str = engine if isinstance(engine, str) else "melo-tts"
        params_dict = params if isinstance(params, dict) else None
        return _vc_generate_text(
            self._work_dir(), character_id, name, text,
            engine=engine_str, params=params_dict,
        )

    @_serialize_call
    def clone_voice_from_file(
        self,
        character_id: Any = None,
        name: Any = None,
        source_path: Any = None,
        engine: Any = None,
        params: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        if not isinstance(name, str) or not name:
            raise DevKitError(400, "声音名称不能为空", code="missing_name")
        if not isinstance(source_path, str) or not source_path:
            raise DevKitError(400, "音频文件路径不能为空", code="missing_source_path")
        engine_str = engine if isinstance(engine, str) else "cosyvoice"
        params_dict = params if isinstance(params, dict) else None
        return _vc_clone_file(
            self._work_dir(), character_id, name, source_path,
            engine=engine_str, params=params_dict,
        )

    @_serialize_call
    def generate_singing(
        self,
        character_id: Any = None,
        name: Any = None,
        text: Any = None,
        engine: Any = None,
        params: Any = None,
    ) -> dict[str, Any]:
        """C2.1 歌声合成（DiffSinger 占位）。离线环境用最佳 TTS 后端生成歌唱占位音频。"""
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        if not isinstance(name, str) or not name:
            raise DevKitError(400, "声音名称不能为空", code="missing_name")
        if not isinstance(text, str) or not text.strip():
            raise DevKitError(400, "歌词文本不能为空", code="empty_text")
        engine_str = engine if isinstance(engine, str) else "fallback"
        params_dict = params if isinstance(params, dict) else None
        return _vc_sing(
            self._work_dir(), character_id, name, text,
            engine=engine_str, params=params_dict,
        )

    @_serialize_call
    def export_voice(self, voice_id: Any) -> dict[str, Any]:
        if not isinstance(voice_id, str) or not voice_id:
            raise DevKitError(400, "声音 ID 不能为空", code="missing_voice_id")
        return _vc_export(self._work_dir(), voice_id)

    # --- model export + generation --------------------------------------

    @_serialize_call
    def export_model(self, model_id: Any) -> dict[str, Any]:
        if not isinstance(model_id, str) or not model_id:
            raise DevKitError(400, "模型 ID 不能为空", code="missing_model_id")
        return _mv_export(self._work_dir(), model_id)

    @_serialize_call
    def generate_model(
        self,
        description: Any = None,
        name: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(description, str) or not description.strip():
            raise DevKitError(400, "描述文本不能为空", code="empty_description")
        name_str = name if isinstance(name, str) else ""
        return _mv_generate(self._work_dir(), description, name=name_str)

    # --- plot editor ----------------------------------------------------

    @_serialize_call
    def list_plots(self) -> list[dict[str, Any]]:
        return _pe_list(self._work_dir())

    @_serialize_call
    def get_plot(self, plot_id: Any) -> dict[str, Any] | None:
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "剧情 ID 不能为空", code="missing_plot_id")
        return _pe_get(self._work_dir(), plot_id)

    @_serialize_call
    def save_plot(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        return _pe_save(self._work_dir(), data)

    @_serialize_call
    def delete_plot(self, plot_id: Any) -> dict[str, Any]:
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "剧情 ID 不能为空", code="missing_plot_id")
        ok = _pe_delete(self._work_dir(), plot_id)
        return {"deleted": ok}

    @_serialize_call
    def export_plot(self, plot_id: Any) -> dict[str, Any]:
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "剧情 ID 不能为空", code="missing_plot_id")
        return _pe_export(self._work_dir(), plot_id)

    @_serialize_call
    def get_plot_nodes(self, plot_id: Any) -> list[dict[str, Any]]:
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "剧情 ID 不能为空", code="missing_plot_id")
        return _pe_nodes(self._work_dir(), plot_id)

    @_serialize_call
    def save_plot_node(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        plot_id = data.get("plot_id")
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "节点必须关联剧情 ID", code="missing_plot_id")
        return _pe_node_save(self._work_dir(), plot_id, data)

    @_serialize_call
    def delete_plot_node(self, node_id: Any) -> dict[str, Any]:
        if not isinstance(node_id, str) or not node_id:
            raise DevKitError(400, "节点 ID 不能为空", code="missing_node_id")
        ok = _pe_node_delete(self._work_dir(), node_id)
        return {"deleted": ok}

    @_serialize_call
    def get_plot_edges(self, plot_id: Any) -> list[dict[str, Any]]:
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "剧情 ID 不能为空", code="missing_plot_id")
        return _pe_edges(self._work_dir(), plot_id)

    @_serialize_call
    def save_plot_edge(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        plot_id = data.get("plot_id")
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "连线必须关联剧情 ID", code="missing_plot_id")
        return _pe_edge_save(self._work_dir(), plot_id, data)

    @_serialize_call
    def delete_plot_edge(self, edge_id: Any) -> dict[str, Any]:
        if not isinstance(edge_id, str) or not edge_id:
            raise DevKitError(400, "边 ID 不能为空", code="missing_edge_id")
        ok = _pe_edge_delete(self._work_dir(), edge_id)
        return {"deleted": ok}

    @_serialize_call
    def validate_plot_bindings(self, plot_id: Any) -> dict[str, Any]:
        """C3 AC-2 — check node/edge bindings resolve to real characters/worlds."""
        if not isinstance(plot_id, str) or not plot_id:
            raise DevKitError(400, "剧情 ID 不能为空", code="missing_plot_id")
        return _pe_validate_bindings(self._work_dir(), plot_id)

    # --- dialog editor --------------------------------------------------

    @_serialize_call
    def list_dialog_characters(self) -> list[str]:
        return _de_chars(self._work_dir())

    @_serialize_call
    def list_dialogs(self, character_id: Any) -> list[dict[str, Any]]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _de_list(self._work_dir(), character_id)

    @_serialize_call
    def get_dialog(self, dialog_id: Any) -> dict[str, Any] | None:
        if not isinstance(dialog_id, str) or not dialog_id:
            raise DevKitError(400, "对话 ID 不能为空", code="missing_dialog_id")
        return _de_get(self._work_dir(), dialog_id)

    @_serialize_call
    def save_dialog(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        character_id = data.get("character_id")
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _de_save(self._work_dir(), character_id, data)

    @_serialize_call
    def delete_dialog(self, dialog_id: Any) -> dict[str, Any]:
        if not isinstance(dialog_id, str) or not dialog_id:
            raise DevKitError(400, "对话 ID 不能为空", code="missing_dialog_id")
        ok = _de_delete(self._work_dir(), dialog_id)
        return {"deleted": ok}

    @_serialize_call
    def check_dialog_minimum(self, character_id: Any) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        result = _de_minimum(self._work_dir(), character_id)
        return {
            "ok": bool(result.get("ok", False)),
            "message": result.get("message", ""),
            "dialog_count": result.get("current_count", 0),
        }

    @_serialize_call
    def export_dialogs(self, character_id: Any) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _de_export(self._work_dir(), character_id)

    # --- motion editor --------------------------------------------------

    @_serialize_call
    def list_motion_characters(self) -> list[str]:
        return _moe_chars(self._work_dir())

    @_serialize_call
    def list_motions(self, character_id: Any) -> list[dict[str, Any]]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _moe_list(self._work_dir(), character_id)

    @_serialize_call
    def get_motion(self, motion_id: Any) -> dict[str, Any] | None:
        if not isinstance(motion_id, str) or not motion_id:
            raise DevKitError(400, "动作 ID 不能为空", code="missing_motion_id")
        return _moe_get(self._work_dir(), motion_id)

    @_serialize_call
    def save_motion(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        character_id = data.get("character_id")
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _moe_save(self._work_dir(), character_id, data)

    @_serialize_call
    def delete_motion(self, motion_id: Any) -> dict[str, Any]:
        if not isinstance(motion_id, str) or not motion_id:
            raise DevKitError(400, "动作 ID 不能为空", code="missing_motion_id")
        ok = _moe_delete(self._work_dir(), motion_id)
        return {"deleted": ok}

    @_serialize_call
    def import_motion_file(self, character_id: Any, file_path: Any) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        if file_path is None:
            raise DevKitError(400, "文件路径不能为空", code="missing_file_path")
        # pywebview's create_file_dialog returns a list of paths.
        paths = file_path if isinstance(file_path, (list, tuple)) else [file_path]
        paths = [p for p in paths if isinstance(p, str) and p]
        if not paths:
            raise DevKitError(400, "文件路径不能为空", code="missing_file_path")
        src = paths[0]
        name = os.path.splitext(os.path.basename(src))[0]
        return _moe_import(self._work_dir(), character_id, src, name)

    @_serialize_call
    def export_motions(self, character_id: Any) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        return _moe_export(self._work_dir(), character_id)

    # --- AI assistant ---------------------------------------------------

    @_serialize_call
    def log_assist_event(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise DevKitError(400, "数据格式错误", code="bad_data")
        return _aa_log(
            self._work_dir(),
            event_type=str(data.get("event_type", "")),
            target_module=str(data.get("target_module", "")),
            description=str(data.get("description", "")),
            accepted=bool(data.get("accepted", True)),
        )

    @_serialize_call
    def list_assist_log(self, limit: Any = 50, offset: Any = 0) -> list[dict[str, Any]]:
        try:
            n = int(limit) if limit is not None else 50
            o = int(offset) if offset is not None else 0
        except (TypeError, ValueError) as exc:
            raise DevKitError(400, "参数格式错误", code="bad_params") from exc
        return _aa_list(self._work_dir(), limit=n, offset=o)

    @_serialize_call
    def get_assist_stats(self) -> dict[str, Any]:
        return _aa_stats(self._work_dir())

    @_serialize_call
    def get_ai_ratio(self) -> dict[str, Any]:
        ratio = _aa_ratio(self._work_dir())
        return {"ai_ratio": ratio, "ratio": ratio}

    @_serialize_call
    def check_ai_threshold(self, threshold: Any = None) -> dict[str, Any]:
        t = float(threshold) if threshold is not None else None
        return _aa_threshold(self._work_dir(), threshold=t)

    @_serialize_call
    def auto_suggest(self, context: Any) -> dict[str, Any]:
        if not isinstance(context, str) or not context.strip():
            raise DevKitError(400, "上下文不能为空", code="empty_context")
        return _aa_suggest(self._work_dir(), context)

    @_serialize_call
    def ai_suggest_questions(self, context: Any) -> dict[str, Any]:
        """C4 AC-2 — return clarifying questions before producing a design."""
        if not isinstance(context, str) or not context.strip():
            raise DevKitError(400, "上下文不能为空", code="empty_context")
        return _aa_suggest_questions(self._work_dir(), context)

    # --- helpers not exposed via js_api --------------------------------

    def archive_name(self, developer_id: str, *, now=None) -> str:
        """Expose :func:`devkit.archive_name` (for tests)."""
        return archive_name(developer_id, now=now)


__all__ = [
    "DevKitApi",
    "serialize_error",
]
