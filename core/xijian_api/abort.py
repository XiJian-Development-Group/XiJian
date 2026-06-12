"""Process-wide :class:`AbortSignal` registry.

The chat (and other long-running) routes register an :class:`AbortSignal`
keyed by ``request_id`` when they start.  A separate ``POST .../abort``
request triggers the signal so the streaming generator can stop early
and return ``finish_reason="abort"``.

This is intentionally minimal — a thread-safe ``dict`` guarded by a
``threading.Lock`` plus a small :class:`AbortSignal` class wrapping a
:class:`threading.Event`.
"""

from __future__ import annotations

import threading
from typing import Optional

from xijian_api.errors import GenerationAborted

# Module-level state (DESIGN §9.1).
_REGISTRY: dict[str, "AbortSignal"] = {}
_LOCK = threading.Lock()


class AbortSignal:
    """Cooperative cancellation primitive backed by a :class:`threading.Event`.

    Streaming generators call :meth:`raise_if_aborted` between chunk
    emissions; once :meth:`set` has been called the next call raises
    :class:`GenerationAborted` which is caught by the Flask error
    handler.
    """

    __slots__ = ("_ev",)

    def __init__(self) -> None:
        self._ev = threading.Event()

    def set(self) -> None:
        """Mark the signal as aborted."""
        self._ev.set()

    def is_set(self) -> bool:
        """Return ``True`` if :meth:`set` has been called."""
        return self._ev.is_set()

    def raise_if_aborted(self) -> None:
        """Raise :class:`GenerationAborted` if the signal has been set."""
        if self._ev.is_set():
            raise GenerationAborted("aborted by client")

    def reset(self) -> None:
        """Clear the signal so the same instance can be reused."""
        self._ev.clear()


def register(request_id: str) -> AbortSignal:
    """Register (or fetch) the :class:`AbortSignal` for ``request_id``."""
    with _LOCK:
        signal = _REGISTRY.get(request_id)
        if signal is None:
            signal = AbortSignal()
            _REGISTRY[request_id] = signal
        return signal


def get(request_id: str) -> Optional[AbortSignal]:
    """Return the registered signal for ``request_id`` or ``None``."""
    with _LOCK:
        return _REGISTRY.get(request_id)


def abort(request_id: str) -> bool:
    """Trigger the abort for ``request_id``.

    Returns ``True`` if a signal existed (and was set), ``False``
    otherwise.
    """
    with _LOCK:
        signal = _REGISTRY.get(request_id)
        if signal is None:
            return False
        signal.set()
        return True


def cleanup(request_id: str) -> None:
    """Remove the entry for ``request_id`` from the registry."""
    with _LOCK:
        _REGISTRY.pop(request_id, None)


def reset_for_testing() -> None:
    """Clear the entire registry (used by tests)."""
    with _LOCK:
        _REGISTRY.clear()


__all__ = [
    "AbortSignal",
    "register",
    "get",
    "abort",
    "cleanup",
    "reset_for_testing",
]