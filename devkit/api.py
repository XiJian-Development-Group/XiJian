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
    cooldown_remaining,
    delete_local_archive,
    get_submission,
    last_submit_for,
    list_submissions,
    submit,
)
from devkit.character_editor import (
    list_characters as _ce_list,
    get_character as _ce_get,
    save_character as _ce_save,
    delete_character as _ce_delete,
    export_character_for_submit as _ce_export,
    import_persona as _ce_import_persona,
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
)
from devkit.model_viewer import (
    list_models as _mv_list,
    register_model as _mv_register,
    unregister_model as _mv_unregister,
    get_model_info as _mv_info,
)
from devkit.voice_cloner import (
    list_voices as _vc_list,
    list_characters_with_voices as _vc_chars,
    get_voice as _vc_get,
    save_voice as _vc_save,
    delete_voice as _vc_delete,
    list_engines as _vc_engines,
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
            resolved_all = self._resolve_packages(package_ids)
            target_kind = resolved_all["target_kind"]
            target_id = resolved_all["target_id"]
            file_entries = resolved_all["file_entries"]
            payload = payload or resolved_all["payload"]
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
        return submit(
            developer_id=developer_id,
            target_kind=target_kind,
            target_id=target_id,
            payload=payload if isinstance(payload, Mapping) else None,
            file_entries=list(file_entries) if file_entries else None,
            smtp_send=smtp_send if callable(smtp_send) else None,
            archive_path=archive_path if isinstance(archive_path, str) else None,
        )

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
            os.path.join(os.path.expanduser("~"), "隙间Dev"),
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
    ) -> dict[str, Any]:
        if not isinstance(character_id, str) or not character_id:
            raise DevKitError(400, "角色 ID 不能为空", code="missing_char_id")
        if not isinstance(name, str) or not name:
            raise DevKitError(400, "声音名称不能为空", code="missing_name")
        return _vc_save(
            self._work_dir(),
            character_id,
            name,
            sample_path=sample_path if isinstance(sample_path, str) else None,
            engine=engine if isinstance(engine, str) else "melo-tts",
        )

    @_serialize_call
    def delete_voice(self, voice_id: Any) -> dict[str, Any]:
        if not isinstance(voice_id, str) or not voice_id:
            raise DevKitError(400, "声音 ID 不能为空", code="missing_voice_id")
        ok = _vc_delete(self._work_dir(), voice_id)
        return {"deleted": ok}

    # --- helpers not exposed via js_api --------------------------------

    def archive_name(self, developer_id: str, *, now=None) -> str:
        """Expose :func:`devkit.archive_name` (for tests)."""
        return archive_name(developer_id, now=now)


__all__ = [
    "DevKitApi",
    "serialize_error",
]
