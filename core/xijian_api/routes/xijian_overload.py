"""``/v1/xijian/overload/*`` 路由 — A5.4 系统过载保护。

过载防护**不允许用户禁用**；通过 HTTP 暴露的唯一旋钮是 tier
（``strict`` / ``medium``）。其他所有操作均为只读：状态、指标、
事件日志、恢复窗口。两个恢复确认是 POST 端点，但只有在强制
20 秒等待结束后才会成功（该值由 AC-2 固定，故意不可配置）。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import overload as ov_stub


bp = Blueprint("xijian_overload", __name__)


# ---------------------------------------------------------------------------
# 状态 / 配置（唯一面向用户的旋钮）
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/overload/status")
def overload_status():
    """返回当前监控状态、tier、恢复握手和最近样本。"""
    return jsonify(ov_stub.status())


@bp.patch("/v1/xijian/overload/tier")
def overload_tier_patch():
    """切换活动 tier。故意拒绝 ``disabled`` / ``off``。"""
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
    """返回活动 tier + 主机推荐的 tier。"""
    return jsonify(
        {
            "tier": ov_stub.current_tier(),
            "recommended_tier": ov_stub.host_recommendation(),
            "valid_tiers": list(ov_stub.VALID_TIERS),
        }
    )


# ---------------------------------------------------------------------------
# 指标 + 事件
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/overload/metrics")
def overload_metrics():
    """返回最近 ``limit`` 个滑动窗口样本（默认 60）。"""
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
    """返回最近的触发事件，新的在前。"""
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
# 恢复握手
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/overload/recovery")
def overload_recovery_state():
    """返回进行中的恢复握手状态（无则返回 404）。"""
    window = ov_stub.recovery_window()
    if not window.get("active"):
        return jsonify({"active": False})
    record = ov_stub.status().get("recovery") or {}
    return jsonify({"active": True, "window": window, "record": record})


@bp.post("/v1/xijian/overload/recovery/first-confirm")
def overload_recovery_first_confirm():
    """双重确认的第一步。"""
    result = ov_stub.first_confirm()
    if not result.get("ok"):
        if result.get("error") == "no_active_recovery":
            raise ApiError(
                404, "no active recovery", "not_found_error", code="no_active_recovery"
            )
        # ``too_early`` 以 425（Too Early）返回 — UI
        # 应遵循剩余秒数提示。
        raise ApiError(
            425,
            f"recovery wait not elapsed: {result.get('remaining_seconds')}s remaining",
            "invalid_request_error",
            code=result["error"],
        )
    return jsonify(result)


@bp.post("/v1/xijian/overload/recovery/finalize")
def overload_recovery_finalize():
    """第二步 — 关闭恢复并恢复正常运行。"""
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
    """强制清除进行中的恢复（供测试与管理员工具使用）。"""
    payload = request.get_json(silent=True) or {}
    return jsonify(ov_stub.cancel_recovery(reason=payload.get("reason")))


# ---------------------------------------------------------------------------
# 开发 / 测试辅助 — 无需等待真实滑动窗口即可推送合成样本。
# 与其他仅限开发的端点一样由 ``XIJIAN_DEV=1`` 保护，绝不会进入生产。
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
        if duration_s is not None and (
            duration_s != duration_s
            or duration_s in (float("inf"), float("-inf"))
        ):
            raise ValueError
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
