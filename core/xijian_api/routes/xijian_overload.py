"""``/v1/xijian/overload/*`` routes — A5.4 system overload protection.

The overload guard is **not user-disablable**; the only knob exposed
over HTTP is the tier (``strict`` / ``medium``).  Every other
operation is read-only: status, metrics, event log, recovery window.
The two recovery confirmations are POST endpoints but they only
succeed after the mandatory 20 s wait has elapsed (the value is
fixed by AC-2 and intentionally not configurable).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import overload as ov_stub


bp = Blueprint("xijian_overload", __name__)


# ---------------------------------------------------------------------------
# Status / config (the only user-facing knobs)
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/overload/status")
def overload_status():
    """Return the current monitor state, tier, recovery handshake, and last samples."""
    return jsonify(ov_stub.status())


@bp.patch("/v1/xijian/overload/tier")
def overload_tier_patch():
    """Switch the active tier.  ``disabled`` / ``off`` are deliberately rejected."""
    payload = request.get_json(silent=True) or {}
    tier = payload.get("tier")
    if tier is None:
        raise ApiError(
            400,
            "`tier` is required (one of: strict, medium)",
            "invalid_request_error",
            code="missing_tier",
            param="tier",
        )
    try:
        result = ov_stub.set_tier(tier)
    except ValueError as exc:
        raise ApiError(
            400,
            str(exc),
            "invalid_request_error",
            code="invalid_tier",
            param="tier",
        ) from exc
    return jsonify(result)


@bp.get("/v1/xijian/overload/tier")
def overload_tier_get():
    """Return the active tier + host-recommended tier."""
    return jsonify(
        {
            "tier": ov_stub.current_tier(),
            "recommended_tier": ov_stub.host_recommendation(),
            "valid_tiers": list(ov_stub.VALID_TIERS),
        }
    )


# ---------------------------------------------------------------------------
# Metrics + events
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/overload/metrics")
def overload_metrics():
    """Return the last ``limit`` sliding-window samples (default 60)."""
    payload = request.args or {}
    try:
        limit = int(payload.get("limit", 60))
    except ValueError as exc:
        raise ApiError(
            400, "limit must be an integer", "invalid_request_error", code="bad_limit"
        ) from exc
    if limit < 1 or limit > 600:
        raise ApiError(
            400, "limit must be in [1, 600]", "invalid_request_error", code="bad_limit"
        )
    return jsonify({"samples": ov_stub.recent_samples(limit=limit)})


@bp.get("/v1/xijian/overload/events")
def overload_events():
    """Return recent trigger events, newest first."""
    payload = request.args or {}
    try:
        limit = int(payload.get("limit", 50))
    except ValueError as exc:
        raise ApiError(
            400, "limit must be an integer", "invalid_request_error", code="bad_limit"
        ) from exc
    if limit < 1 or limit > 500:
        raise ApiError(
            400, "limit must be in [1, 500]", "invalid_request_error", code="bad_limit"
        )
    return jsonify({"events": ov_stub.list_events(limit=limit)})


# ---------------------------------------------------------------------------
# Recovery handshake
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/overload/recovery")
def overload_recovery_state():
    """Return the in-flight recovery handshake state (or 404 if none)."""
    window = ov_stub.recovery_window()
    if not window.get("active"):
        return jsonify({"active": False})
    record = ov_stub.status().get("recovery") or {}
    return jsonify({"active": True, "window": window, "record": record})


@bp.post("/v1/xijian/overload/recovery/first-confirm")
def overload_recovery_first_confirm():
    """First step of the double confirmation."""
    result = ov_stub.first_confirm()
    if not result.get("ok"):
        if result.get("error") == "no_active_recovery":
            raise ApiError(
                404, "no active recovery", "not_found_error", code="no_active_recovery"
            )
        # ``too_early`` falls through as 425 (Too Early) — the UI
        # should respect the remaining-seconds hint.
        raise ApiError(
            425,
            f"recovery wait not elapsed: {result.get('remaining_seconds')}s remaining",
            "invalid_request_error",
            code=result["error"],
        )
    return jsonify(result)


@bp.post("/v1/xijian/overload/recovery/finalize")
def overload_recovery_finalize():
    """Second step — closes the recovery and resumes normal operation."""
    result = ov_stub.finalize_recovery()
    if not result.get("ok"):
        if result.get("error") == "no_active_recovery":
            raise ApiError(
                404, "no active recovery", "not_found_error", code="no_active_recovery"
            )
        if result.get("error") == "first_confirm_required":
            raise ApiError(
                409,
                "first confirmation required before finalize",
                "invalid_request_error",
                code="first_confirm_required",
            )
        raise ApiError(
            425,
            f"recovery wait not elapsed: {result.get('remaining_seconds')}s remaining",
            "invalid_request_error",
            code=result["error"],
        )
    return jsonify(result)


@bp.post("/v1/xijian/overload/recovery/cancel")
def overload_recovery_cancel():
    """Force-clear an in-flight recovery (used by tests + admin tooling)."""
    payload = request.get_json(silent=True) or {}
    return jsonify(ov_stub.cancel_recovery(reason=payload.get("reason")))


# ---------------------------------------------------------------------------
# Dev / test helper — push synthetic samples without waiting for the
# real sliding window.  Guarded by ``XIJIAN_DEV=1`` like the other
# dev-only endpoints, so it never ships in production.
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/_test/overload/simulate")
def overload_simulate():
    import os as _os

    if _os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(404, "not found", "not_found_error", code="route_not_found")
    payload = request.get_json(silent=True) or {}
    metric = payload.get("metric", ov_stub.METRIC_CPU)
    try:
        duration_s = float(payload.get("duration_s", 0.0)) or None
    except ValueError as exc:
        raise ApiError(
            400, "duration_s must be a number", "invalid_request_error", code="bad_duration"
        ) from exc
    try:
        result = ov_stub.simulate_overload(metric, duration_s=duration_s)
    except ValueError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="bad_metric", param="metric"
        ) from exc
    return jsonify(result)


__all__ = ["bp"]
