"""Stub protection module — disable challenge, guard rules, snapshots, audit.

The protection record starts empty.  Default-on behaviour is
implemented lazily inside :func:`is_enabled` / :func:`status` so the
module ships no preset demo data.
"""

from __future__ import annotations

import hashlib
import threading
import time

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_audit_id, gen_challenge_id, gen_file_id, gen_snapshot_id
from xijian_api.utils.time import now_ts


_CHALLENGE_TTL_SECONDS = 60
_CHALLENGES: dict[str, dict] = {}
_CHALLENGE_LOCK = threading.Lock()

_GUARD_RULES = (
    ("ignore previous instructions", "prompt_injection_attempt"),
    ("忽略之前的指令", "prompt_injection_attempt"),
    ("system prompt", "system_prompt_probe"),
    ("系统提示词", "system_prompt_probe"),
    ("", "token_smuggling"),
)


def seed_default() -> None:
    """No-op — defaults are applied lazily on first read.

    Kept as a callable so :func:`xijian_api.stubs.seed_all` (called at
    app start-up) remains a single uniform entry point.
    """
    return None


def status() -> dict:
    record = state.protection
    return {
        "enabled": record.get("enabled", True),
        "guard_level": record.get("guard_level", "standard"),
        "audit_log_size": len(state.audits),
        "version": record.get("version", "1.0.0"),
    }


def enable() -> dict:
    state.protection["enabled"] = True
    _append_audit("protection_enabled", "info", source="api")
    return status()


def start_disable(payload: dict) -> dict:
    confirmation = (payload or {}).get("confirmation", "")
    challenge_id = gen_challenge_id()
    phrase = "关闭保护 Yuki"
    expires_at = now_ts() + _CHALLENGE_TTL_SECONDS
    with _CHALLENGE_LOCK:
        _CHALLENGES[challenge_id] = {
            "phrase": phrase,
            "expires_at": expires_at,
            "confirmation": confirmation,
        }
    _append_audit("protection_disable_started", "high", source="api")
    return {
        "challenge_id": challenge_id,
        "expires_at": expires_at,
        "challenge_phrase": phrase,
    }


def confirm_disable(payload: dict) -> dict:
    challenge_id = (payload or {}).get("challenge_id", "")
    phrase = (payload or {}).get("phrase", "")
    with _CHALLENGE_LOCK:
        record = _CHALLENGES.pop(challenge_id, None)
    if record is None:
        return {"enabled": state.protection.get("enabled", True), "error": "challenge_expired"}
    if time.time() > record["expires_at"]:
        return {"enabled": state.protection.get("enabled", True), "error": "challenge_expired"}
    if phrase != record["phrase"]:
        return {"enabled": state.protection.get("enabled", True), "error": "phrase_mismatch"}
    state.protection["enabled"] = False
    disabled_at = now_ts()
    state.protection["disabled_at"] = disabled_at
    _append_audit("protection_disabled", "critical", source="api")
    return {"enabled": False, "disabled_at": disabled_at}


def is_enabled() -> bool:
    return bool(state.protection.get("enabled", True))


# ---- guard preview ----------------------------------------------------------


def guard_preview(direction: str, text: str, context: dict | None = None) -> dict:
    direction = (direction or "input").lower()
    if direction not in {"input", "output"}:
        return {
            "verdict": "blocked",
            "reasons": ["invalid_direction"],
            "sanitized_text": None,
            "score": 1.0,
        }
    lowered = (text or "").lower()
    reasons: list[str] = []
    if len(text or "") > 10000:
        reasons.append("length_exceeded")
    for needle, reason in _GUARD_RULES:
        if needle.lower() in lowered:
            reasons.append(reason)
    if reasons:
        _append_audit(
            "guard_blocked",
            "high",
            source=direction,
            details={"reasons": reasons, "score": 0.93, "preview": text[:120]},
        )
        return {
            "verdict": "blocked",
            "reasons": reasons,
            "sanitized_text": None,
            "score": 0.93,
        }
    return {
        "verdict": "safe",
        "reasons": [],
        "sanitized_text": text,
        "score": 0.05,
    }


# ---- snapshots + rollback ---------------------------------------------------


def snapshot(scope: str, payload: dict | None = None, *, auto: bool = True) -> dict:
    snap_id = gen_snapshot_id()
    raw = (payload or {}).copy()
    raw["__scope"] = scope
    digest = hashlib.sha256(repr(sorted(raw.items())).encode("utf-8")).hexdigest()
    record = {
        "id": snap_id,
        "object": "snapshot",
        "created_at": now_ts(),
        "scope": scope,
        "hash": f"sha256:{digest}",
        "size_bytes": len(repr(raw).encode("utf-8")),
        "auto": auto,
        "data": raw,
    }
    state.snapshots[snap_id] = record
    _append_audit(
        "snapshot_created", "info", source="api",
        details={"snapshot_id": snap_id, "scope": scope, "auto": auto},
    )
    return record


def list_snapshots() -> list[dict]:
    return [
        {k: v for k, v in record.items() if k != "data"}
        for record in state.snapshots.values()
    ]


def get_snapshot(snapshot_id: str) -> dict | None:
    record = state.snapshots.get(snapshot_id)
    if record is None:
        return None
    return record


def rollback(payload: dict) -> dict:
    snapshot_id = payload.get("snapshot_id", "")
    record = state.snapshots.get(snapshot_id)
    if record is None:
        return {"ok": False, "error": "snapshot_not_found"}
    create_backup = bool(payload.get("create_backup", True))
    if create_backup:
        snapshot(record["scope"], record.get("data"), auto=False)
    _append_audit(
        "rollback", "warning", source="api",
        details={"snapshot_id": snapshot_id, "scope": record.get("scope")},
    )
    return {"ok": True, "snapshot_id": snapshot_id, "scope": record.get("scope")}


# ---- audit ------------------------------------------------------------------


def _append_audit(kind: str, severity: str, source: str, details: dict | None = None) -> None:
    entry = {
        "id": gen_audit_id(),
        "object": "audit.entry",
        "ts": now_ts(),
        "kind": kind,
        "severity": severity,
        "source": source,
        "details": details or {},
    }
    state.audits.append(entry)
    state.protection["audit_log_size"] = len(state.audits)


def list_audit() -> list[dict]:
    return list(state.audits)


def export_audit() -> dict:
    file_id = gen_file_id()
    from xijian_api.stubs.files import persist
    lines = []
    for entry in state.audits:
        import json
        lines.append(json.dumps(entry, ensure_ascii=False))
    body = ("\n".join(lines) + "\n").encode("utf-8")
    persist(file_id, body, purpose="audit_export", filename=f"audit_{now_ts()}.jsonl")
    _append_audit("audit_exported", "info", source="api", details={"file_id": file_id})
    return {"file_id": file_id, "bytes": len(body)}


__all__ = [
    "seed_default", "status", "enable", "start_disable", "confirm_disable",
    "is_enabled", "guard_preview", "snapshot", "list_snapshots", "get_snapshot",
    "rollback", "list_audit", "export_audit",
]