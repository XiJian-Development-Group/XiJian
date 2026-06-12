"""Logging configuration for the XiJian API server.

DESIGN §3.4: ``stderr`` only, ``[xijian-api]`` prefix, ``%s`` placeholders.

We deliberately avoid touching the root logger configuration so this
module can be imported safely from tests and from other modules that
already have logging configured (e.g. ``waitress``).
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "xijian_api"
_PREFIX = "[xijian-api] "

_configured = False


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the ``xijian_api`` logger and return it.

    The function is idempotent — calling it more than once will not
    stack handlers.  It writes to ``stderr`` and prepends ``[xijian-api]``
    to every record.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_PREFIX + "%(message)s"))
    logger.addHandler(handler)

    _configured = True
    return logger


def get_logger() -> logging.Logger:
    """Return the configured ``xijian_api`` logger.

    If :func:`configure_logging` has not been called yet the logger is
    configured lazily with default settings.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        return configure_logging()
    return logger


__all__ = ["configure_logging", "get_logger"]