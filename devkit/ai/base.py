"""DevKit 的 AI 抽象层：错误、数据类和后端基类。

复制并改编自 core/xijian_api/ai/base.py
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class BackendError(Exception):
    code = "backend_error"
    recoverable = True

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class BackendUnavailable(BackendError):
    code = "backend_unavailable"


class ModelNotFound(BackendError):
    code = "model_not_found"
    recoverable = False


class ModelNotLoaded(BackendError):
    code = "model_not_loaded"


class ContextLengthExceeded(BackendError):
    code = "context_length_exceeded"
    recoverable = False


class GenerationAborted(BackendError):
    code = "generation_aborted"
    recoverable = False


class GuardBlocked(BackendError):
    code = "protection_blocked"
    recoverable = False