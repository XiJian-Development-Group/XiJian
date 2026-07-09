"""AI abstraction layer for DevKit: errors, dataclasses, and backend base classes.

Copied & adapted from core/xijian_api/ai/base.py
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Errors
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