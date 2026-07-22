"""Logging configuration for the XiJian API server.

DESIGN §3.4: ``stderr`` (optionally a file) with the ``[xijian-api]``
prefix and ``%s`` placeholders.

The log level can be controlled (in priority order) via:

* the ``--log-level`` CLI flag handled in :mod:`xijian_api.app`,
* the ``XIJIAN_LOG_LEVEL`` environment variable
  (``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR`` / ``CRITICAL``),
* the ``logging`` argument passed directly to :func:`configure_logging`,
* a fallback of ``INFO``.

An optional log file can be enabled via ``XIJIAN_LOG_FILE`` or the
``--log-file`` CLI flag / :func:`reconfigure_logging` argument.

We deliberately avoid touching the root logger configuration so this
module can be imported safely from tests and from other modules that
already have logging configured (e.g. ``waitress``).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

_LOGGER_NAME = "xijian_api"
_PREFIX = "[xijian-api] "
# A compact, single-line format: prefix + timestamp + level + logger + message.
_FORMAT = _PREFIX + "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

#: Mapping of human-readable level names to :mod:`logging` constants.
LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,  # convenient alias
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,  # convenient alias
}

_configured = False
_current_level: int = logging.INFO
_current_log_file: Optional[str] = None


def resolve_level(value: "int | str | None", default: int = logging.INFO) -> int:
    """Resolve a logging level from an int, name, or ``None``.

    Unknown names fall back to ``default``.  ``None`` reads the
    ``XIJIAN_LOG_LEVEL`` environment variable (then ``default``).
    """
    if value is None:
        env = os.environ.get("XIJIAN_LOG_LEVEL", "").strip().upper()
        if not env:
            return default
        return LEVELS.get(env, default)
    if isinstance(value, int):
        return value
    return LEVELS.get(str(value).strip().upper(), default)


def _apply_handlers(
    logger: logging.Logger,
    level: int,
    log_file: "str | None",
) -> None:
    """(Re)wire the handlers of ``logger`` for the given configuration."""
    logger.setLevel(level)
    logger.propagate = False

    # Remove any pre-existing handlers so reconfigure is a clean slate.
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
        logger.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT, _DATEFMT)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            # File logging is best-effort — keep stderr working.
            logger.warning(
                "无法打开日志文件 %s: %s（仅使用 stderr 输出）",
                log_file,
                exc,
            )


def configure_logging(
    level: "int | None" = None,
    log_file: "str | None" = None,
) -> logging.Logger:
    """Configure the ``xijian_api`` logger and return it.

    The function is idempotent for the default case — calling it more
    than once without explicit arguments will not stack handlers.
    Pass explicit ``level`` / ``log_file`` (or use
    :func:`reconfigure_logging`) to force a reconfiguration.

    When ``level`` is ``None`` the level is resolved from the
    ``XIJIAN_LOG_LEVEL`` environment variable (default ``INFO``).
    """
    global _configured, _current_level, _current_log_file
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    resolved_level = resolve_level(level)
    resolved_file = log_file or os.environ.get("XIJIAN_LOG_FILE") or None
    _apply_handlers(logger, resolved_level, resolved_file)

    _configured = True
    _current_level = resolved_level
    _current_log_file = resolved_file
    return logger


def reconfigure_logging(
    level: "int | str | None" = None,
    log_file: "str | None" = None,
) -> logging.Logger:
    """Force a reconfiguration of the ``xijian_api`` logger.

    Unlike :func:`configure_logging` this clears existing handlers and
    reapplies them — useful when the CLI overrides logging options at
    startup after a module already lazily configured logging.
    """
    global _configured, _current_level, _current_log_file
    logger = logging.getLogger(_LOGGER_NAME)

    resolved_level = resolve_level(level)
    # If no explicit file is given, keep the previously configured one
    # (so a reconfigure that only changes the level does not drop the
    # file handler unexpectedly).
    resolved_file = log_file if log_file is not None else _current_log_file
    _apply_handlers(logger, resolved_level, resolved_file)

    _configured = True
    _current_level = resolved_level
    _current_log_file = resolved_file
    return logger


def get_logger() -> logging.Logger:
    """Return the configured ``xijian_api`` logger.

    If :func:`configure_logging` has not been called yet the logger is
    configured lazily with default settings (honouring
    ``XIJIAN_LOG_LEVEL`` / ``XIJIAN_LOG_FILE``).
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        return configure_logging()
    return logger


def current_level() -> int:
    """Return the currently effective numeric log level."""
    return _current_level


def current_log_file() -> "str | None":
    """Return the currently configured log file path (if any)."""
    return _current_log_file


__all__ = [
    "LEVELS",
    "configure_logging",
    "reconfigure_logging",
    "resolve_level",
    "get_logger",
    "current_level",
    "current_log_file",
]
