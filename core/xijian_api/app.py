"""Flask application factory and ``main()`` entry point.

The factory pattern (``create_app``) makes the foundation easy to
embed in tests (the test suite calls ``create_app(testing=True)``)
and in production (where the same factory is used to spin up the
real WSGI server).

The CLI entry point :func:`main` is intentionally resilient: every
startup stage is wrapped in best-effort recovery so the server keeps
running in a degraded but stable state whenever a non-fatal error
occurs (missing config file, missing token, missing storage dirs,
…).  Every recovery is logged — recovery never silences an error.
"""

from __future__ import annotations

import argparse
import logging
import os
import traceback
from pathlib import Path
from typing import Optional

from flask import Flask

from xijian_api import auth
from xijian_api.config import Config, DEFAULT_HOST, DEFAULT_PORT
from xijian_api.errors import register_error_handlers
from xijian_api.handshake import register_healthz
from xijian_api.middleware import install_middleware
from xijian_api.routes import register_routes
from xijian_api.utils.log import (
    configure_logging,
    get_logger,
    reconfigure_logging,
)

_LOGGER = get_logger()


def create_app(*, testing: bool = False, config: Config | None = None) -> Flask:
    """Build and return a configured :class:`flask.Flask` instance.

    Parameters
    ----------
    testing:
        When ``True`` the app runs in test mode:

        * The Bearer token is set to a fixed placeholder (no token-file
          I/O).
        * The WSGI server is **not** started; tests use
          ``app.test_client()``.
        * Logging is configured to ``INFO`` level (configurable via
          ``XIJIAN_LOG_LEVEL``).
    config:
        Optional pre-built :class:`Config` instance.  When ``None`` a
        fresh one is built from the environment, with ``testing``
        propagated.
    """
    configure_logging()

    if config is None:
        config = Config.from_env(testing=testing)
    elif testing and not config.testing:
        # Caller passed a non-testing config explicitly; honour the
        # flag they passed to ``create_app`` so tests win.
        object.__setattr__(config, "testing", True)

    app = Flask("xijian_api")
    app.config["TESTING"] = bool(testing)
    app.config["XIJIAN_CONFIG"] = config

    # Load the Bearer token (either from disk or generate a placeholder
    # in test mode).
    auth.setup_token(config)

    # Seed in-memory stub state so endpoints that expect default
    # records (Yuki, world_modern_tokyo, ...) have something to return.
    from xijian_api.stubs import seed_all

    seed_all()

    # Middleware first: request-id / trace-id / auth / idempotency.
    install_middleware(app)

    # Errors second so the handlers are in place before any blueprint
    # triggers an exception.
    register_error_handlers(app)

    # Healthcheck before routes so it's always available.
    register_healthz(app)

    # Routes (root + every optional module that imports cleanly).
    register_routes(app)

    _LOGGER.info(
        "xijian_api app created (testing=%s, dev=%s)",
        config.testing,
        config.dev,
    )
    return app


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for :func:`main`.

    Every option is optional — the server can start with no flags at
    all, falling back to sensible defaults (port ``18500``,
    ``0.0.0.0``, non-dev).  Environment variables
    (``XIJIAN_API_PORT`` / ``XIJIAN_HOST`` / ``XIJIAN_DEV`` /
    ``XIJIAN_LOG_LEVEL`` / ``XIJIAN_LOG_FILE`` / ``XIJIAN_CONFIG``)
    fill the gap between CLI flags and defaults.
    """
    parser = argparse.ArgumentParser(
        prog="xijian-api",
        description="XiJian Core API server — 本地优先的二次元 AI 聊天后端。",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"监听端口 (默认 {DEFAULT_PORT}，或 config.toml [server].port，或 $XIJIAN_API_PORT)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"监听地址 (默认 {DEFAULT_HOST}，或 $XIJIAN_HOST，或 config.toml)",
    )
    dev_group = parser.add_mutually_exclusive_group()
    dev_group.add_argument(
        "--dev",
        dest="dev",
        action="store_true",
        default=None,
        help="开发模式：自动生成 Bearer token 并启用测试路由",
    )
    dev_group.add_argument(
        "--no-dev",
        dest="dev",
        action="store_false",
        help="明确关闭开发模式（生产模式，需预置 token 文件）",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径 (覆盖 $XIJIAN_CONFIG 与默认搜索路径)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"],
        help="日志级别 (默认 INFO，或 $XIJIAN_LOG_LEVEL)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="日志文件路径 (可选；默认仅输出到 stderr，或 $XIJIAN_LOG_FILE)",
    )
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="完成初始化与自检后不启动 WSGI 服务 (用于冒烟测试)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本信息并退出",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------


def _ensure_storage_dirs(config: Config) -> None:
    """Create the storage directory tree, logging each recovery.

    Each subdirectory creation is independent so a failure on one
    (e.g. a read-only mount for snapshots) does not block the others.
    """
    storage = config.storage
    targets: list[tuple[str, Path]] = [
        ("base", storage.base_path),
        ("files", storage.files_path),
        ("models", storage.models_path),
        ("snapshots", storage.snapshots_path),
        ("audit", storage.audit_path),
    ]
    for label, path in targets:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _LOGGER.warning(
                "存储目录创建失败 [%s] %s: %s（相关功能可能不可用，服务继续启动）",
                label,
                path,
                exc,
            )
        else:
            _LOGGER.debug("存储目录就绪 [%s] %s", label, path)


def _load_config_resilient(testing: bool = False) -> Config:
    """Load configuration with automatic fallback to defaults.

    If the TOML file is missing or unparseable we fall back to an
    empty :class:`Config` (built-in defaults) and emit a WARNING — the
    server keeps running with stock settings rather than aborting.
    """
    try:
        return Config.from_env(testing=testing)
    except (OSError, ValueError, RuntimeError) as exc:
        _LOGGER.warning(
            "配置加载失败，回退到内置默认配置: %s (%s)",
            exc,
            type(exc).__name__,
        )
        return Config.empty()


def _build_app_resilient(config: Config) -> Flask:
    """Build the Flask app, auto-recovering from token failures.

    The most common production startup failure is a missing bearer
    token file when dev mode is off.  Rather than aborting, we retry
    once in dev mode (auto-generating a token) and log the downgrade.
    """
    try:
        return create_app(testing=config.testing, config=config)
    except RuntimeError as exc:
        # Token-related failures are the typical recoverable case.
        _LOGGER.warning(
            "应用初始化失败 (%s) — 尝试以开发模式重新生成 token 后启动",
            exc,
        )
        # Force dev mode on a fresh config copy so setup_token generates
        # a token instead of raising.
        forced = _with_dev(config)
        try:
            app = create_app(testing=forced.testing, config=forced)
            _LOGGER.warning(
                "已降级为开发模式启动：Bearer token 已自动生成 (path=%s)",
                os.environ.get("XIJIAN_DEV_TOKEN_FILE", "auto"),
            )
            return app
        except Exception as exc2:  # pragma: no cover - last resort
            _LOGGER.critical(
                "降级启动仍失败，无法继续: %s\n%s",
                exc2,
                traceback.format_exc(),
            )
            raise


def _with_dev(config: Config) -> Config:
    """Return a shallow copy of ``config`` with dev mode forced on."""
    import dataclasses

    new_server = dataclasses.replace(config.server, dev=True)
    return dataclasses.replace(config, server=new_server)


def _print_banner(
    config: Config,
    host: str,
    port: int,
    dev: bool,
    log_file: Optional[str],
) -> None:
    """Emit a startup banner summarising the resolved configuration."""
    bar = "=" * 64
    _LOGGER.info(bar)
    _LOGGER.info("XiJian Core API 启动")
    _LOGGER.info("监听地址      : %s:%d", host, port)
    _LOGGER.info("开发模式      : %s", dev)
    _LOGGER.info("测试模式      : %s", config.testing)
    _LOGGER.info("配置文件      : %s", config.source_path or "(内置默认)")
    _LOGGER.info("存储根目录    : %s", config.storage.base_path)
    _LOGGER.info("已注册模型    : %d 个", len(config.models))
    _LOGGER.info(
        "日志级别      : %s",
        logging.getLevelName(_LOGGER.getEffectiveLevel()),
    )
    if log_file:
        _LOGGER.info("日志文件      : %s", log_file)
    else:
        _LOGGER.info("日志文件      : (仅 stderr)")
    _LOGGER.info(bar)


# ---------------------------------------------------------------------------
# main() — production-style startup
# ---------------------------------------------------------------------------


def _serve(app: Flask, host: str, port: int) -> None:
    """Start a WSGI server.  Prefer ``waitress``; fall back to Flask.

    ``waitress`` is preferred because it is multi-threaded and
    matches what we will use in production.  When it is not
    installed (e.g. during very early development or in a stripped
    environment) we fall back to ``app.run`` which is single-threaded
    but adequate for local development.
    """
    try:
        from waitress import serve  # type: ignore[import-not-found]
    except ImportError:
        _LOGGER.warning(
            "waitress 未安装，回退到 Flask 开发服务器 (不建议生产使用)",
        )
        # ``threaded=True`` so the test client / curl smoke checks
        # don't deadlock under load.
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        return

    _LOGGER.info("waitress 服务启动: %s:%d", host, port)
    try:
        serve(app, host=host, port=port, ident="xijian-api")
    except OSError as exc:
        # EADDRINUSE on macOS/BSD is 48, on Linux 98.
        if getattr(exc, "errno", None) in (48, 98) or "Address already in use" in str(exc):
            _LOGGER.error(
                "端口 %d 已被占用，请使用 --port 指定其他端口或释放该端口",
                port,
            )
        raise
    except KeyboardInterrupt:
        _LOGGER.info("收到中断信号，正在关闭服务")
        raise


def main(argv: list[str] | None = None) -> int:
    """Production-style entry point.

    Parses CLI flags (with environment-variable and config-file
    fallbacks), creates the app, and starts a WSGI server.  Returns
    the process exit code.  Every recoverable error is logged and the
    server is kept in a stable state whenever possible.
    """
    args = parse_args(argv)

    # --version short-circuits before any heavy setup.
    if args.version:
        from xijian_api.config import API_VERSION

        print(f"xijian-api {API_VERSION}")
        return 0

    # Apply --config to the environment so Config.from_env picks it up.
    if args.config:
        os.environ["XIJIAN_CONFIG"] = args.config

    # Configure logging as early as possible so every subsequent log
    # line honours the requested level / file.
    reconfigure_logging(args.log_level, args.log_file)
    log_level_name = logging.getLevelName(_LOGGER.getEffectiveLevel())
    log_file = args.log_file or os.environ.get("XIJIAN_LOG_FILE")
    _LOGGER.info("日志系统就绪 (级别=%s, 文件=%s)", log_level_name, log_file or "stderr")

    try:
        return _run(args, log_file)
    except KeyboardInterrupt:
        _LOGGER.info("收到中断信号，正在关闭服务")
        return 0
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - last-resort safety net
        _LOGGER.critical(
            "启动过程中发生未捕获的致命错误: %s\n%s",
            exc,
            traceback.format_exc(),
        )
        return 1


def _run(args: argparse.Namespace, log_file: Optional[str]) -> int:
    """Inner startup routine, separated so :func:`main` can wrap it."""
    # ------------------------------------------------------------------
    # 1. Resolve the dev decision EARLY so configuration loading and
    #    token setup honour it.  Priority: CLI > env > config.toml.
    #    --dev  → force env XIJIAN_DEV=1 (overrides TOML).
    #    --no-dev → force env XIJIAN_DEV=0 (overrides TOML).
    #    neither → leave env untouched; from_env uses env-or-TOML.
    # ------------------------------------------------------------------
    if args.dev is True:
        os.environ["XIJIAN_DEV"] = "1"
    elif args.dev is False:
        os.environ["XIJIAN_DEV"] = "0"

    # ------------------------------------------------------------------
    # 2. Load configuration (resilient).  config.dev now reflects the
    #    CLI/env decision above.
    # ------------------------------------------------------------------
    config = _load_config_resilient(testing=False)
    dev = config.dev

    # ------------------------------------------------------------------
    # 3. Resolve host / port with CLI > env > config > default.
    # ------------------------------------------------------------------
    host = (
        args.host
        or os.environ.get("XIJIAN_HOST")
        or config.host
        or DEFAULT_HOST
    )
    # ``config.server.port`` already reflects $XIJIAN_API_PORT via
    # Config.from_env, so reading it covers env + TOML + default.
    port = args.port if args.port is not None else config.server.port
    if not (1 <= port <= 65535):
        _LOGGER.error("端口 %d 越界 (1-65535)，回退到默认 %d", port, DEFAULT_PORT)
        port = DEFAULT_PORT

    _LOGGER.info(
        "启动参数解析完成: host=%s port=%d dev=%s config=%s",
        host,
        port,
        dev,
        config.source_path or "(默认)",
    )

    # ------------------------------------------------------------------
    # 4. Ensure storage directories exist.
    # ------------------------------------------------------------------
    _ensure_storage_dirs(config)

    # ------------------------------------------------------------------
    # 5. Build the Flask app (auto-recovers token failures).
    # ------------------------------------------------------------------
    try:
        app = _build_app_resilient(config)
    except Exception:
        # _build_app_resilient already logged the critical detail.
        return 1

    # The app may have been rebuilt with dev forced on (token
    # auto-recovery); read the *effective* config back so the banner
    # reflects reality rather than the originally-requested value.
    effective_config: Config = app.config.get("XIJIAN_CONFIG", config)
    effective_dev = effective_config.dev

    # ------------------------------------------------------------------
    # 6. Startup banner.
    # ------------------------------------------------------------------
    _print_banner(effective_config, host, port, effective_dev, log_file)

    # ------------------------------------------------------------------
    # 7. Optionally skip serving (smoke test mode).
    # ------------------------------------------------------------------
    if args.no_serve:
        _LOGGER.info("--no-serve 已指定，初始化完成但不启动 WSGI 服务")
        return 0

    # ------------------------------------------------------------------
    # 8. Serve.
    # ------------------------------------------------------------------
    try:
        _serve(app, host, port)
    except OSError as exc:
        # Port-in-use already has a targeted message in _serve.
        _LOGGER.error("服务因 OSError 退出: %s", exc)
        return 1
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        _LOGGER.critical(
            "WSGI 服务异常退出: %s\n%s",
            exc,
            traceback.format_exc(),
        )
        return 1
    return 0


__all__ = ["create_app", "main", "parse_args"]
