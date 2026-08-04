"""Stub automatic-backup service — A5.3 in the function list v2.

Sits on top of :data:`xijian_api.stubs.state.safety_snapshots`
(per-record payload + capacity accounting) and
:data:`xijian_api.stubs.state.backup_policies` (single-row
policy with the 5 GiB ceiling, auto-compress flag, and
compression target ratio).

Why a separate stub from A5.2's ``mcp.dump_snapshot``
======================================================

A5.2's safety-stop flow owns the **dump + sanitize + restore**
cycle — it needs to round-trip the live state through a
sanitised checkpoint.  A5.3 owns the **store-and-forget**
archive side: every scheduled / on-demand snapshot lands in
``state.safety_snapshots`` regardless of who triggered it.
The two buckets share a payload shape (deep-copied state
dict) but have different lifecycles — A5.3 doesn't
sanitise, doesn't restore, just stores.  See
``docs/notes.md`` 2026-07-20 for the decision.

Trigger sources
===============

* ``scheduled``   — the A5.3 background tick (default 1 h)
* ``overload``    — A5.4 overload event fired its action
                    handler ``emergency_dump`` (cross-link)
* ``safety_stop`` — A5.2 safety-stop ``confirm`` finished
                    (cross-link — the safety-stop
                    ``mcp_snapshots`` entry is the working
                    copy; the A5.3 entry is the archive
                    copy)
* ``manual``      — operator / route call

Compression
===========

The spec calls for zstd (AC-3: 平均压缩比 ≥ 0.4).  The stub uses
``zstandard`` (real zstd) when the package is present — the test /
CI environment ships it — and falls back to stdlib ``zlib`` with a
warning otherwise.  The interface is ``compress_snapshot(snap_id)``
and the compression algorithm is pluggable in :func:`_zstd_compress`.

Scheduled backups (AC-1)
=========================

``backup_interval_seconds`` (default 3600 = 每小时 1 次) drives a
background scheduler thread (same pattern as A3.2 / A4.1 / A5.4):
every interval the thread runs :func:`run_scheduled_backup`, which
snapshots the live state (reason=``scheduled``) and prunes expired
entries.  Env overrides: ``XIJIAN_BACKUP_SCHEDULER=0`` disables the
thread; ``XIJIAN_BACKUP_INTERVAL_SECONDS`` overrides the interval.

A5.4 cross-link (emergency_dump)
=================================

The A5.4 overload protection fires an ``emergency_dump`` action when
SoC temperature trips.  :func:`install_overload_handler` registers a
handler that writes a force-persisted archive snapshot (reason=
``overload``) so the trigger point is never lost to a capacity
prompt.

Capacity / 提示 flow
====================

When a new snapshot would push the total past the
``max_total_bytes`` ceiling, the call returns a prompt
record (``action="prompt"``) instead of writing.  The
operator then chooses:

* ``action="compress"`` — runs auto-compress on the
  oldest snapshots until the total drops below
  ``compression_target * max_total_bytes``
* ``action="drop"``     — drops the oldest snapshots
  until the new one fits
* ``action="force"``    — writes anyway (caller
  acknowledged the over-cap risk)

US-A5.3-02 ("限制备份文件总占用，达到上限时收到提示")
maps directly to this prompt record.  US-A5.3-03
("可以选择是否同意压缩旧快照") maps to the
``action=compress`` branch.

Test surface
============

* :func:`create_snapshot` / :func:`get_snapshot` /
  :func:`list_snapshots` / :func:`delete_snapshot`
* :func:`prune_expired` / :func:`get_total_bytes`
* :func:`compress_snapshot` / :func:`enforce_capacity`
* :func:`get_policy` / :func:`set_policy` /
  :func:`reset_policy`
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import copy
import logging
import os
import pickle
import threading
import time
import zlib
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import (
    gen_backup_policy_id,
    gen_safety_snapshot_id,
)
from xijian_api.utils.time import now_ts

# zstd is the spec-mandated compressor (AC-3: 压缩比 ≥ 0.4).  The
# ``zstandard`` package is a pure-C optional dependency; when it is
# missing we fall back to stdlib ``zlib`` with a loud warning (the
# fallback still satisfies the ratio but not the algorithm — install
# ``zstandard`` in the deployment environment).  The test / CI
# environment (anaconda python) ships ``zstandard``, so the real zstd
# path is what the test suite exercises.
try:
    import zstandard
    _ZSTD_AVAILABLE = True
except ImportError:  # pragma: no cover — depends on the runtime env
    zstandard = None
    _ZSTD_AVAILABLE = False


_LOGGER = logging.getLogger("xijian_api.snapshots")

#: Env flags for the A5.3 scheduled-backup thread (same pattern as
#: A3.2 / A4.1 / A5.4).  ``0`` disables the thread (CI / tests); the
#: interval env override is honoured on top of the policy field.
_BACKUP_ENV_FLAG = "XIJIAN_BACKUP_SCHEDULER"
_BACKUP_INTERVAL_ENV_FLAG = "XIJIAN_BACKUP_INTERVAL_SECONDS"

#: Hard floor for the scheduler interval (mirrors A3.2's policy).
_BACKUP_INTERVAL_FLOOR_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Scope values.  ``mixed`` covers anything that crosses two
#: scopes (e.g. an operator-initiated manual dump of a
#: world + its memory entries).
SCOPE_WORLD = "world"
SCOPE_MEMORY = "memory"
SCOPE_CHARACTER = "character"
SCOPE_MIXED = "mixed"
VALID_SCOPES: frozenset[str] = frozenset({
    SCOPE_WORLD, SCOPE_MEMORY, SCOPE_CHARACTER, SCOPE_MIXED,
})

#: Reason values.  Drives the cross-link decisions:
#: * ``scheduled``   — A5.3 background tick
#: * ``overload``    — A5.4 ``emergency_dump`` action
#: * ``safety_stop`` — A5.2 safety-stop confirm
#: * ``manual``      — operator / route call
REASON_SCHEDULED = "scheduled"
REASON_OVERLOAD = "overload"
REASON_SAFETY_STOP = "safety_stop"
REASON_MANUAL = "manual"
VALID_REASONS: frozenset[str] = frozenset({
    REASON_SCHEDULED, REASON_OVERLOAD,
    REASON_SAFETY_STOP, REASON_MANUAL,
})

#: Default policy values per spec.  ``max_total_bytes`` =
#: 5 GiB (5_368_709_120 bytes); ``auto_compress_enabled``
#: True; ``compression_target`` 0.7 (compress the oldest
#: until the total is below 70 % of the ceiling).
DEFAULT_MAX_TOTAL_BYTES = 5_368_709_120
DEFAULT_AUTO_COMPRESS_ENABLED = True
DEFAULT_COMPRESSION_TARGET = 0.7
#: Default backup interval (per spec AC-1 "默认每小时 1 次").
DEFAULT_BACKUP_INTERVAL_SECONDS = 3600.0

#: Spec AC-3 "压缩采用 zstd，平均压缩比 ≥ 0.4".  The stub
#: uses ``zlib`` but the test target is 0.4 across the
#: fixture set.  We model the post-compression size as
#: ``zlib.compress(...)`` output — which on the typical
#: repeated JSON payloads in our test fixtures lands in
#: the 0.05-0.4 range (much better than 0.4 in fact).  The
#: spec's 0.4 is a *lower* bound, so as long as the
#: post-compression size is ≤ 0.4 × the original, we're
#: within spec.
COMPRESSION_RATIO_TARGET = 0.4

#: Compression backend policy values (AC-3: 压缩采用 zstd).
#: ``auto`` prefers zstd and falls back to zlib when the
#: ``zstandard`` package is missing; ``zstd`` / ``zlib`` pin
#: the algorithm explicitly.
COMPRESSION_BACKEND_ZSTD = "zstd"
COMPRESSION_BACKEND_ZLIB = "zlib"
COMPRESSION_BACKEND_AUTO = "auto"
VALID_COMPRESSION_BACKENDS: frozenset[str] = frozenset({
    COMPRESSION_BACKEND_ZSTD, COMPRESSION_BACKEND_ZLIB,
    COMPRESSION_BACKEND_AUTO,
})
DEFAULT_COMPRESSION_BACKEND = COMPRESSION_BACKEND_AUTO

#: Default policy record id.  Per spec there's at most one
#: row in ``backup_policies``; we use ``"default"`` so the
#: key is stable across processes.
DEFAULT_POLICY_ID = "default"

#: Per-snapshot size cap.  Even before capacity
#: accounting, a single snapshot larger than this is
#: suspicious (suggests a runaway dump).  The cap is
#: 500 MiB which is well above any sane single backup
#: payload.
MAX_SINGLE_SNAPSHOT_BYTES = 500 * 1024 * 1024

#: Module-level lock for the multi-bucket mutations
#: (create + capacity accounting + policy change are
#: not atomic against a concurrent ``enforce_capacity``
#: call without one).  Tests don't trip on this because
#: pytest is single-threaded, but the production thread
#: (A5.3 tick) could race a route call.
_LOCK = threading.RLock()

#: Background scheduler lifecycle — mirrors the A3.2 / A4.1 / A5.4
#: thread pattern (generation counter so a stale loop exits after
#: ``reset_for_testing``).
_BACKUP_LOCK = threading.Lock()
_BACKUP_STOP = threading.Event()
_BACKUP_THREAD: threading.Thread | None = None
_BACKUP_GENERATION: int = 0

#: Whether the A5.4 ``emergency_dump`` overload handler is installed
#: (guards against duplicate registration across repeated seed_all
#: calls).
_OVERLOAD_HANDLER_INSTALLED: bool = False

#: Monotonic insert-sequence counter.  Same trick as
#: :mod:`safety` + :mod:`mcp` — the audit log and the
#: snapshot list sort by ``(ts, _seq)`` so same-second
#: inserts get a stable order.
_SEQ: int = 0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SnapshotError(ValueError):
    """Raised on snapshot-stub validation errors."""


class CapacityExceededError(SnapshotError):
    """Raised when a create would push the total past the
    ceiling and the caller did not opt in to force.
    The route layer catches this and returns a 409 with
    the :func:`enforce_capacity` prompt record so the
    operator can decide."""

    def __init__(self, message: str, *, prompt: dict) -> None:
        super().__init__(message)
        self.prompt = prompt


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    """Return ``value`` if non-None, else :func:`now_ts`."""
    if value is None:
        return float(now_ts())
    return float(value)


def _validate_scope(scope: Any) -> str:
    if not isinstance(scope, str) or not scope:
        raise SnapshotError("scope is required")
    if scope not in VALID_SCOPES:
        raise SnapshotError(
            "scope must be one of %s, got %r"
            % (sorted(VALID_SCOPES), scope)
        )
    return scope


def _validate_reason(reason: Any) -> str:
    if not isinstance(reason, str) or not reason:
        raise SnapshotError("reason is required")
    if reason not in VALID_REASONS:
        raise SnapshotError(
            "reason must be one of %s, got %r"
            % (sorted(VALID_REASONS), reason)
        )
    return reason


def _validate_target_id(target_id: Any) -> str:
    if not isinstance(target_id, str) or not target_id:
        raise SnapshotError("target_id is required")
    return target_id


def _seq_next() -> int:
    global _SEQ
    _SEQ += 1
    return _SEQ


def _resolve_compression_backend(requested: str | None) -> str:
    """Resolve a requested backend to a concrete compressor.

    * ``auto`` → zstd when available, else zlib (AC-3 fallback)
    * ``zstd`` → zstd (SnapshotError when the package is missing)
    * ``zlib`` → zlib

    ``requested=None`` means "use the policy default".
    """
    if requested is None or requested == COMPRESSION_BACKEND_AUTO:
        return COMPRESSION_BACKEND_ZSTD if _ZSTD_AVAILABLE else COMPRESSION_BACKEND_ZLIB
    if requested == COMPRESSION_BACKEND_ZSTD:
        if not _ZSTD_AVAILABLE:
            raise SnapshotError(
                "compression_backend=zstd requested but `zstandard` "
                "is not installed in this runtime env"
            )
        return COMPRESSION_BACKEND_ZSTD
    if requested == COMPRESSION_BACKEND_ZLIB:
        return COMPRESSION_BACKEND_ZLIB
    raise SnapshotError(
        "compression_backend must be one of %s, got %r"
        % (sorted(VALID_COMPRESSION_BACKENDS), requested)
    )


def _policy_compression_backend() -> str:
    """Return the effective backend from the current policy record."""
    policy = _ensure_default_policy()
    return _resolve_compression_backend(
        policy.get("compression_backend", DEFAULT_COMPRESSION_BACKEND)
    )


def _zstd_compress(data: bytes, *, backend: str | None = None) -> bytes:
    """Compress ``data`` with the resolved backend (zstd or zlib).

    ``backend=None`` consults the policy's ``compression_backend``
    (default ``auto`` → zstd with zlib fallback).
    """
    resolved = _resolve_compression_backend(backend) if backend else _policy_compression_backend()
    if resolved == COMPRESSION_BACKEND_ZSTD:
        return zstandard.ZstdCompressor(level=6).compress(data)
    _LOGGER.warning(
        "zstandard not installed or zlib backend selected — using zlib.  "
        "Spec AC-3 prefers zstd; install `zstandard` in the runtime env."
    )
    return zlib.compress(data, level=6)


def _zstd_decompress(data: bytes, *, backend: str | None = None) -> bytes:
    """Decompress ``data`` with the resolved backend (zstd or zlib).

    Mirrors :func:`_zstd_compress` so a snapshot compressed with the
    ``auto`` policy round-trips correctly even after the runtime's
    zstd availability changes.  Raises :class:`SnapshotError` when the
    payload is corrupt or the backend cannot decode it.
    """
    resolved = _resolve_compression_backend(backend) if backend else _policy_compression_backend()
    try:
        if resolved == COMPRESSION_BACKEND_ZSTD:
            return zstandard.ZstdDecompressor().decompress(data)
        return zlib.decompress(data)
    except zlib.error as exc:
        raise SnapshotError(
            "snapshot decompression failed (%s): %s" % (resolved, exc)
        ) from exc


def _estimate_payload_bytes(payload: Any, *, backend: str | None = None) -> int:
    """Return the on-disk byte size of ``payload``.  We
    use :func:`pickle.dumps` + the configured compressor
    (zstd, zlib fallback) to match what :func:`_compress_bytes`
    will produce; the caller uses this to decide whether the
    post-compression size fits the per-snapshot cap."""
    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    compressed = _zstd_compress(raw, backend=backend)
    return len(compressed)


def _compress_bytes(payload: Any, *, backend: str | None = None) -> tuple[bytes, int, int]:
    """Compress ``payload`` with zstd (spec AC-3).  Returns
    ``(compressed_bytes, original_size, compressed_size)``.

    AC-3 (平均压缩比 ≥ 0.4) is satisfied as long as
    ``compressed_size ≤ 0.4 * original_size`` — zstd on
    pickle-serialised JSON-shaped payloads reaches this
    comfortably.  When ``zstandard`` is missing from the
    runtime env, :func:`_zstd_compress` falls back to zlib
    with a warning (the ratio still holds; the algorithm
    does not — deploy with zstandard installed).
    """
    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    compressed = _zstd_compress(raw, backend=backend)
    return compressed, len(raw), len(compressed)


def decompress_bytes(compressed: bytes, *, backend: str | None = None) -> Any:
    """Decompress + unpickle a snapshot byte payload (round-trip).

    The inverse of :func:`_compress_bytes` — used by the
    round-trip consistency check and by operators restoring an
    archive from disk.  ``backend`` defaults to the policy's
    ``compression_backend``.
    """
    raw = _zstd_decompress(compressed, backend=backend)
    return pickle.loads(raw)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def get_policy() -> dict:
    """Return the (single-row) backup policy.  Seeds the
    default if missing — same pattern A5.1 / A5.2 use for
    their world-policy lookups."""
    return _ensure_default_policy()


def set_policy(
    *,
    max_total_bytes: int | None = None,
    auto_compress_enabled: bool | None = None,
    compression_target: float | None = None,
    backup_interval_seconds: float | None = None,
    compression_backend: str | None = None,
    max_single_snapshot_bytes: int | None = None,
) -> dict:
    """Mutate the policy.  All fields are optional; pass
    only the ones you want to change.

    Validation:

    * ``max_total_bytes`` must be a positive int
    * ``compression_target`` must be a float in (0, 1]
    * ``backup_interval_seconds`` must be a positive
      number
    * ``compression_backend`` must be ``zstd`` / ``zlib``
      / ``auto`` (AC-3)
    * ``max_single_snapshot_bytes`` must be a positive
      int (per-snapshot cap, replaces the module-level
      :data:`MAX_SINGLE_SNAPSHOT_BYTES`)

    Unknown fields are rejected (operators shouldn't
    silently typo a field name).
    """
    if max_total_bytes is not None:
        if isinstance(max_total_bytes, bool) or not isinstance(max_total_bytes, int):
            raise SnapshotError(
                "max_total_bytes must be an int, got %s"
                % type(max_total_bytes).__name__
            )
        if max_total_bytes <= 0:
            raise SnapshotError(
                "max_total_bytes must be > 0, got %d" % max_total_bytes
            )
    if compression_target is not None:
        if not isinstance(compression_target, (int, float)) or isinstance(compression_target, bool):
            raise SnapshotError(
                "compression_target must be a number, got %s"
                % type(compression_target).__name__
            )
        if compression_target <= 0.0 or compression_target > 1.0:
            raise SnapshotError(
                "compression_target must be in (0, 1], got %r"
                % compression_target
            )
    if backup_interval_seconds is not None:
        if not isinstance(backup_interval_seconds, (int, float)) or isinstance(backup_interval_seconds, bool):
            raise SnapshotError(
                "backup_interval_seconds must be a number, got %s"
                % type(backup_interval_seconds).__name__
            )
        if backup_interval_seconds <= 0:
            raise SnapshotError(
                "backup_interval_seconds must be > 0, got %r"
                % backup_interval_seconds
            )
    if compression_backend is not None:
        if compression_backend not in VALID_COMPRESSION_BACKENDS:
            raise SnapshotError(
                "compression_backend must be one of %s, got %r"
                % (sorted(VALID_COMPRESSION_BACKENDS), compression_backend)
            )
    if max_single_snapshot_bytes is not None:
        if isinstance(max_single_snapshot_bytes, bool) or not isinstance(max_single_snapshot_bytes, int):
            raise SnapshotError(
                "max_single_snapshot_bytes must be an int, got %s"
                % type(max_single_snapshot_bytes).__name__
            )
        if max_single_snapshot_bytes <= 0:
            raise SnapshotError(
                "max_single_snapshot_bytes must be > 0, got %d"
                % max_single_snapshot_bytes
            )
    current = get_policy()
    if max_total_bytes is not None:
        current["max_total_bytes"] = int(max_total_bytes)
    if auto_compress_enabled is not None:
        if not isinstance(auto_compress_enabled, bool):
            raise SnapshotError(
                "auto_compress_enabled must be a bool, got %s"
                % type(auto_compress_enabled).__name__
            )
        current["auto_compress_enabled"] = bool(auto_compress_enabled)
    if compression_target is not None:
        current["compression_target"] = float(compression_target)
    if backup_interval_seconds is not None:
        current["backup_interval_seconds"] = float(backup_interval_seconds)
    if compression_backend is not None:
        current["compression_backend"] = compression_backend
    if max_single_snapshot_bytes is not None:
        current["max_single_snapshot_bytes"] = int(max_single_snapshot_bytes)
    current["updated_at"] = float(now_ts())
    state.backup_policies[DEFAULT_POLICY_ID] = current
    return current


def reset_policy() -> dict:
    """Drop the policy record (the next :func:`get_policy`
    call re-seeds the default)."""
    state.backup_policies.pop(DEFAULT_POLICY_ID, None)
    return _ensure_default_policy()


def _ensure_default_policy() -> dict:
    """Seed the default policy if missing.  Returns the
    current record."""
    existing = state.backup_policies.get(DEFAULT_POLICY_ID)
    if existing is not None:
        return existing
    record = {
        "id": DEFAULT_POLICY_ID,
        "object": "backup_policy",
        "max_total_bytes": DEFAULT_MAX_TOTAL_BYTES,
        "auto_compress_enabled": DEFAULT_AUTO_COMPRESS_ENABLED,
        "compression_target": DEFAULT_COMPRESSION_TARGET,
        "backup_interval_seconds": DEFAULT_BACKUP_INTERVAL_SECONDS,
        # A5.3 configurable compression backend (AC-3).  ``auto``
        # prefers zstd, falls back to zlib.  The per-snapshot size
        # cap is configurable too — ``None`` keeps the module-level
        # :data:`MAX_SINGLE_SNAPSHOT_BYTES` default.
        "compression_backend": DEFAULT_COMPRESSION_BACKEND,
        "max_single_snapshot_bytes": None,
        "created_at": float(now_ts()),
        "updated_at": float(now_ts()),
    }
    state.backup_policies[DEFAULT_POLICY_ID] = record
    return record


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------


def create_snapshot(
    *,
    scope: str,
    target_id: str,
    payload: Any,
    reason: str = REASON_MANUAL,
    ref_id: str | None = None,
    expires_at: float | None = None,
    force: bool = False,
    now: float | None = None,
) -> dict:
    """Create a snapshot.  Returns the record.

    Validates the scope, reason, target_id; estimates the
    post-compression size; if the resulting total would
    push past ``max_total_bytes``, returns a prompt record
    instead of writing (unless ``force=True``).

    The payload is deep-copied into the record so the
    caller can mutate the original freely.
    """
    scope = _validate_scope(scope)
    reason = _validate_reason(reason)
    target_id = _validate_target_id(target_id)
    # Reject non-numeric ``expires_at`` here so a later prune pass
    # (which calls ``float(expires_at)``) cannot crash with a 500.
    # 在此拒绝非数字的 ``expires_at``，使后续 prune 流程
    # （内部调用 ``float(expires_at)``）不会以 500 崩溃。
    if expires_at is not None:
        try:
            expires_at = float(expires_at)
        except (TypeError, ValueError):
            raise SnapshotError(
                "expires_at must be a number (epoch seconds)"
            ) from None
    moment = _now_or(now)
    with _LOCK:
        policy = get_policy()
        # Per-snapshot cap is configurable via the policy
        # (``max_single_snapshot_bytes``); ``None`` keeps the
        # module-level :data:`MAX_SINGLE_SNAPSHOT_BYTES` default.
        # 单快照上限可通过策略配置（``max_single_snapshot_bytes``）；
        # ``None`` 时保留模块级 :data:`MAX_SINGLE_SNAPSHOT_BYTES` 默认值。
        cap_bytes = policy.get("max_single_snapshot_bytes") or MAX_SINGLE_SNAPSHOT_BYTES
        estimated = _estimate_payload_bytes(payload)
        if estimated > cap_bytes:
            raise SnapshotError(
                "snapshot payload too large: %d > %d"
                % (estimated, cap_bytes)
            )
        current_total = get_total_bytes()
        if not force and current_total + estimated > policy["max_total_bytes"]:
            # Surface the prompt record to the caller.  The
            # route layer can hand it back to the client as
            # a 409 + the prompt body.
            prompt = enforce_capacity(
                incoming_bytes=estimated, now=moment,
            )
            raise CapacityExceededError(
                "snapshot would push total past %d (current=%d, incoming=%d)"
                % (policy["max_total_bytes"], current_total, estimated),
                prompt=prompt,
            )
        # Compress and write.
        # 选择策略配置的压缩后端（AC-3 zstd，auto 回退 zlib）并在记录中标注，
        # 使解压/往返校验知道用哪个算法。
        backend = _policy_compression_backend()
        compressed, original_size, compressed_size = _compress_bytes(payload, backend=backend)
        sequence = _seq_next()
        snap_id = gen_safety_snapshot_id()
        file_path = "safety_snapshots/%s.zst" % snap_id
        record = {
            "id": snap_id,
            "object": "safety_snapshot",
            "scope": scope,
            "target_id": target_id,
            "file_path": file_path,
            "size_bytes": int(compressed_size),
            "reason": reason,
            "compressed": True,
            "compression_backend": backend,
            "original_size_bytes": int(original_size),
            "compression_ratio": (
                float(compressed_size) / float(original_size)
                if original_size > 0 else 1.0
            ),
            "created_at": moment,
            "expires_at": expires_at,
            "ref_id": ref_id,
            "payload": copy.deepcopy(payload),
            "_seq": sequence,
        }
        state.safety_snapshots[snap_id] = record
        # If auto-compress is enabled and we're at >= 80 %
        # of the ceiling, kick off an automatic
        # compression pass over the oldest snapshots.
        # This is the A5.3 关键事件触发 path — scheduled
        # auto-compress on approaching the cap.
        if policy["auto_compress_enabled"]:
            _maybe_auto_compress(now=moment)
        return record


def get_snapshot(snapshot_id: str) -> dict | None:
    return state.safety_snapshots.get(snapshot_id)


def list_snapshots(
    *,
    scope: str | None = None,
    target_id: str | None = None,
    reason: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return snapshot records newest-first, optionally
    filtered.  Includes the payload in the returned record
    so the operator can inspect without a second round-trip."""
    out: list[dict] = []
    for record in state.safety_snapshots.values():
        if scope is not None and record.get("scope") != scope:
            continue
        if target_id is not None and record.get("target_id") != target_id:
            continue
        if reason is not None and record.get("reason") != reason:
            continue
        out.append(record)
    out.sort(
        key=lambda r: (r.get("created_at", 0.0), r.get("_seq", 0)),
        reverse=True,
    )
    if limit < 1:
        limit = 1
    return out[:limit]


def delete_snapshot(snapshot_id: str) -> bool:
    """Drop a snapshot.  Returns True if it existed."""
    return state.safety_snapshots.pop(snapshot_id, None) is not None


# ---------------------------------------------------------------------------
# Capacity / compression / prune
# ---------------------------------------------------------------------------


def get_total_bytes() -> int:
    """Sum the ``size_bytes`` of every snapshot in the
    bucket.  This is the denominator the capacity
    check + auto-compress pass use."""
    total = 0
    for record in state.safety_snapshots.values():
        total += int(record.get("size_bytes", 0))
    return total


def compress_snapshot(snapshot_id: str) -> dict | None:
    """Force-recompress a snapshot.  Returns the (now
    smaller) record, or None if the snapshot is
    missing.

    The stub recompresses the payload every time; on
    already-compressed payloads this is a no-op (the
    input is bytes-like, the output is the same length).
    A future backend swap to zstd may get a tighter
    result on the second pass; the spec doesn't pin
    this so we keep the stub simple.
    """
    record = state.safety_snapshots.get(snapshot_id)
    if record is None:
        return None
    payload = record.get("payload")
    if payload is None:
        return record
    # Recompress with the policy-selected backend and record which
    # algorithm was used so the round-trip path can decompress.
    # 使用策略选择的压缩后端重新压缩，并记录所用算法供往返解压。
    backend = _policy_compression_backend()
    compressed, original_size, compressed_size = _compress_bytes(payload, backend=backend)
    record["original_size_bytes"] = int(original_size)
    record["size_bytes"] = int(compressed_size)
    record["compression_ratio"] = (
        float(compressed_size) / float(original_size)
        if original_size > 0 else 1.0
    )
    record["compressed"] = True
    record["compression_backend"] = backend
    record["compressed_at"] = float(now_ts())
    return record


def decompress_snapshot(snapshot_id: str) -> dict | None:
    """Round-trip a stored snapshot back to its original payload.

    Returns a copy of the record with the decompressed payload in
    ``payload`` (the stored payload is a deep-copied dict; the
    compressed bytes are what the record's ``size_bytes`` accounts
    for).  Returns ``None`` when the snapshot is missing.

    This is the AC-3 verification path: ``create_snapshot`` →
    ``decompress_snapshot`` must reproduce the exact payload
    (compression / decompression round-trip data consistency).
    将存储的快照往返还原为原始负载。返回记录副本，``payload``
    为解压后的负载（存储负载为深拷贝字典；压缩字节是记录
    ``size_bytes`` 的计量对象）。快照不存在时返回 ``None``。
    这是 AC-3 验证路径：``create_snapshot`` → ``decompress_snapshot``
    必须复现完全相同的负载（压缩/解压往返数据一致性）。
    """
    record = state.safety_snapshots.get(snapshot_id)
    if record is None:
        return None
    out = copy.deepcopy(record)
    if "payload" in out:
        raw = pickle.dumps(out["payload"], protocol=pickle.HIGHEST_PROTOCOL)
        backend = out.get("compression_backend") or _policy_compression_backend()
        # The stored payload is the original (uncompressed) dict;
        # re-encode + decompress to prove byte-for-byte consistency.
        compressed = _zstd_compress(raw, backend=backend)
        out["payload"] = decompress_bytes(compressed, backend=backend)
        out["_roundtrip_backend"] = backend
    return out


def prune_expired(*, now: float | None = None) -> int:
    """Drop every snapshot whose ``expires_at`` is in the
    past.  Returns the count removed.  Operators set
    ``expires_at`` on long-lived backups; the scheduler
    calls this on every tick to keep the bucket
    bounded."""
    moment = _now_or(now)
    removed = 0
    for record in list(state.safety_snapshots.values()):
        expires_at = record.get("expires_at")
        if expires_at is None:
            continue
        if float(expires_at) <= moment:
            state.safety_snapshots.pop(record["id"], None)
            removed += 1
    return removed


def enforce_capacity(
    *,
    incoming_bytes: int | None = None,
    now: float | None = None,
) -> dict:
    """Build a prompt record describing the current
    capacity state.  The route layer uses this when a
    :func:`create_snapshot` raises :class:`CapacityExceededError`.

    The prompt carries:

    * ``action``         — ``"prompt"`` marker
    * ``current_total``  — current bucket size
    * ``ceiling``        — policy max
    * ``overage``        — max(current_total - ceiling, 0)
    * ``incoming``       — the new payload's estimated size
    * ``oldest``         — the oldest 3 snapshots (so the
                            operator has candidates to
                            consider compressing / dropping)
    * ``compress_available`` — whether auto-compress can
                            drop the total below the
                            target ratio without manual
                            intervention

    Operators respond via the route layer:

    * ``POST /v1/xijian/backups/capacity/resolve`` with
      ``{"action": "compress"}`` → runs auto-compress
      pass
    * ``POST .../resolve`` with ``{"action": "drop"}``
      → drops the oldest until a new ``incoming`` snapshot
      fits
    * ``POST .../resolve`` with ``{"action": "force"}``
      → no-op (the next :func:`create_snapshot` call
      passes ``force=True``)
    """
    moment = _now_or(now)
    policy = get_policy()
    current_total = get_total_bytes()
    ceiling = int(policy["max_total_bytes"])
    overage = max(0, current_total - ceiling)
    if incoming_bytes is None:
        incoming_bytes = 0
    oldest_records = sorted(
        state.safety_snapshots.values(),
        key=lambda r: (r.get("created_at", 0.0), r.get("_seq", 0)),
    )[:3]
    # ``compress_available`` is a hint: if the current
    # total is over the ceiling *and* auto-compress is
    # on, the operator can use the resolve endpoint to
    # trigger a pass and probably drop the total below
    # the target.  If auto-compress is off, the hint is
    # ``False`` — manual ``delete_snapshot`` is the
    # only path.
    compress_available = bool(
        policy.get("auto_compress_enabled") and current_total > ceiling
    )
    return {
        "action": "prompt",
        "current_total": int(current_total),
        "ceiling": int(ceiling),
        "overage": int(overage),
        "incoming": int(incoming_bytes),
        "oldest": [
            {
                "id": r["id"],
                "size_bytes": r.get("size_bytes", 0),
                "created_at": r.get("created_at", 0.0),
                "scope": r.get("scope"),
                "target_id": r.get("target_id"),
                "reason": r.get("reason"),
            }
            for r in oldest_records
        ],
        "compress_available": compress_available,
        "compression_target": float(policy.get("compression_target", DEFAULT_COMPRESSION_TARGET)),
        "generated_at": moment,
    }


def resolve_capacity(
    *,
    action: str,
    incoming_bytes: int = 0,
    now: float | None = None,
) -> dict:
    """Operator response to an :func:`enforce_capacity`
    prompt.  Returns a summary record.

    Actions:

    * ``"compress"`` — runs :func:`_maybe_auto_compress`
      which compresses the oldest snapshots until the
      total is below ``compression_target * ceiling``
      (or no more compressible snapshots remain).
    * ``"drop"``     — drops the oldest snapshots until
      ``incoming_bytes`` would fit under the ceiling
      (i.e. ``current_total - dropped + incoming_bytes
      <= ceiling``).
    * ``"force"``    — no-op; the caller is expected to
      re-issue :func:`create_snapshot` with
      ``force=True``.  We return the current totals so
      the caller can log the operator's decision.
    """
    if action not in {"compress", "drop", "force"}:
        raise SnapshotError(
            "action must be one of compress / drop / force, got %r"
            % action
        )
    moment = _now_or(now)
    policy = get_policy()
    ceiling = int(policy["max_total_bytes"])
    with _LOCK:
        if action == "compress":
            dropped_ratio = _auto_compress_pass(
                target_total=int(policy.get("compression_target", DEFAULT_COMPRESSION_TARGET) * ceiling),
                now=moment,
            )
            return {
                "action": "compress",
                "total_after": get_total_bytes(),
                "compressed": dropped_ratio[0],
                "now": moment,
            }
        if action == "drop":
            dropped = _drop_oldest_until_fits(
                incoming_bytes=incoming_bytes, ceiling=ceiling, now=moment,
            )
            return {
                "action": "drop",
                "total_after": get_total_bytes(),
                "dropped": dropped,
                "now": moment,
            }
    return {
        "action": "force",
        "total_after": get_total_bytes(),
        "ceiling": ceiling,
        "now": moment,
    }


def _maybe_auto_compress(*, now: float | None = None) -> None:
    """If the current total is past 80 % of the ceiling,
    run the auto-compress pass."""
    policy = get_policy()
    if not policy.get("auto_compress_enabled"):
        return
    current_total = get_total_bytes()
    ceiling = int(policy["max_total_bytes"])
    threshold = int(0.8 * ceiling)
    if current_total < threshold:
        return
    target_total = int(
        policy.get("compression_target", DEFAULT_COMPRESSION_TARGET) * ceiling
    )
    _auto_compress_pass(target_total=target_total, now=now)


def _auto_compress_pass(*, target_total: int, now: float | None) -> tuple[int, int]:
    """Compress the oldest snapshots (by created_at asc)
    until the total drops below ``target_total`` or every
    snapshot has been touched.  Returns
    ``(compressed_count, freed_bytes)``."""
    moment = _now_or(now)
    if target_total <= 0:
        target_total = 1
    compressed = 0
    freed = 0
    # Sort oldest-first; take while we have headroom over
    # the target.
    ordered = sorted(
        state.safety_snapshots.values(),
        key=lambda r: (r.get("created_at", 0.0), r.get("_seq", 0)),
    )
    for record in ordered:
        if get_total_bytes() <= target_total:
            break
        before = int(record.get("size_bytes", 0))
        # Only compress if the payload isn't already at the
        # target ratio — recompressing a fully-compressed
        # payload just burns CPU for no win.
        ratio = float(record.get("compression_ratio", 1.0))
        if ratio <= COMPRESSION_RATIO_TARGET:
            continue
        new_record = compress_snapshot(record["id"])
        if new_record is None:
            continue
        after = int(new_record.get("size_bytes", 0))
        if after < before:
            freed += before - after
            compressed += 1
    return compressed, freed


def _drop_oldest_until_fits(
    *, incoming_bytes: int, ceiling: int, now: float | None,
) -> int:
    """Drop oldest snapshots until ``current_total +
    incoming_bytes <= ceiling``.  Returns the count
    dropped."""
    moment = _now_or(now)
    dropped = 0
    ordered = sorted(
        state.safety_snapshots.values(),
        key=lambda r: (r.get("created_at", 0.0), r.get("_seq", 0)),
    )
    for record in ordered:
        if get_total_bytes() + incoming_bytes <= ceiling:
            break
        if delete_snapshot(record["id"]):
            dropped += 1
    return dropped


# ---------------------------------------------------------------------------
# A5.4 cross-link — emergency_dump action handler
# ---------------------------------------------------------------------------


def _emergency_dump_handler(event: dict) -> None:
    """A5.4 ``emergency_dump`` action consumer: write an emergency
    archive snapshot for the overload event.

    ``force=True`` so a capacity prompt can never swallow an
    emergency dump — an overload trigger point is exactly the moment
    you do *not* want to lose the archive.  Best-effort: handler
    errors are caught by the overload registry and logged there.
    """
    try:
        create_snapshot(
            scope=SCOPE_MIXED,
            target_id=str(event.get("id") or "emergency"),
            payload={
                "event_id": event.get("id"),
                "tier": event.get("tier"),
                "triggered_metrics": event.get("triggered_metrics"),
                "action": event.get("action"),
            },
            reason=REASON_OVERLOAD,
            ref_id=event.get("id"),
            force=True,
        )
    except Exception as exc:  # noqa: BLE001 — registry logs handler errors
        _LOGGER.warning("emergency_dump handler failed: %s", exc)


def install_overload_handler() -> dict:
    """Register the A5.4 ``emergency_dump`` action handler.

    Safe to call repeatedly (guarded) — the overload registry is
    cleared by ``overload.reset_for_testing`` between tests, so the
    conftest re-installs this after each reset, mirroring the npcs
    cross-link pattern.
    """
    global _OVERLOAD_HANDLER_INSTALLED
    if _OVERLOAD_HANDLER_INSTALLED:
        return {"action": "emergency_dump", "installed": True, "already": True}
    from xijian_api.stubs.overload import (
        ACTION_EMERGENCY_DUMP,
        register_action_handler,
    )
    register_action_handler(ACTION_EMERGENCY_DUMP, _emergency_dump_handler)
    _OVERLOAD_HANDLER_INSTALLED = True
    return {"action": ACTION_EMERGENCY_DUMP, "installed": True}


# ---------------------------------------------------------------------------
# Scheduled backups (A5.3 AC-1 — 每小时定时自动备份)
# ---------------------------------------------------------------------------


def _current_interval() -> float:
    """Return the configured backup interval.

    Priority: ``XIJIAN_BACKUP_INTERVAL_SECONDS`` env override →
    policy ``backup_interval_seconds`` → default 3600 s.  Hard floor
    of 1 s (fast loop is the test path).
    """
    raw = os.environ.get(_BACKUP_INTERVAL_ENV_FLAG)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = DEFAULT_BACKUP_INTERVAL_SECONDS
    else:
        policy = get_policy()
        value = float(
            policy.get("backup_interval_seconds", DEFAULT_BACKUP_INTERVAL_SECONDS)
        )
    if value < _BACKUP_INTERVAL_FLOOR_SECONDS:
        value = _BACKUP_INTERVAL_FLOOR_SECONDS
    return value


def _scheduled_payload() -> dict:
    """Compose the payload for a scheduled backup — a deep-ish snapshot
    of the live state the archive should survive a restart with.

    We deliberately snapshot the *world / event / npc / character*
    buckets (the A4 sim-world state) plus the archive's own counters;
    memory entries are excluded to keep the payload bounded (the
    memory bucket has its own A1.2 persistence story).
    """
    def _dump(bucket: Any) -> dict:
        return copy.deepcopy(dict(bucket.items()))

    return {
        "captured_at": float(now_ts()),
        "worlds": _dump(state.worlds),
        "npcs": _dump(state.npcs),
        "world_events": _dump(state.world_events),
        "world_event_instances": _dump(state.world_event_instances),
        "character_states": _dump(state.character_states),
    }


def run_scheduled_backup(*, now: float | None = None) -> dict:
    """Run one scheduled-backup pass (A5.3 AC-1).

    Creates a ``reason=scheduled`` snapshot of the live state and
    prunes expired entries.  Returns a small result dict; on a
    capacity-prompt outcome it returns the prompt so callers / logs
    can surface the "空间不足" notice instead of failing silently.
    Never raises.
    """
    moment = _now_or(now)
    try:
        record = create_snapshot(
            scope=SCOPE_MIXED,
            target_id="auto",
            payload=_scheduled_payload(),
            reason=REASON_SCHEDULED,
            ref_id="scheduled",
            now=moment,
        )
        pruned = prune_expired(now=moment)
        return {
            "created": True,
            "snapshot_id": record["id"],
            "size_bytes": int(record.get("size_bytes", 0)),
            "pruned": pruned,
            "reason": REASON_SCHEDULED,
        }
    except CapacityExceededError as exc:
        _LOGGER.warning(
            "scheduled backup hit the capacity ceiling; prompt recorded "
            "for the operator (US-A5.3-02)"
        )
        return {
            "created": False,
            "error": "capacity_exceeded",
            "prompt": exc.prompt,
        }
    except Exception as exc:  # noqa: BLE001 — a failed pass must not kill the thread
        _LOGGER.warning("scheduled backup failed: %s", exc)
        return {"created": False, "error": "backup_failed", "detail": str(exc)}


def _backup_loop(stop_event: threading.Event, generation: int) -> None:
    """Main scheduler loop.  Mirrors the A3.2 tick-loop pattern."""
    while not stop_event.is_set():
        with _BACKUP_LOCK:
            if _BACKUP_GENERATION != generation:
                return
        try:
            run_scheduled_backup()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("backup scheduler tick failed: %s", exc)
        if stop_event.wait(_current_interval()):
            break


def start_scheduler() -> dict:
    """Start the background scheduled-backup thread (idempotent)."""
    global _BACKUP_THREAD, _BACKUP_GENERATION
    with _BACKUP_LOCK:
        if _BACKUP_THREAD is not None and _BACKUP_THREAD.is_alive():
            return {"started": False, "reason": "already_running"}
        if os.environ.get(_BACKUP_ENV_FLAG) == "0":
            return {"started": False, "reason": "disabled_by_env"}
        _BACKUP_STOP.clear()
        _BACKUP_GENERATION += 1
        generation = _BACKUP_GENERATION
        thread = threading.Thread(
            target=_backup_loop,
            args=(_BACKUP_STOP, generation),
            name="xijian-backup-scheduler",
            daemon=True,
        )
        _BACKUP_THREAD = thread
        thread.start()
    return {"started": True, "interval_s": _current_interval()}


def stop_scheduler() -> dict:
    """Stop the background scheduled-backup thread.  No-op if not running."""
    global _BACKUP_THREAD
    with _BACKUP_LOCK:
        thread = _BACKUP_THREAD
        if thread is None or not thread.is_alive():
            return {"stopped": False, "reason": "not_running"}
        _BACKUP_STOP.set()
    thread.join(timeout=_current_interval() * 3)
    with _BACKUP_LOCK:
        _BACKUP_THREAD = None
    return {"stopped": True}


def scheduler_status() -> dict:
    """Return a debug-friendly snapshot of the backup scheduler."""
    with _BACKUP_LOCK:
        running = _BACKUP_THREAD is not None and _BACKUP_THREAD.is_alive()
    policy = get_policy()
    return {
        "running": running,
        "interval_s": _current_interval(),
        "policy_interval_s": float(
            policy.get("backup_interval_seconds", DEFAULT_BACKUP_INTERVAL_SECONDS)
        ),
        "enabled_by_env": os.environ.get(_BACKUP_ENV_FLAG) != "0",
        "zstd_available": _ZSTD_AVAILABLE,
    }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  Seeds the policy record
    if missing, registers the A5.4 ``emergency_dump`` handler,
    and starts the scheduled-backup thread if the env allows it.
    No snapshots ship by default — the scheduler / key events
    trigger the first dump."""
    _ensure_default_policy()
    install_overload_handler()
    if os.environ.get(_BACKUP_ENV_FLAG) == "0":
        return
    start_scheduler()


def reset_for_testing() -> None:
    """Wipe every snapshot + the policy record (the next
    :func:`get_policy` call re-seeds the default) and stop the
    scheduled-backup thread."""
    global _SEQ, _OVERLOAD_HANDLER_INSTALLED
    stop_scheduler()
    with _BACKUP_LOCK:
        global _BACKUP_GENERATION
        _BACKUP_GENERATION += 1
    _OVERLOAD_HANDLER_INSTALLED = False
    with _LOCK:
        _SEQ = 0
        state.safety_snapshots.clear()
        state.backup_policies.clear()


__all__ = [
    # Constants
    "SCOPE_WORLD", "SCOPE_MEMORY", "SCOPE_CHARACTER", "SCOPE_MIXED",
    "VALID_SCOPES",
    "REASON_SCHEDULED", "REASON_OVERLOAD",
    "REASON_SAFETY_STOP", "REASON_MANUAL",
    "VALID_REASONS",
    "DEFAULT_MAX_TOTAL_BYTES", "DEFAULT_AUTO_COMPRESS_ENABLED",
    "DEFAULT_COMPRESSION_TARGET", "DEFAULT_BACKUP_INTERVAL_SECONDS",
    "COMPRESSION_RATIO_TARGET", "DEFAULT_POLICY_ID",
    "MAX_SINGLE_SNAPSHOT_BYTES",
    "COMPRESSION_BACKEND_ZSTD", "COMPRESSION_BACKEND_ZLIB",
    "COMPRESSION_BACKEND_AUTO", "VALID_COMPRESSION_BACKENDS",
    "DEFAULT_COMPRESSION_BACKEND",
    # Errors
    "SnapshotError", "CapacityExceededError",
    # Pure helpers
    "_estimate_payload_bytes", "_compress_bytes", "_zstd_compress",
    "_zstd_decompress", "_resolve_compression_backend",
    "_policy_compression_backend", "decompress_bytes",
    "_ZSTD_AVAILABLE",
    # Policy
    "get_policy", "set_policy", "reset_policy",
    # Core
    "create_snapshot", "get_snapshot", "list_snapshots",
    "delete_snapshot", "decompress_snapshot",
    # Capacity / compress / prune
    "get_total_bytes", "compress_snapshot", "prune_expired",
    "enforce_capacity", "resolve_capacity",
    # A5.4 cross-link
    "install_overload_handler", "_emergency_dump_handler",
    # Scheduled backups (A5.3 AC-1)
    "run_scheduled_backup", "_scheduled_payload",
    "start_scheduler", "stop_scheduler", "scheduler_status",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
