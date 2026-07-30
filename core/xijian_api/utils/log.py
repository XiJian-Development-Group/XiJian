"""Logging configuration for the XiJian API server.
隙间 API 服务器的日志配置。

DESIGN §3.4: ``stderr`` (optionally a file) with the ``[xijian-api]``
prefix and ``%s`` placeholders.
DESIGN §3.4: ``stderr`` (可选文件) 带 ``[xijian-api]`` 前缀和 ``%s`` 占位符。

The log level can be controlled (in priority order) via:
日志级别可通过以下方式控制 (按优先级排序):

* the ``--log-level`` CLI flag handled in :mod:`xijian_api.app`,
  :mod:`xijian_api.app` 中处理的 ``--log-level`` CLI 标志,
* the ``XIJIAN_LOG_LEVEL`` environment variable
  (``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR`` / ``CRITICAL``),
  ``XIJIAN_LOG_LEVEL`` 环境变量,
* the ``logging`` argument passed directly to :func:`configure_logging`,
  直接传递给 :func:`configure_logging` 的 ``logging`` 参数,
* a fallback of ``INFO``.
  回退为 ``INFO``。

An optional log file can be enabled via ``XIJIAN_LOG_FILE`` or the
``--log-file`` CLI flag / :func:`reconfigure_logging` argument.
可通过 ``XIJIAN_LOG_FILE`` 或 ``--log-file`` CLI 标志 / :func:`reconfigure_logging` 参数启用可选的日志文件。

We deliberately avoid touching the root logger configuration so this
module can be imported safely from tests and from other modules that
already have logging configured (e.g. ``waitress``).
我们有意避免触碰根日志记录器配置，以便本模块可安全地从测试和已配置日志的其他模块 (如 ``waitress``) 导入。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

_LOGGER_NAME = "xijian_api"
_PREFIX = "[xijian-api] "
# A compact, single-line format: prefix + timestamp + level + logger + message.
# 紧凑的单行格式：前缀 + 时间戳 + 级别 + 日志记录器 + 消息。
_FORMAT = _PREFIX + "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

#: Mapping of human-readable level names to :mod:`logging` constants.
#: 人类可读级别名称到 :mod:`logging` 常量的映射。
LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,  # convenient alias / 便捷别名
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,  # convenient alias / 便捷别名
}

_configured = False
_current_level: int = logging.INFO
_current_log_file: Optional[str] = None


def resolve_level(value: "int | str | None", default: int = logging.INFO) -> int:
    """Resolve a logging level from an int, name, or ``None``.
    从整数、名称或 ``None`` 解析日志级别。

    Unknown names fall back to ``default``.  ``None`` reads the
    ``XIJIAN_LOG_LEVEL`` environment variable (then ``default``).
    未知名称回退为 ``default``。``None`` 读取 ``XIJIAN_LOG_LEVEL`` 环境变量 (然后回退为 ``default``)。
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
    """(Re)wire the handlers of ``logger`` for the given configuration.
    为给定配置 (重新) 连接 ``logger`` 的处理器。"""
    logger.setLevel(level)
    logger.propagate = False

    # Remove any pre-existing handlers so reconfigure is a clean slate.
    # 移除所有已有处理器，使重新配置从干净状态开始。
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:  # pragma: no cover - best effort cleanup / 尽力清理
            pass
        logger.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT, _DATEFMT)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    if log_file:
        try:
            # Ensure the parent directory exists so a non-existent log
            # file (including one in a not-yet-created directory) is
            # auto-created instead of raising FileNotFoundError.
            # 确保父目录存在，使不存在的日志文件 (包括尚在未创建目录中的) 能自动创建而非抛出 FileNotFoundError。
            parent = os.path.dirname(os.path.abspath(log_file))
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            # File logging is best-effort — keep stderr working.
            # 文件日志为尽力而为 —— 保持 stderr 可用。
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
    配置 ``xijian_api`` 日志记录器并返回它。

    The function is idempotent for the default case — calling it more
    than once without explicit arguments will not stack handlers.
    该函数在默认情况下为幂等 —— 无显式参数的多次调用不会堆积处理器。

    Pass explicit ``level`` / ``log_file`` (or use
    :func:`reconfigure_logging`) to force a reconfiguration.
    传递显式 ``level`` / ``log_file`` (或使用 :func:`reconfigure_logging`) 以强制重新配置。

    When ``level`` is ``None`` the level is resolved from the
    ``XIJIAN_LOG_LEVEL`` environment variable (default ``INFO``).
    当 ``level`` 为 ``None`` 时，从 ``XIJIAN_LOG_LEVEL`` 环境变量解析级别 (默认为 ``INFO``)。

    When ``log_file`` is ``None`` the file is resolved from
    ``XIJIAN_LOG_FILE``; in 打包模式(frozen) 下若仍未指定，则
    默认写入 ``<exe_dir>/logs/xijian-api.log``。
    当 ``log_file`` 为 ``None`` 时，从 ``XIJIAN_LOG_FILE`` 解析；
    in 打包模式(frozen) 下若仍未指定，则默认写入 ``<exe_dir>/logs/xijian-api.log``。
    """
    global _configured, _current_level, _current_log_file
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    resolved_level = resolve_level(level)
    resolved_file = log_file or os.environ.get("XIJIAN_LOG_FILE") or None
    # 打包模式下，若未指定日志文件，使用可执行文件同级的 logs/ 目录
    # In frozen mode, when no log file is specified, use logs/ dir next to the executable
    if resolved_file is None:
        from xijian_api.runtime import is_frozen, default_log_file
        if is_frozen():
            resolved_file = str(default_log_file())
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
    强制重新配置 ``xijian_api`` 日志记录器。

    Unlike :func:`configure_logging` this clears existing handlers and
    reapplies them — useful when the CLI overrides logging options at
    startup after a module already lazily configured logging.
    与 :func:`configure_logging` 不同，这会清除已有处理器并重新应用 ——
    当 CLI 在模块已延迟配置日志后于启动时覆盖日志选项时很有用。
    """
    global _configured, _current_level, _current_log_file
    logger = logging.getLogger(_LOGGER_NAME)

    resolved_level = resolve_level(level)
    # If no explicit file is given, keep the previously configured one
    # (so a reconfigure that only changes the level does not drop the
    # file handler unexpectedly).
    # 若未给出显式文件，保留先前已配置的文件 (以便仅更改级别的重新配置不会意外丢失文件处理器)。
    resolved_file = log_file if log_file is not None else _current_log_file
    _apply_handlers(logger, resolved_level, resolved_file)

    _configured = True
    _current_level = resolved_level
    _current_log_file = resolved_file
    return logger


def get_logger() -> logging.Logger:
    """Return the configured ``xijian_api`` logger.
    返回已配置的 ``xijian_api`` 日志记录器。

    If :func:`configure_logging` has not been called yet the logger is
    configured lazily with default settings (honouring
    ``XIJIAN_LOG_LEVEL`` / ``XIJIAN_LOG_FILE``).
    若尚未调用 :func:`configure_logging`，则使用默认设置延迟配置日志记录器
    (遵从 ``XIJIAN_LOG_LEVEL`` / ``XIJIAN_LOG_FILE``)。
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        return configure_logging()
    return logger


def current_level() -> int:
    """Return the currently effective numeric log level.
    返回当前生效的数字日志级别。"""
    return _current_level


def current_log_file() -> "str | None":
    """Return the currently configured log file path (if any).
    返回当前配置的日志文件路径 (如有)。"""
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