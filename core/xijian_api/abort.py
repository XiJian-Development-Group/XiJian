"""Process-wide :class:`AbortSignal` registry.

进程级 :class:`AbortSignal` 注册表。

The chat (and other long-running) routes register an :class:`AbortSignal`
keyed by ``request_id`` when they start.  A separate ``POST .../abort``
request triggers the signal so the streaming generator can stop early
and return ``finish_reason="abort"``.

聊天（及其他长时间运行的）路由在启动时注册一个以 ``request_id`` 为键的 :class:`AbortSignal`。
单独的 ``POST .../abort`` 请求触发该信号，使流式生成器可以提前停止
并返回 ``finish_reason="abort"``。

This is intentionally minimal — a thread-safe ``dict`` guarded by a
``threading.Lock`` plus a small :class:`AbortSignal` class wrapping a
:class:`threading.Event`.

这是有意保持精简的——一个受 ``threading.Lock`` 保护的线程安全 ``dict``，
加上一个包装了 :class:`threading.Event` 的小型 :class:`AbortSignal` 类。
"""

from __future__ import annotations

import threading
from typing import Optional

from xijian_api.errors import GenerationAborted

# Module-level state (DESIGN §9.1).
# 模块级状态（DESIGN §9.1）。
_REGISTRY: dict[str, "AbortSignal"] = {}
_LOCK = threading.Lock()


class AbortSignal:
    """Cooperative cancellation primitive backed by a :class:`threading.Event`.

    由 :class:`threading.Event` 支持的协作式取消原语。

    Streaming generators call :meth:`raise_if_aborted` between chunk
    emissions; once :meth:`set` has been called the next call raises
    :class:`GenerationAborted` which is caught by the Flask error
    handler.

    流式生成器在每次块发出之间调用 :meth:`raise_if_aborted`；
    一旦调用了 :meth:`set`，下一次调用将抛出 :class:`GenerationAborted`，
    由 Flask 错误处理器捕获。
    """

    __slots__ = ("_ev",)

    def __init__(self) -> None:
        self._ev = threading.Event()

    def set(self) -> None:
        """Mark the signal as aborted.

        将信号标记为已中止。
        """
        self._ev.set()

    def is_set(self) -> bool:
        """Return ``True`` if :meth:`set` has been called.

        如果已调用 :meth:`set`，返回 ``True``。
        """
        return self._ev.is_set()

    def raise_if_aborted(self) -> None:
        """Raise :class:`GenerationAborted` if the signal has been set.

        如果信号已被设置，抛出 :class:`GenerationAborted`。
        """
        if self._ev.is_set():
            raise GenerationAborted("aborted by client")

    def reset(self) -> None:
        """Clear the signal so the same instance can be reused.

        清除信号，使同一实例可以被重用。
        """
        self._ev.clear()


def register(request_id: str) -> AbortSignal:
    """Register (or fetch) the :class:`AbortSignal` for ``request_id``.

    注册（或获取）``request_id`` 对应的 :class:`AbortSignal`。
    """
    with _LOCK:
        signal = _REGISTRY.get(request_id)
        if signal is None:
            signal = AbortSignal()
            _REGISTRY[request_id] = signal
        return signal


def get(request_id: str) -> Optional[AbortSignal]:
    """Return the registered signal for ``request_id`` or ``None``.

    返回为 ``request_id`` 注册的信号，或 ``None``。
    """
    with _LOCK:
        return _REGISTRY.get(request_id)


def abort(request_id: str) -> bool:
    """Trigger the abort for ``request_id``.

    触发 ``request_id`` 的中止。

    Returns ``True`` if a signal existed (and was set), ``False``
    otherwise.

    如果信号存在（并被设置了）返回 ``True``，否则返回 ``False``。
    """
    with _LOCK:
        signal = _REGISTRY.get(request_id)
        if signal is None:
            return False
        signal.set()
        return True


def cleanup(request_id: str) -> None:
    """Remove the entry for ``request_id`` from the registry.

    从注册表中移除 ``request_id`` 的条目。
    """
    with _LOCK:
        _REGISTRY.pop(request_id, None)


def reset_for_testing() -> None:
    """Clear the entire registry (used by tests).

    清除整个注册表（供测试使用）。
    """
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
