"""AI 抽象层：错误类型、数据类和后端基类。

AI abstraction layer: error types, dataclasses, and backend base classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence


# ---------------------------------------------------------------------------
# Errors / 错误类型
# ---------------------------------------------------------------------------


class BackendError(Exception):
    """后端错误基类。Base class for backend errors."""
    code = "backend_error"
    recoverable = True

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class BackendUnavailable(BackendError):
    """后端不可用（如平台不支持、依赖缺失等）。Backend unavailable (e.g., unsupported platform, missing deps)."""
    code = "backend_unavailable"


class ModelNotFound(BackendError):
    """配置中找不到模型。Model not found in config."""
    code = "model_not_found"
    recoverable = False


class ModelNotLoaded(BackendError):
    """模型未加载。Model not loaded."""
    code = "model_not_loaded"


class ContextLengthExceeded(BackendError):
    """上下文长度超限。Context length exceeded."""
    code = "context_length_exceeded"
    recoverable = False


class GenerationAborted(BackendError):
    """生成被中止。Generation aborted."""
    code = "generation_aborted"
    recoverable = False


class GuardBlocked(BackendError):
    """内容被安全策略拦截。Content blocked by guard/safety policy."""
    code = "protection_blocked"
    recoverable = False