"""TTS degradation guard — A5.4 ``degrade_tts`` action consumer.

The A5.4 overload protection defines four subsystem actions; this
module owns the **TTS degradation flag**.  When the overload monitor
trips a metric whose most-severe action is ``degrade_tts`` (GPU/ANE
pressure per the v2.1 threshold table), the registered handler sets a
process-wide flag.  The TTS stack (:mod:`xijian_api.stubs.audio` and
the ``ai/backends/*/tts.py`` backends) can read :func:`is_degraded`
to switch to a lower-quality / lower-latency synthesis path.

Why a flag module and not a backend change
==========================================

The TTS backends (mlx / gguf / openai) are configured via
``config.backends.tts`` and are intentionally backend-agnostic about
*safety* state.  Keeping the degradation signal in a tiny stub module
mirrors the overload registry's decoupling philosophy: the overload
module never names its consumers, and the consumers (npcs, memory,
snapshots, this module) wire themselves in via
:func:`install_overload_handler`.

The flag is intentionally *not* auto-reset — overload recovery is a
user-confirmed handshake (A5.4 AC-3 double confirmation), so the TTS
stack stays degraded until :func:`clear_degraded` is called by the
finalise path or an operator.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

_LOGGER = logging.getLogger("xijian_api.tts_guard")

#: Process-wide degradation state.  Guarded by ``_LOCK``.
_DEGRADED = False
_REASON: str | None = None

_LOCK = threading.Lock()

#: Guard against duplicate handler registration across repeated
#: ``seed_all`` calls (the overload registry is cleared by
#: ``overload.reset_for_testing`` between tests, so the conftest
#: re-installs this after each reset — mirroring the npcs pattern).
_INSTALLED = False


def is_degraded() -> bool:
    """Return True when the TTS stack should run degraded."""
    with _LOCK:
        return _DEGRADED


def degradation() -> dict:
    """Return the current degradation state (flag + reason)."""
    with _LOCK:
        return {"degraded": _DEGRADED, "reason": _REASON}


def set_degraded(reason: str | None = None) -> dict:
    """Flip the degradation flag on (idempotent)."""
    global _DEGRADED, _REASON
    with _LOCK:
        _DEGRADED = True
        if reason:
            _REASON = str(reason)
    _LOGGER.warning("TTS degradation flag set (reason=%s)", reason)
    return {"degraded": True, "reason": _REASON}


def clear_degraded() -> dict:
    """Flip the degradation flag off (called after overload recovery)."""
    global _DEGRADED, _REASON
    with _LOCK:
        _DEGRADED = False
        _REASON = None
    _LOGGER.info("TTS degradation flag cleared")
    return {"degraded": False, "reason": None}


# ---------------------------------------------------------------------------
# A5.4 cross-link
# ---------------------------------------------------------------------------


def _degrade_for_overload(event: dict) -> None:
    """A5.4 ``degrade_tts`` action consumer: set the degradation flag."""
    set_degraded(reason=f"overload:{event.get('id')}")


def install_overload_handler() -> dict:
    """Register the A5.4 ``degrade_tts`` action handler (guarded)."""
    global _INSTALLED
    if _INSTALLED:
        return {"action": "degrade_tts", "installed": True, "already": True}
    from xijian_api.stubs.overload import (
        ACTION_DEGRADE_TTS,
        register_action_handler,
    )
    register_action_handler(ACTION_DEGRADE_TTS, _degrade_for_overload)
    _INSTALLED = True
    return {"action": ACTION_DEGRADE_TTS, "installed": True}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed: wire the overload handler."""
    install_overload_handler()


def reset_for_testing() -> None:
    """Clear the flag + allow re-registration (called between tests)."""
    global _DEGRADED, _REASON, _INSTALLED
    with _LOCK:
        _DEGRADED = False
        _REASON = None
        _INSTALLED = False


__all__ = [
    "is_degraded",
    "degradation",
    "set_degraded",
    "clear_degraded",
    "install_overload_handler",
    "seed_default",
    "reset_for_testing",
]
