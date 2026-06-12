"""Flask application factory and ``main()`` entry point.

The factory pattern (``create_app``) makes the foundation easy to
embed in tests (the test suite calls ``create_app(testing=True)``)
and in production (where the same factory is used to spin up the
real WSGI server).
"""

from __future__ import annotations

import os
from typing import Any

from flask import Flask

from xijian_api import auth
from xijian_api.config import Config, DEFAULT_HOST
from xijian_api.errors import register_error_handlers
from xijian_api.handshake import register_healthz
from xijian_api.middleware import install_middleware
from xijian_api.routes import register_routes
from xijian_api.utils.log import configure_logging, get_logger


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
# main() — production-style startup
# ---------------------------------------------------------------------------


def _require_port() -> int:
    """Return the configured port, raising if it is missing or invalid."""
    raw = os.environ.get("XIJIAN_API_PORT")
    if not raw:
        raise SystemExit("XIJIAN_API_PORT is required")
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit(f"XIJIAN_API_PORT must be an integer, got {raw!r}") from exc
    if not (0 < port < 65536):
        raise SystemExit(f"XIJIAN_API_PORT out of range: {port}")
    return port


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
            "waitress not installed; falling back to Flask dev server "
            "(not for production)"
        )
        # ``threaded=True`` so the test client / curl smoke checks
        # don't deadlock under load.
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        return

    _LOGGER.info("serving via waitress on %s:%s", host, port)
    serve(app, host=host, port=port, ident="xijian-api")


def main(argv: list[str] | None = None) -> int:
    """Production-style entry point.

    Parses ``XIJIAN_API_PORT`` from the environment, creates the app,
    and starts a WSGI server.  Returns the process exit code.
    """
    configure_logging()
    port = _require_port()
    config = Config.from_env(testing=False)
    app = create_app(testing=False, config=config)
    _serve(app, DEFAULT_HOST, port)
    return 0


__all__ = ["create_app", "main"]