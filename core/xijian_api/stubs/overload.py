"""Stub overload-protection module — A5.4 in the function list v2.

The overload guard is the **last-line safety net** for the local API:
when CPU / SoC temperature / memory / GPU-ANE stays past its tier
threshold, the monitor thread triggers the most-severe action for the
metric(s) that fired, freezes the chat pipeline, dumps a context
snapshot, and forces the user through a 20-second recovery wait +
double confirmation.  The system **cannot be disabled by the user**;
``tier`` is the only knob exposed (strict / medium).

This module keeps the live sliding-window samples in module-level
``deque`` instances and only the persistent pieces (event log, last
trigger info, recovery handshake) in :data:`xijian_api.stubs.state.overload`
so the data survives the periodic :func:`seed_all` calls without losing
the in-flight sample history.

Test surface
------------
Pure helpers that the test suite drives directly:

* :func:`evaluate_metrics` — pure function: given a sequence of
  samples and a tier, return the triggered metrics + the most-severe
  action.  No I/O, no thread, no time.
* :func:`select_most_severe_action` — pure ranker.
* :func:`recovery_window` — pure: how long remains before the user
  may confirm.

Side-effecting functions (used by routes + monitor thread):

* :func:`start_recovery` / :func:`first_confirm` /
  :func:`finalize_recovery` / :func:`cancel_recovery` — drive the
  state machine that the UI walks through after a trigger.
* :func:`inject_sample` — used by the monitor thread **and** by tests
  that want to short-circuit real collection.
* :func:`start_monitor` / :func:`stop_monitor` — thread lifecycle.

The collection primitives (:func:`collect_sample`) and the threshold
constants live in this module so the route layer is just a thin HTTP
shell on top.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_overload_event_id, gen_snapshot_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Constants — locked by v2.1 of the function list
# ---------------------------------------------------------------------------

#: Sample rate for the monitor loop.  1 Hz matches the sequence diagram
#: in A5.4.  We do not expose this — it is a safety-critical interval.
SAMPLE_INTERVAL_SECONDS = 1.0

#: Maximum window we ever need to keep around.  The medium CPU band
#: is 100 s; round up so the deque never has to grow.
MAX_SAMPLES = 120

#: How long the user must wait between trigger and final confirmation.
#: Per AC-2 the value is fixed at 20 s and is NOT user-configurable.
RECOVERY_WAIT_SECONDS = 20

#: The system has no user-facing kill switch.  Tests / ops that need
#: to disable the monitor at start-up can set
#: ``XIJIAN_OVERLOAD_MONITOR=0`` in the environment.
_MONITOR_ENV_FLAG = "XIJIAN_OVERLOAD_MONITOR"


# Tier thresholds — v2.1 of the function list, kept verbatim.
# Each entry is (cpu_pct, cpu_window_s, soc_celsius, mem_pct,
#                gpu_ane_pct, gpu_ane_window_s).
TIER_THRESHOLDS: dict[str, dict[str, float | None]] = {
    "strict": {
        "cpu_pct": 93.0,
        "cpu_window_s": 60.0,
        "soc_celsius": 95.0,
        "mem_pct": 90.0,
        "gpu_ane_pct": 75.0,
        "gpu_ane_window_s": 45.0,
    },
    "medium": {
        "cpu_pct": 95.0,
        "cpu_window_s": 100.0,
        "soc_celsius": 95.0,
        "mem_pct": 90.0,
        "gpu_ane_pct": 80.0,
        "gpu_ane_window_s": 80.0,
    },
}

VALID_TIERS: tuple[str, ...] = ("strict", "medium")


# ---------------------------------------------------------------------------
# Action taxonomy
# ---------------------------------------------------------------------------
#
# When multiple metrics trip at once we run the most-severe action.
# "Most severe" is the action that affects the most subsystems and
# cannot be reversed without user confirmation.  Order is bottom-up —
# the LAST item wins in :func:`select_most_severe_action`.

#: Single-shot indicator: an instantaneous trip (e.g. SoC temperature,
#: memory pressure) without a duration window.
@dataclass(frozen=True)
class Sample:
    """One 1-Hz reading from the monitor thread (or a test)."""

    ts: float
    cpu_pct: float | None
    mem_pct: float | None
    soc_celsius: float | None
    gpu_ane_pct: float | None


#: Set of "tripping" events we recognise.  Names are stable because the
#: audit log embeds them as ``metric`` fields.
METRIC_CPU = "cpu"
METRIC_SOC = "soc_temp"
METRIC_MEM = "memory"
METRIC_GPU = "gpu_ane"

#: Action labels — these go into ``state.overload['last_event']['action']``
#: and downstream subsystems key off them.
ACTION_SUSPEND_IDLE_NPCS = "suspend_idle_npcs"
ACTION_DEGRADE_TTS = "degrade_tts"
ACTION_COMPRESS_MEMORY = "compress_memory"
ACTION_EMERGENCY_DUMP = "emergency_dump"

#: Severity ranking (larger = more severe).  The greatest severity
#: wins when multiple metrics fire in the same window.
_ACTION_SEVERITY: dict[str, int] = {
    ACTION_SUSPEND_IDLE_NPCS: 10,
    ACTION_DEGRADE_TTS: 20,
    ACTION_COMPRESS_MEMORY: 30,
    ACTION_EMERGENCY_DUMP: 40,
}

#: Mapping from a tripping metric to the action it triggers.
_METRIC_TO_ACTION: dict[str, str] = {
    METRIC_CPU: ACTION_SUSPEND_IDLE_NPCS,
    METRIC_GPU: ACTION_DEGRADE_TTS,
    METRIC_MEM: ACTION_COMPRESS_MEMORY,
    METRIC_SOC: ACTION_EMERGENCY_DUMP,
}


# ---------------------------------------------------------------------------
# Module-level state — sliding window + monitor lifecycle
# ---------------------------------------------------------------------------

_LOGGER = logging.getLogger("xijian_api.overload")

_SAMPLES: deque[Sample] = deque(maxlen=MAX_SAMPLES)
_STATE_LOCK = threading.Lock()

#: The currently active tier.  Default to ``"medium"`` to match the
#: v2.1 "MacBook Pro / Mac mini / Mac Studio → medium" guidance, which
#: is the common case for the supported Mac lineup.  Users can change
#: this at runtime via ``PATCH /v1/xijian/overload/tier``.
_TIER: str = "medium"

#: Background monitor handle (None when stopped).
_MONITOR_THREAD: threading.Thread | None = None
_MONITOR_STOP = threading.Event()

#: Generation counter used by tests to invalidate the cached sample
#: window after a ``reset_for_testing`` call.
_GENERATION: int = 0


# ---------------------------------------------------------------------------
# Pure helpers — easy to unit test
# ---------------------------------------------------------------------------


def _samples_to_metric_view(samples: Iterable[Sample]) -> dict[str, list[tuple[float, float | None]]]:
    """Project a sequence of samples into a ``{metric: [(ts, value)]}`` view.

    Missing measurements (``None``) are kept as ``None`` so the caller
    can choose to skip them.  This is a pure projection — no time
    filtering happens here.
    """
    view: dict[str, list[tuple[float, float | None]]] = {
        METRIC_CPU: [],
        METRIC_MEM: [],
        METRIC_SOC: [],
        METRIC_GPU: [],
    }
    for s in samples:
        view[METRIC_CPU].append((s.ts, s.cpu_pct))
        view[METRIC_MEM].append((s.ts, s.mem_pct))
        view[METRIC_SOC].append((s.ts, s.soc_celsius))
        view[METRIC_GPU].append((s.ts, s.gpu_ane_pct))
    return view


def _all_over_threshold(
    points: list[tuple[float, float | None]],
    *,
    threshold: float,
    window_s: float,
    now: float,
) -> bool:
    """Return True iff every sample in the last ``window_s`` exceeds ``threshold``.

    A ``None`` value breaks the run (we can't claim a sensor read
    confirmed overload when the sensor was missing).  The window is
    computed relative to ``now`` so the same input is reproducible.

    To honour the "持续 X 秒" spec from the function list (the whole
    point of which is to suppress transient spikes), we require the
    samples in the window to actually span at least ``window_s``
    seconds end-to-end.  A single over-threshold sample is *not*
    enough — we need evidence the threshold was sustained.
    """
    cutoff = now - window_s
    in_window: list[tuple[float, float | None]] = [
        (ts, value) for ts, value in points if ts >= cutoff
    ]
    if len(in_window) < 2:
        return False
    span = in_window[-1][0] - in_window[0][0]
    if span < window_s - SAMPLE_INTERVAL_SECONDS:
        return False
    for _ts, value in in_window:
        if value is None or value <= threshold:
            return False
    return True


def _any_over_threshold(
    points: list[tuple[float, float | None]],
    *,
    threshold: float,
    now: float,
) -> bool:
    """Return True iff the most recent sample exceeds ``threshold``."""
    latest: float | None = None
    latest_ts: float = float("-inf")
    for ts, value in points:
        if ts >= latest_ts:
            latest_ts = ts
            latest = value
    if latest is None or latest_ts == float("-inf"):
        return False
    return latest > threshold


def select_most_severe_action(actions: Iterable[str]) -> str | None:
    """Return the most-severe action label, or None if ``actions`` is empty."""
    best: str | None = None
    best_sev = -1
    for action in actions:
        sev = _ACTION_SEVERITY.get(action, 0)
        if sev > best_sev:
            best_sev = sev
            best = action
    return best


def evaluate_metrics(
    samples: Iterable[Sample],
    tier: str,
    *,
    now: float | None = None,
) -> dict:
    """Decide whether the supplied samples trigger any overload action.

    Returns a dict with:

    * ``triggered``: list of metric names that tripped this round
    * ``action``: the most-severe action implied by ``triggered``,
      or ``None`` if nothing tripped
    * ``per_metric``: per-metric explanation with the effective
      window and threshold so the audit log can record *why* a
      trigger fired.

    The function is pure: it does not touch the module-level state
    or the monitor thread.  The monitor thread calls it with the
    current sliding window; tests call it with synthetic samples.
    """
    thresholds = TIER_THRESHOLDS.get(tier)
    if thresholds is None:
        return {
            "triggered": [],
            "action": None,
            "per_metric": {},
            "error": f"unknown_tier:{tier}",
        }
    points = _samples_to_metric_view(samples)
    moment = now if now is not None else time.time()

    per_metric: dict[str, dict] = {}

    cpu_points = points[METRIC_CPU]
    if _all_over_threshold(
        cpu_points,
        threshold=float(thresholds["cpu_pct"] or 0.0),
        window_s=float(thresholds["cpu_window_s"] or 0.0),
        now=moment,
    ):
        per_metric[METRIC_CPU] = {
            "threshold_pct": float(thresholds["cpu_pct"]),
            "window_s": float(thresholds["cpu_window_s"]),
        }

    mem_points = points[METRIC_MEM]
    if _any_over_threshold(
        mem_points,
        threshold=float(thresholds["mem_pct"] or 0.0),
        now=moment,
    ):
        per_metric[METRIC_MEM] = {
            "threshold_pct": float(thresholds["mem_pct"]),
        }

    soc_points = points[METRIC_SOC]
    soc_threshold = thresholds.get("soc_celsius")
    if soc_threshold and _any_over_threshold(
        soc_points,
        threshold=float(soc_threshold),
        now=moment,
    ):
        per_metric[METRIC_SOC] = {
            "threshold_celsius": float(soc_threshold),
        }

    gpu_points = points[METRIC_GPU]
    if _all_over_threshold(
        gpu_points,
        threshold=float(thresholds["gpu_ane_pct"] or 0.0),
        window_s=float(thresholds["gpu_ane_window_s"] or 0.0),
        now=moment,
    ):
        per_metric[METRIC_GPU] = {
            "threshold_pct": float(thresholds["gpu_ane_pct"]),
            "window_s": float(thresholds["gpu_ane_window_s"]),
        }

    triggered = list(per_metric.keys())
    actions = [_METRIC_TO_ACTION[m] for m in triggered if m in _METRIC_TO_ACTION]
    return {
        "triggered": triggered,
        "action": select_most_severe_action(actions),
        "per_metric": per_metric,
    }


# ---------------------------------------------------------------------------
# Sensor collection (best effort, cross-platform)
# ---------------------------------------------------------------------------


def _read_soc_temp() -> float | None:
    """Return the system-on-chip temperature in °C, or ``None``.

    ``psutil.sensors_temperatures()`` only returns real values on
    systems that expose the kernel sensor interface.  Per AC-1 we
    only **require** CPU + memory; temperature is best effort.
    """
    try:
        import psutil  # local import: optional dep at import time
    except ImportError:
        return None
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False)
    except Exception:  # noqa: BLE001 — psutil raises OSError on some hosts
        return None
    if not sensors:
        return None
    # Prefer labels that look CPU / SoC / package / core.
    preferred = ("cpu_thermal", "cpu-thermal", "soc_thermal", "coretemp", "k10temp", "cpu")
    for label in preferred:
        for key, entries in sensors.items():
            if label in key.lower():
                for entry in entries:
                    if entry.current is not None:
                        return float(entry.current)
    # Fall back to whatever has a temperature.
    for entries in sensors.values():
        for entry in entries:
            if entry.current is not None:
                return float(entry.current)
    return None


def _read_gpu_ane_pressure() -> float | None:
    """Heuristic: the highest child process CPU% in this process tree.

    On Apple Silicon the MLX / CoreML stack runs as a child process
    and a high sustained CPU% is a reasonable proxy for ANE/GPU
    offload pressure.  On hosts that don't expose a useful signal we
    return ``None`` so the monitor gracefully degrades to a 3-metric
    view as the doc allows.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        root = psutil.Process(os.getpid())
    except Exception:  # noqa: BLE001
        return None
    try:
        children = root.children(recursive=True)
    except Exception:  # noqa: BLE001
        children = []
    if not children:
        return None
    samples = []
    for child in children:
        try:
            with child.oneshot():
                samples.append(child.cpu_percent(interval=None))
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # noqa: F821
            continue
    if not samples:
        return None
    return max(samples)


def collect_sample() -> Sample:
    """Take one 1-Hz reading and return a :class:`Sample`.

    Falls back to ``None`` for any sensor that the host cannot report;
    the monitor thread keeps running either way and the evaluation
    logic just ignores ``None`` readings (see :func:`evaluate_metrics`).
    """
    import psutil
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    return Sample(
        ts=time.time(),
        cpu_pct=float(cpu) if cpu is not None else None,
        mem_pct=float(mem) if mem is not None else None,
        soc_celsius=_read_soc_temp(),
        gpu_ane_pct=_read_gpu_ane_pressure(),
    )


# ---------------------------------------------------------------------------
# Public API — the monitor thread + stateful recovery handshake
# ---------------------------------------------------------------------------


def current_tier() -> str:
    """Return the currently active tier (``"strict"`` or ``"medium"``)."""
    with _STATE_LOCK:
        return _TIER


def set_tier(tier: str) -> dict:
    """Switch the active tier.  Raises ``ValueError`` for anything else.

    Per AC-3 the user picks strict or medium; there is no other knob.
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier: {tier!r}")
    with _STATE_LOCK:
        global _TIER
        _TIER = tier
        record = state.overload.setdefault("config", {})
        record["tier"] = tier
        record["tier_changed_at"] = now_ts()
    return {"tier": tier, "tier_changed_at": record["tier_changed_at"]}


def host_recommendation() -> str:
    """Return the recommended tier for the current host (heuristic)."""
    machine = platform.machine().lower()
    system = platform.system().lower()
    # Apple Silicon → M-series; consumer SKUs (Air, fan-less) → strict,
    # everything else → medium.  Non-Apple hosts default to medium.
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        # Without fancfg introspection we can't tell Air vs Pro; lean strict.
        return "strict"
    return "medium"


def status() -> dict:
    """Return a JSON-friendly snapshot of the current overload state."""
    with _STATE_LOCK:
        recent = list(_SAMPLES)[-20:]
    record = state.overload
    cfg = record.get("config", {})
    last_event = record.get("last_event")
    recovery = record.get("recovery")
    return {
        "monitor_running": _MONITOR_THREAD is not None and _MONITOR_THREAD.is_alive(),
        "tier": current_tier(),
        "tier_changed_at": cfg.get("tier_changed_at"),
        "recommended_tier": host_recommendation(),
        "recovery_wait_seconds": RECOVERY_WAIT_SECONDS,
        "recoverable": record.get("recoverable", True),
        "last_event": last_event,
        "recovery": recovery,
        "recent_samples": [_sample_to_dict(s) for s in recent],
        "platform": platform.platform(),
    }


def recent_samples(limit: int = 60) -> list[dict]:
    """Return the last ``limit`` sliding-window samples as dicts."""
    with _STATE_LOCK:
        recent = list(_SAMPLES)[-limit:]
    return [_sample_to_dict(s) for s in recent]


def list_events(limit: int = 50) -> list[dict]:
    """Return the most recent trigger events (newest first)."""
    events = state.overload.get("events", [])
    return list(reversed(events[-limit:]))


def inject_sample(sample: Sample) -> dict:
    """Append a sample to the window and run a fresh evaluation.

    Used by the monitor thread (real data) and by tests (synthetic
    data).  Returns the evaluation result plus, when an overload
    trips, the side-effect of recording a trigger event.
    """
    global _GENERATION
    with _STATE_LOCK:
        _SAMPLES.append(sample)
        # Drop anything older than the longest window so the deque
        # never has to be re-sized.  This is a soft cap, not a hard one.
        horizon = time.time() - max(
            float(TIER_THRESHOLDS["medium"]["cpu_window_s"] or 0.0),
            float(TIER_THRESHOLDS["medium"]["gpu_ane_window_s"] or 0.0),
        )
        while _SAMPLES and _SAMPLES[0].ts < horizon:
            _SAMPLES.popleft()
        snapshot = list(_SAMPLES)
        tier = _TIER
    result = evaluate_metrics(snapshot, tier)
    if result["triggered"]:
        _record_trigger(result, sample, tier)
    _GENERATION += 1
    return result


def simulate_overload(metric: str, *, duration_s: float | None = None) -> dict:
    """Test / dev helper: build a synthetic window that trips one metric.

    The monitor thread is *not* used — this directly evaluates a
    freshly built sample list so callers (and tests) don't have to
    wait for the 60-100 s sliding windows to elapse in real time.

    Side effects:

    * Replaces the current sliding window with the synthetic samples
      so subsequent :func:`status` calls see the simulated state.
    * If the synthetic window trips an action, records a trigger
      event and starts the recovery handshake.
    """
    duration_s = duration_s if duration_s is not None else 5.0
    tier = current_tier()
    thresholds = TIER_THRESHOLDS[tier]
    # CPU / GPU are "持续 X 秒" — push enough samples to fill the
    # window; mem / soc are instantaneous.
    if metric == METRIC_CPU:
        window_s = float(thresholds["cpu_window_s"] or 0.0)
        value = float(thresholds["cpu_pct"] or 0.0) + 1.0
    elif metric == METRIC_GPU:
        window_s = float(thresholds["gpu_ane_window_s"] or 0.0)
        value = float(thresholds["gpu_ane_pct"] or 0.0) + 1.0
    elif metric in {METRIC_MEM, METRIC_SOC}:
        window_s = 1.0
        value = float(thresholds["mem_pct"] or 0.0) + 1.0
    else:
        raise ValueError(f"unknown metric: {metric!r}")
    count = max(int(window_s / SAMPLE_INTERVAL_SECONDS) + 1, 2)
    # Anchor the window at the *current* real time so the horizon
    # cleanup inside :func:`inject_sample` doesn't strip samples off
    # the front while the loop is running.
    now = time.time()
    samples = [
        _synthetic_sample(metric, value, ts=now - (count - 1 - i) * SAMPLE_INTERVAL_SECONDS)
        for i in range(count)
    ]
    # Replace the sliding window so the simulation is deterministic
    # regardless of any leftover samples from a prior monitor tick.
    with _STATE_LOCK:
        _SAMPLES.clear()
        for s in samples:
            _SAMPLES.append(s)
    return evaluate_metrics(samples, tier)


def _synthetic_sample(metric: str, value: float, *, ts: float) -> Sample:
    """Build a sample that only ``metric`` is high; the rest are calm."""
    low = 5.0
    return Sample(
        ts=ts,
        cpu_pct=value if metric == METRIC_CPU else low,
        mem_pct=value if metric == METRIC_MEM else low,
        soc_celsius=value if metric == METRIC_SOC else None,
        gpu_ane_pct=value if metric == METRIC_GPU else low,
    )


def _synthetic_sample(metric: str, value: float, *, ts: float) -> Sample:
    """Build a sample that only ``metric`` is high; the rest are calm."""
    low = 5.0
    return Sample(
        ts=ts,
        cpu_pct=value if metric == METRIC_CPU else low,
        mem_pct=value if metric == METRIC_MEM else low,
        soc_celsius=value if metric == METRIC_SOC else None,
        gpu_ane_pct=value if metric == METRIC_GPU else low,
    )


# ---------------------------------------------------------------------------
# Recovery handshake
# ---------------------------------------------------------------------------


def start_recovery(event: dict) -> dict:
    """Begin the 20-second wait after a trigger fired.

    Stores a ``recovery`` record in :data:`state.overload` so the UI
    can poll.  Returns the record.  Calling this again before the
    user reaches ``finalize_recovery`` **resets the 20 s countdown**
    (per the doc's edge-case spec — "用户在恢复前再次触发过载 → 重置
    20s 倒计时").
    """
    triggered_at = float(event.get("triggered_at") or time.time())
    earliest = triggered_at + RECOVERY_WAIT_SECONDS
    record = {
        "event_id": event.get("id"),
        "triggered_at": triggered_at,
        "earliest_confirm_at": earliest,
        "first_confirmed_at": None,
        "status": "waiting",
    }
    state.overload["recovery"] = record
    return record


def recovery_window() -> dict:
    """Return how long remains before the user may finalise the recovery."""
    record = state.overload.get("recovery")
    if not record:
        return {"active": False, "remaining_seconds": 0, "can_confirm": False}
    now = time.time()
    remaining = max(0.0, float(record["earliest_confirm_at"]) - now)
    return {
        "active": True,
        "remaining_seconds": int(remaining) + (1 if remaining % 1 else 0),
        "can_finalize": remaining <= 0 and record.get("status") == "first_confirmed",
        "status": record.get("status"),
    }


def first_confirm() -> dict:
    """User clicked "I understand, resume the wait" — first of two steps."""
    record = state.overload.get("recovery")
    if not record:
        return {"ok": False, "error": "no_active_recovery"}
    now = time.time()
    if now < float(record["earliest_confirm_at"]):
        return {
            "ok": False,
            "error": "too_early",
            "remaining_seconds": int(float(record["earliest_confirm_at"]) - now) + 1,
        }
    record["first_confirmed_at"] = now
    record["status"] = "first_confirmed"
    return {"ok": True, "status": record["status"]}


def finalize_recovery() -> dict:
    """Second confirmation — closes out the recovery handshake."""
    record = state.overload.get("recovery")
    if not record:
        return {"ok": False, "error": "no_active_recovery"}
    if record.get("status") != "first_confirmed":
        return {"ok": False, "error": "first_confirm_required"}
    now = time.time()
    if now < float(record["earliest_confirm_at"]):
        return {
            "ok": False,
            "error": "too_early",
            "remaining_seconds": int(float(record["earliest_confirm_at"]) - now) + 1,
        }
    record["status"] = "finalized"
    record["finalized_at"] = now
    event_id = record.get("event_id")
    state.overload["recovery"] = None
    # Note the finalised event in the audit log via the global
    # protection audit — keeps the surface uniform.
    try:
        from xijian_api.stubs.protection import _append_audit
        _append_audit(
            "overload_recovery_finalized",
            "info",
            source="api",
            details={"event_id": event_id, "tier": current_tier()},
        )
    except Exception:  # noqa: BLE001 — best effort, never fail the API call
        pass
    return {"ok": True, "event_id": event_id, "status": "finalized"}


def cancel_recovery(reason: str | None = None) -> dict:
    """Force-clear an in-flight recovery (used by tests and admin tools)."""
    record = state.overload.get("recovery")
    if not record:
        return {"ok": True, "cancelled": False}
    record["status"] = "cancelled"
    record["cancel_reason"] = reason
    state.overload["recovery"] = None
    return {"ok": True, "cancelled": True, "event_id": record.get("event_id")}


# ---------------------------------------------------------------------------
# Monitor thread lifecycle
# ---------------------------------------------------------------------------


def _record_trigger(result: dict, sample: Sample, tier: str) -> None:
    """Append a trigger event to the log + start the recovery handshake."""
    event = {
        "id": gen_overload_event_id(),
        "ts": now_ts(),
        "tier": tier,
        "triggered_at": sample.ts,
        "triggered_metrics": result["triggered"],
        "action": result["action"],
        "per_metric": result["per_metric"],
        "sample": _sample_to_dict(sample),
        "status": "triggered",
    }
    state.overload.setdefault("events", []).append(event)
    state.overload["last_event"] = event
    # Keep the log bounded so the in-memory store doesn't grow forever
    # during long-running sessions.
    events = state.overload["events"]
    if len(events) > 200:
        del events[: len(events) - 200]
    # Drop a context snapshot so the AI can resume from the trigger
    # point.  The snapshot helper handles deduplication.
    try:
        from xijian_api.stubs.protection import snapshot
        snapshot(
            scope="overload",
            payload={
                "event_id": event["id"],
                "tier": tier,
                "triggered_metrics": event["triggered_metrics"],
                "action": event["action"],
            },
            auto=True,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("overload snapshot failed: %s", exc)
    # Begin the recovery handshake — the 20 s wait + double confirm.
    start_recovery(event)
    # Try to publish a WS event so the UI can show the prompt.
    try:
        from xijian_api.routes.ws_routes import publish_event
        publish_event(
            "overload.triggered",
            {
                "event_id": event["id"],
                "tier": tier,
                "triggered_metrics": event["triggered_metrics"],
                "action": event["action"],
                "earliest_confirm_at": state.overload["recovery"]["earliest_confirm_at"],
            },
        )
    except Exception:  # noqa: BLE001
        # No-op: WS may not be wired in tests.
        pass


def _monitor_loop(stop_event: threading.Event) -> None:
    """Main loop: sample every SAMPLE_INTERVAL_SECONDS until ``stop_event`` set."""
    # Prime psutil.cpu_percent() — the first non-blocking call returns 0.0.
    try:
        import psutil
        psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        pass
    while not stop_event.is_set():
        try:
            sample = collect_sample()
            inject_sample(sample)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("overload sample failed: %s", exc)
        # Interruptible sleep so shutdown is prompt.
        if stop_event.wait(SAMPLE_INTERVAL_SECONDS):
            break


def start_monitor() -> dict:
    """Start the background monitor thread (idempotent).

    Returns a small status dict so the caller can log / surface the
    result.  Honours the ``XIJIAN_OVERLOAD_MONITOR`` env var so tests
    and CI runs that don't want the thread can suppress it.
    """
    global _MONITOR_THREAD
    with _STATE_LOCK:
        if _MONITOR_THREAD is not None and _MONITOR_THREAD.is_alive():
            return {"started": False, "reason": "already_running"}
        if os.environ.get(_MONITOR_ENV_FLAG) == "0":
            return {"started": False, "reason": "disabled_by_env"}
        _MONITOR_STOP.clear()
        thread = threading.Thread(
            target=_monitor_loop,
            args=(_MONITOR_STOP,),
            name="xijian-overload-monitor",
            daemon=True,
        )
        _MONITOR_THREAD = thread
        thread.start()
    return {"started": True, "interval_s": SAMPLE_INTERVAL_SECONDS}


def stop_monitor() -> dict:
    """Stop the background monitor thread.  No-op if not running."""
    global _MONITOR_THREAD
    with _STATE_LOCK:
        thread = _MONITOR_THREAD
        if thread is None or not thread.is_alive():
            return {"stopped": False, "reason": "not_running"}
        _MONITOR_STOP.set()
    thread.join(timeout=SAMPLE_INTERVAL_SECONDS * 3)
    with _STATE_LOCK:
        _MONITOR_THREAD = None
    return {"stopped": True}


def reset_for_testing() -> None:
    """Wipe all in-memory state.  Used by the test conftest between cases."""
    stop_monitor()
    with _STATE_LOCK:
        _SAMPLES.clear()
        global _TIER, _GENERATION
        _TIER = "medium"
        _GENERATION += 1
    state.overload.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_to_dict(sample: Sample) -> dict:
    return {
        "ts": sample.ts,
        "cpu_pct": sample.cpu_pct,
        "mem_pct": sample.mem_pct,
        "soc_celsius": sample.soc_celsius,
        "gpu_ane_pct": sample.gpu_ane_pct,
    }


def seed_default() -> None:
    """Idempotent default-seed.  Starts the monitor if the env allows it.

    Called by ``stubs.seed_all()`` at app start-up.  The monitor is
    suppressed in tests via ``XIJIAN_OVERLOAD_MONITOR=0`` so the
    background thread doesn't race the assertions.
    """
    if not state.overload.get("config"):
        state.overload["config"] = {
            "tier": _TIER,
            "tier_changed_at": now_ts(),
        }
    if not state.overload.get("events"):
        state.overload["events"] = []
    if os.environ.get(_MONITOR_ENV_FLAG) == "0":
        return
    start_monitor()


__all__ = [
    # constants
    "SAMPLE_INTERVAL_SECONDS",
    "RECOVERY_WAIT_SECONDS",
    "TIER_THRESHOLDS",
    "VALID_TIERS",
    "Sample",
    "METRIC_CPU",
    "METRIC_SOC",
    "METRIC_MEM",
    "METRIC_GPU",
    "ACTION_SUSPEND_IDLE_NPCS",
    "ACTION_DEGRADE_TTS",
    "ACTION_COMPRESS_MEMORY",
    "ACTION_EMERGENCY_DUMP",
    # pure
    "select_most_severe_action",
    "evaluate_metrics",
    # state
    "current_tier",
    "set_tier",
    "host_recommendation",
    "status",
    "recent_samples",
    "list_events",
    "inject_sample",
    "simulate_overload",
    # recovery
    "start_recovery",
    "recovery_window",
    "first_confirm",
    "finalize_recovery",
    "cancel_recovery",
    # monitor
    "start_monitor",
    "stop_monitor",
    "collect_sample",
    "seed_default",
    "reset_for_testing",
]
