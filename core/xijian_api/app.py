"""Flask application factory and ``main()`` entry point.

Flask 应用工厂和 ``main()`` 入口点。

The factory pattern (``create_app``) makes the foundation easy to
embed in tests (the test suite calls ``create_app(testing=True)``)
and in production (where the same factory is used to spin up the
real WSGI server).

工厂模式 (``create_app``) 使得基础框架易于嵌入测试（测试套件调用 ``create_app(testing=True)``）
和生产环境（使用同一工厂启动真实的 WSGI 服务器）。

The CLI entry point :func:`main` is intentionally resilient: every
startup stage is wrapped in best-effort recovery so the server keeps
running in a degraded but stable state whenever a non-fatal error
occurs (missing config file, missing token, missing storage dirs,
…).  Every recovery is logged, recovery never silences an error.

CLI 入口点 :func:`main` 被设计为具有弹性：每个启动阶段都包裹在尽力恢复中，
以便在发生非致命错误（缺少配置文件、缺少令牌、缺少存储目录等）时，
服务器能以降级但稳定的状态继续运行。每次恢复都会被记录，恢复不会静默错误。
"""

from __future__ import annotations

import argparse
import logging
import os
import traceback
from pathlib import Path
from typing import Optional

from flask import Flask

from werkzeug.serving import make_server as _werkzeug_make_server

import atexit

from xijian_api import auth
from xijian_api.config import Config, DEFAULT_HOST, DEFAULT_PORT
from xijian_api.discovery import write_discovery, remove_discovery
from xijian_api.errors import register_error_handlers
from xijian_api.handshake import register_healthz
from xijian_api.middleware import install_middleware
from xijian_api.routes import register_routes
from xijian_api.runtime import (
    ensure_runtime_dirs,
    is_frozen,
    setup_external_libs,
)
from xijian_api.utils.log import (
    configure_logging,
    get_logger,
    reconfigure_logging,
)

_LOGGER = get_logger()


def create_app(*, testing: bool = False, config: Config | None = None) -> Flask:
    """Build and return a configured :class:`flask.Flask` instance.

    构建并返回一个已配置的 :class:`flask.Flask` 实例。

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

        当 ``True`` 时，应用以测试模式运行：
        * Bearer 令牌设置为固定占位符（无需读写令牌文件）。
        * WSGI 服务器**不会**启动；测试使用 ``app.test_client()``。
        * 日志级别配置为 ``INFO``（可通过 ``XIJIAN_LOG_LEVEL`` 配置）。
    config:
        Optional pre-built :class:`Config` instance.  When ``None`` a
        fresh one is built from the environment, with ``testing``
        propagated.

        可选预构建的 :class:`Config` 实例。为 ``None`` 时，
        从环境变量构建新的配置，并传递 ``testing`` 参数。
    """
    configure_logging()

    if config is None:
        config = Config.from_env(testing=testing)
    elif testing and not config.testing:
        # Caller passed a non-testing config explicitly; honour the
        # flag they passed to ``create_app`` so tests win.
        # 调用者显式传入了非测试配置；尊重传递给 ``create_app`` 的标志，使测试优先。
        object.__setattr__(config, "testing", True)

    app = Flask("xijian_api")
    app.config["TESTING"] = bool(testing)
    app.config["XIJIAN_CONFIG"] = config

    # Load the Bearer token (either from disk or generate a placeholder
    # in test mode).
    # 加载 Bearer 令牌（从磁盘加载，或在测试模式下生成占位符）。
    auth.setup_token(config)

    # A.5 — Legacy data migration (``~/.xijian`` → CORE_ROOT).  Runs
    # synchronously once at startup, before seeding, so the unified
    # storage root is populated before any handler reads from it.
    # Failures are logged but never block startup.
    # A.5 — 旧数据迁移（``~/.xijian`` → CORE_ROOT）。在播种之前启动时
    # 同步执行一次，使统一存储根目录在任何处理器读取之前就已填充。
    # 失败仅记日志，绝不阻塞启动。
    try:
        from xijian_api.stubs import migration as migration_stub

        migration_result = migration_stub.migrate_legacy_data()
        _LOGGER.info(
            "legacy migration: migrated=%s conflicts=%d",
            migration_result.get("migrated"),
            len(migration_result.get("conflicts", []) or []),
        )
    except Exception as exc:  # noqa: BLE001 - migration is best-effort
        _LOGGER.warning("legacy migration failed (non-fatal): %s", exc)

    # Seed in-memory stub state so endpoints that expect default
    # records (Yuki, world_modern_tokyo, ...) have something to return.
    # 播种内存中的存根状态，使期望默认记录（Yuki, world_modern_tokyo 等）的端点有数据可返回。
    from xijian_api.stubs import seed_all

    seed_all()

    # A5.3 — Apply the ``[snapshots]`` config section (R5) to the
    # runtime backup policy so operators' config.toml edits take
    # effect at startup.  Non-default values only; a missing/empty
    # section leaves the stub's spec defaults untouched.
    # A5.3 — 将 ``[snapshots]`` 配置段（R5）应用到运行时备份策略，
    # 使运营者在 config.toml 中的修改在启动时生效。仅应用非默认值；
    # 缺失/空配置段时保持存根的规范默认值不变。
    try:
        from xijian_api.stubs import snapshots as snapshots_stub

        snapshots_stub.apply_config(config.snapshots)
    except Exception as exc:  # noqa: BLE001 - startup is best-effort
        _LOGGER.warning(
            "applying [snapshots] config failed (non-fatal): %s", exc
        )

    # B.3 — Resource packs.  Install preload packs (idempotent) then
    # scan + load already-installed packs into runtime state.  Failures
    # are logged but never block startup.
    # B.3 — 资源包。安装预置包（幂等），然后扫描并加载已安装的包。
    # 失败仅记日志，绝不阻塞启动。
    try:
        from xijian_api.stubs import packs as packs_stub

        preload = packs_stub.ensure_preload_packs()
        _LOGGER.info("preload packs installed: %d", len(preload.get("installed", []) or []))
        scan = packs_stub.scan_packs()
        _LOGGER.info("installed packs: %d", len(scan.get("installed", []) or []))
    except Exception as exc:  # noqa: BLE001 - packs init is best-effort
        _LOGGER.warning("resource packs init failed (non-fatal): %s", exc)

    # Middleware first: request-id / trace-id / auth / idempotency.
    # 先安装中间件：请求 ID / 追踪 ID / 认证 / 幂等性。
    install_middleware(app)

    # Errors second so the handlers are in place before any blueprint
    # triggers an exception.
    # 再注册错误处理器，确保在任何蓝图触发异常之前处理器已就位。
    register_error_handlers(app)

    # Healthcheck before routes so it's always available.
    # 健康检查在路由之前注册，确保始终可用。
    register_healthz(app)

    # Routes (root + every optional module that imports cleanly).
    # 注册路由（根路由 + 每个能干净导入的可选模块）。
    register_routes(app)

    _LOGGER.info(
        "xijian_api app created (testing=%s, dev=%s)",
        config.testing,
        config.dev,
    )
    return app


# ---------------------------------------------------------------------------
# CLI argument parsing
# CLI 参数解析
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for :func:`main`.

    解析 :func:`main` 的命令行参数。

    Every option is optional — the server can start with no flags at
    all, falling back to sensible defaults (port ``18500``,
    ``127.0.0.1``, non-dev).  Environment variables
    (``XIJIAN_API_PORT`` / ``XIJIAN_HOST`` / ``XIJIAN_DEV`` /
    ``XIJIAN_LOG_LEVEL`` / ``XIJIAN_LOG_FILE`` / ``XIJIAN_CONFIG``)
    fill the gap between CLI flags and defaults.

    每个选项都是可选的——服务器可以在没有任何标志的情况下启动，
    回退到合理的默认值（端口 ``18500``、``127.0.0.1``、非开发模式）。
    环境变量（``XIJIAN_API_PORT`` / ``XIJIAN_HOST`` / ``XIJIAN_DEV`` /
    ``XIJIAN_LOG_LEVEL`` / ``XIJIAN_LOG_FILE`` / ``XIJIAN_CONFIG``）
    填补了 CLI 标志和默认值之间的空白。
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
        "--port-strict",
        action="store_true",
        help="端口被占用时直接报错退出，不自动更换端口 (默认自动更换可用端口)",
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
        "--server",
        default=None,
        choices=["auto", "werkzeug", "waitress"],
        help="WSGI 服务器驱动: auto (默认，解析为 werkzeug，WebSocket 可用) / werkzeug / waitress (不支持 WebSocket) (默认 auto，或 config.toml [server].driver)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本信息并退出",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Startup helpers
# 启动辅助函数
# ---------------------------------------------------------------------------


def _ensure_storage_dirs(config: Config) -> None:
    """Create the storage directory tree, logging each recovery.

    创建存储目录树，记录每次恢复操作。

    Each subdirectory creation is independent so a failure on one
    (e.g. a read-only mount for snapshots) does not block the others.

    每个子目录的创建是独立的，这样某个子目录的失败（例如快照目录为只读挂载）
    不会阻塞其他目录的创建。
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

    加载配置，自动回退到默认值。

    If the TOML file is missing or unparseable we fall back to an
    empty :class:`Config` (built-in defaults) and emit a WARNING — the
    server keeps running with stock settings rather than aborting.

    如果 TOML 文件不存在或无法解析，我们回退到空的 :class:`Config`（内置默认值）
    并发出 WARNING——服务器使用默认设置继续运行，而不是中止。
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

    构建 Flask 应用，自动从令牌故障中恢复。

    The most common production startup failure is a missing bearer
    token file when dev mode is off.  Rather than aborting, we retry
    once in dev mode (auto-generating a token) and log the downgrade.

    最常见的生产启动故障是开发模式关闭时缺少 Bearer 令牌文件。
    与其中止，我们会在开发模式下重试一次（自动生成令牌）并记录降级。
    """
    try:
        return create_app(testing=config.testing, config=config)
    except RuntimeError as exc:
        # Token-related failures are the typical recoverable case.
        # 令牌相关故障是典型的可恢复情况。
        _LOGGER.warning(
            "应用初始化失败 (%s) — 尝试以开发模式重新生成 token 后启动",
            exc,
        )
        # Force dev mode on a fresh config copy so setup_token generates
        # a token instead of raising.
        # 在全新的配置副本上强制启用开发模式，使 setup_token 生成令牌而非抛出异常。
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
    """Return a shallow copy of ``config`` with dev mode forced on.

    返回 ``config`` 的浅拷贝，并强制开启开发模式。
    """
    import dataclasses

    new_server = dataclasses.replace(config.server, dev=True)
    return dataclasses.replace(config, server=new_server)


def _write_port_file(port: int) -> None:
    """Write the actual listening port to ``run/xijian-<pid>.port`` so
    client processes (the macOS app) can discover it after automatic
    port fallback.

    将实际监听端口写入 ``run/xijian-<pid>.port``，使客户端进程
    （macOS App）在自动换端口后能够发现真实端口。

    Best-effort: a failure is logged but never blocks startup.
    尽力而为：失败仅记录日志，绝不阻塞启动。
    """
    from xijian_api.runtime import default_port_file

    path = default_port_file()
    try:
        path.write_text(str(port), encoding="utf-8")
        _LOGGER.info("实际端口已写入 %s (%d)", path, port)
    except OSError as exc:
        _LOGGER.warning("写入端口文件失败 %s: %s（macapp 可能无法自动发现端口）", path, exc)


def _print_banner(
    config: Config,
    host: str,
    port: int,
    dev: bool,
    log_file: Optional[str],
    server_driver: str = "werkzeug",
) -> None:
    """Emit a startup banner summarising the resolved configuration.

    输出启动横幅，概述已解析的配置。
    """
    from xijian_api.runtime import print_environment_info

    bar = "=" * 64
    _LOGGER.info(bar)
    _LOGGER.info("XiJian Core API 启动")
    _LOGGER.info("监听地址      : %s:%d", host, port)
    ws_hint = "WebSocket 可用" if server_driver == "werkzeug" else "WebSocket 不可用 (/v1/ws)"
    _LOGGER.info("服务器驱动    : %s (%s)", server_driver, ws_hint)
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
    # 运行时环境信息（打包模式/开发模式）
    # Runtime environment info (packaged mode / development mode)
    for line in print_environment_info().splitlines():
        _LOGGER.info(line)
    _LOGGER.info(bar)


# ---------------------------------------------------------------------------
# Server driver selection
# 服务器驱动选型
# ---------------------------------------------------------------------------


def resolve_server_driver(cli_value: str | None, config_value: str | None) -> str:
    """Resolve the effective WSGI server driver.

    解析实际生效的 WSGI 服务器驱动。

    Priority is CLI > config > default.  ``auto`` always resolves to
    ``werkzeug``: the WebSocket endpoint ``/v1/ws`` (feature list A6/A7)
    depends on flask-sock, which needs the raw socket from the WSGI
    environment — waitress does not provide it, so the handshake 500s.
    An explicit ``waitress`` is honoured, with a startup warning that
    ``/v1/ws`` will be unavailable.  Unknown values fall back to
    ``auto`` (→ ``werkzeug``).

    优先级为 CLI > 配置 > 默认。``auto`` 始终解析为 ``werkzeug``：
    WebSocket 端点 ``/v1/ws``（功能清单 A6/A7）依赖 flask-sock，
    需要 WSGI 环境暴露原始 socket——waitress 不提供，握手会 500。
    显式 ``waitress`` 会被采纳，但启动时会警告 ``/v1/ws`` 不可用。
    未知值回退到 ``auto``（→ ``werkzeug``）。

    Returns
    -------
    str
        ``"werkzeug"`` or ``"waitress"`` (never ``"auto"``).

        返回 ``"werkzeug"`` 或 ``"waitress"``（永远不会是 ``"auto"``）。
    """
    chosen = (cli_value or config_value or "auto").strip().lower()
    if chosen not in {"auto", "werkzeug", "waitress"}:
        _LOGGER.warning(
            "未知的服务器驱动 %r，回退到 auto (werkzeug)",
            chosen,
        )
        chosen = "auto"
    if chosen == "auto":
        # WebSocket (A6/A7) 是功能清单核心，必须可用 → 默认 werkzeug。
        return "werkzeug"
    return chosen


# ---------------------------------------------------------------------------
# main() — production-style startup
# main() — 生产风格启动
# ---------------------------------------------------------------------------


def _serve(app: Flask, host: str, port: int, server: str = "auto") -> None:
    """Start a WSGI server according to the resolved driver.

    按解析后的驱动启动 WSGI 服务器。

    ``auto`` (default) resolves to ``werkzeug`` (threaded), because the
    WebSocket endpoint ``/v1/ws`` — spec A6/A7 — is a core capability and
    requires a WSGI environment that exposes the raw socket (flask-sock);
    waitress does not provide one, so the handshake fails with 500.
    Pass ``server="waitress"`` to opt into waitress (faster for plain
    HTTP, but ``/v1/ws`` will be unavailable and a WARNING is logged).
    If waitress is requested but not installed, we fall back to
    ``werkzeug`` so the server still starts.

    ``auto``（默认）解析为 ``werkzeug``（多线程）：WebSocket 端点
    ``/v1/ws``（规格 A6/A7）是核心能力，要求 WSGI 环境暴露原始
    socket（flask-sock 依赖）；waitress 不提供，握手会 500。
    传入 ``server="waitress"`` 可选择 waitress（纯 HTTP 更快，但
    ``/v1/ws`` 不可用，且会记录 WARNING）。若请求 waitress 但未安装，
    回退到 ``werkzeug`` 以保证服务仍能启动。
    """
    driver = resolve_server_driver(server, None)

    if driver == "waitress":
        try:
            from waitress import serve  # type: ignore[import-not-found]
        except ImportError:
            _LOGGER.warning(
                "waitress 未安装，回退到 werkzeug (WebSocket 可用)",
            )
            driver = "werkzeug"

    if driver == "waitress":
        _LOGGER.warning(
            "waitress 不支持 WebSocket，/v1/ws 将不可用（如需 WebSocket 请改用 werkzeug）",
        )
        _LOGGER.info("waitress 服务启动: %s:%d", host, port)
        try:
            serve(app, host=host, port=port, ident="xijian-api")
        except OSError as exc:
            # EADDRINUSE on macOS/BSD is 48, on Linux 98.
            # macOS/BSD 上 EADDRINUSE 为 48，Linux 上为 98。
            if getattr(exc, "errno", None) in (48, 98) or "Address already in use" in str(exc):
                _LOGGER.error(
                    "端口 %d 已被占用，请使用 --port 指定其他端口或释放该端口",
                    port,
                )
            raise
        except KeyboardInterrupt:
            _LOGGER.info("收到中断信号，正在关闭服务")
            raise
        return

    # werkzeug (threaded) — the reliable path also used by the test
    # suite (tests/test_ws.py); WebSocket handshake works here.
    # werkzeug（多线程）— 与测试套件 (tests/test_ws.py) 一致且可靠的路径；
    # WebSocket 握手在此驱动下可用。
    _LOGGER.info("werkzeug 服务启动: %s:%d (WebSocket 可用)", host, port)
    try:
        httpd = _werkzeug_make_server(host, port, app, threaded=True)
    except OSError as exc:
        # EADDRINUSE on macOS/BSD is 48, on Linux 98.
        # macOS/BSD 上 EADDRINUSE 为 48，Linux 上为 98。
        if getattr(exc, "errno", None) in (48, 98) or "Address already in use" in str(exc):
            _LOGGER.error(
                "端口 %d 已被占用，请使用 --port 指定其他端口或释放该端口",
                port,
            )
        raise
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _LOGGER.info("收到中断信号，正在关闭服务")
        raise


def main(argv: list[str] | None = None) -> int:
    """Production-style entry point.

    生产风格入口点。

    Parses CLI flags (with environment-variable and config-file
    fallbacks), creates the app, and starts a WSGI server.  Returns
    the process exit code.  Every recoverable error is logged and the
    server is kept in a stable state whenever possible.

    解析 CLI 标志（包含环境变量和配置文件的回退），创建应用，并启动 WSGI 服务器。
    返回进程退出码。每个可恢复的错误都会被记录，服务器尽可能保持稳定状态。
    """
    # 打包模式下的早期初始化（开发模式下为空操作）
    # Early initialisation in packaged mode (no-op in dev mode)
    setup_external_libs()
    ensure_runtime_dirs()

    args = parse_args(argv)

    # --version short-circuits before any heavy setup.
    # --version 在任何重负载设置之前短路。
    if args.version:
        from xijian_api.config import API_VERSION

        print(f"xijian-api {API_VERSION}")
        return 0

    # Apply --config to the environment so Config.from_env picks it up.
    # 将 --config 应用到环境变量中，以便 Config.from_env 能够读取。
    if args.config:
        os.environ["XIJIAN_CONFIG"] = args.config

    # Configure logging as early as possible so every subsequent log
    # line honours the requested level / file.
    # 尽早配置日志系统，使后续所有日志都遵循请求的级别和文件。
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
    """Inner startup routine, separated so :func:`main` can wrap it.

    内部启动例程，分离出来以便 :func:`main` 可以包装它。
    """
    # ------------------------------------------------------------------
    # 1. Resolve the dev decision EARLY so configuration loading and
    #    token setup honour it.  Priority: CLI > env > config.toml.
    #    --dev  → force env XIJIAN_DEV=1 (overrides TOML).
    #    --no-dev → force env XIJIAN_DEV=0 (overrides TOML).
    #    neither → leave env untouched; from_env uses env-or-TOML.
    # 1. 尽早解析开发模式决策，使配置加载和令牌设置遵循它。
    #    优先级：CLI > 环境变量 > config.toml。
    #    --dev  → 强制设置环境变量 XIJIAN_DEV=1（覆盖 TOML）。
    #    --no-dev → 强制设置环境变量 XIJIAN_DEV=0（覆盖 TOML）。
    #    两者都未指定 → 保持环境变量不变；from_env 使用环境变量或 TOML。
    # ------------------------------------------------------------------
    if args.dev is True:
        os.environ["XIJIAN_DEV"] = "1"
    elif args.dev is False:
        os.environ["XIJIAN_DEV"] = "0"

    # ------------------------------------------------------------------
    # 2. Load configuration (resilient).  config.dev now reflects the
    #    CLI/env decision above.
    # 2. 加载配置（弹性）。config.dev 现在反映了上面的 CLI/环境变量决策。
    # ------------------------------------------------------------------
    config = _load_config_resilient(testing=False)
    dev = config.dev

    # ------------------------------------------------------------------
    # 3. Resolve host / port with CLI > env > config > default.
    # 3. 解析主机/端口：CLI > 环境变量 > 配置 > 默认值。
    # ------------------------------------------------------------------
    host = (
        args.host
        or os.environ.get("XIJIAN_HOST")
        or config.host
        or DEFAULT_HOST
    )
    # ``config.server.port`` already reflects $XIJIAN_API_PORT via
    # Config.from_env, so reading it covers env + TOML + default.
    # ``config.server.port`` 已通过 Config.from_env 反映 $XIJIAN_API_PORT，
    # 因此读取它覆盖了环境变量 + TOML + 默认值。
    port = args.port if args.port is not None else config.server.port
    if not (1 <= port <= 65535):
        _LOGGER.error("端口 %d 越界 (1-65535)，回退到默认 %d", port, DEFAULT_PORT)
        port = DEFAULT_PORT

    # Port pre-flight: if the configured port is occupied, report the
    # occupant and fall back to the next free port (unless --port-strict).
    # 端口预检：配置端口被占用时报告占用进程并自动更换到下一个空闲端口
    # （除非指定 --port-strict）。
    if args.port_strict:
        from xijian_api.ports import find_port_occupant, is_port_in_use

        if is_port_in_use(host, port):
            occupant = find_port_occupant(port)
            detail = f"，被 {occupant} 占用" if occupant else ""
            _LOGGER.error(
                "端口 %d 已被占用%s。--port-strict 已指定，拒绝启动。"
                "请释放该端口或改用其他端口。",
                port,
                detail,
            )
            return 1
    else:
        from xijian_api.ports import resolve_available_port

        try:
            resolution = resolve_available_port(host, port)
        except Exception as exc:  # noqa: BLE001 - startup report
            _LOGGER.error("自动更换端口失败: %s", exc)
            return 1
        if resolution.changed:
            occupant = resolution.occupied_by
            detail = f"，被 {occupant} 占用" if occupant else ""
            _LOGGER.warning(
                "端口 %d 已被占用%s。已自动更换端口: %d → %d",
                port,
                detail,
                port,
                resolution.port,
            )
            port = resolution.port

    _LOGGER.info(
        "启动参数解析完成: host=%s port=%d dev=%s config=%s",
        host,
        port,
        dev,
        config.source_path or "(默认)",
    )

    # ------------------------------------------------------------------
    # 4. Ensure storage directories exist.
    # 4. 确保存储目录存在。
    # ------------------------------------------------------------------
    _ensure_storage_dirs(config)

    # ------------------------------------------------------------------
    # 5. Build the Flask app (auto-recovers token failures).
    # 5. 构建 Flask 应用（自动恢复令牌故障）。
    # ------------------------------------------------------------------
    try:
        app = _build_app_resilient(config)
    except Exception:
        # _build_app_resilient already logged the critical detail.
        # _build_app_resilient 已经记录了关键细节。
        return 1

    # The app may have been rebuilt with dev forced on (token
    # auto-recovery); read the *effective* config back so the banner
    # reflects reality rather than the originally-requested value.
    # 应用可能已通过强制开发模式重建（令牌自动恢复）；
    # 读取*实际生效的*配置，使横幅反映现实而非最初请求的值。
    effective_config: Config = app.config.get("XIJIAN_CONFIG", config)
    effective_dev = effective_config.dev

    # ------------------------------------------------------------------
    # 6. Startup banner.
    # 6. 启动横幅。
    # ------------------------------------------------------------------
    # Resolve the WSGI server driver once (CLI > config > auto) so the
    # banner and the actual serve step agree.
    # 只解析一次 WSGI 服务器驱动 (CLI > config > auto)，使横幅与实际
    # 服务步骤一致。
    server_driver = resolve_server_driver(
        args.server,
        effective_config.server.server_driver,
    )
    _print_banner(
        effective_config,
        host,
        port,
        effective_dev,
        log_file,
        server_driver=server_driver,
    )

    # ------------------------------------------------------------------
    # 7. Optionally skip serving (smoke test mode).
    # 7. 可选地跳过服务（冒烟测试模式）。
    # ------------------------------------------------------------------
    if args.no_serve:
        _LOGGER.info("--no-serve 已指定，初始化完成但不启动 WSGI 服务")
        return 0

    # ------------------------------------------------------------------
    # 8. Write discovery file so other local processes (DevKit) can
    #    find us.
    # 8. 写入发现文件，使其他本地进程（DevKit）能够找到我们。
    # ------------------------------------------------------------------
    token = auth.get_token()
    if token and not config.testing:
        write_discovery(port=port, auth_token=token, pid=os.getpid())
        atexit.register(remove_discovery)
        _LOGGER.info("Core discovery published for port %d", port)

    # Publish the actual port to the pid-scoped port file (the macOS app
    # waits for this file, then polls /healthz on that port).  Written
    # *before* serving so the app finds the port even when fallback
    # changed it.
    # 将实际端口发布到按 pid 隔离的端口文件（macOS App 等待该文件，
    # 然后用该端口轮询 /healthz）。在服务启动*之前*写入，使 App 在
    # 端口被自动更换后也能找到真实端口。
    _write_port_file(port)

    # ------------------------------------------------------------------
    # 9. Serve.
    # 9. 启动服务。
    # ------------------------------------------------------------------
    try:
        _serve(app, host, port, server=server_driver)
    except OSError as exc:
        # Port-in-use already has a targeted message in _serve.
        # 端口被占用的情况已在 _serve 中有针对性的消息。
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


__all__ = ["create_app", "main", "parse_args", "resolve_server_driver"]
