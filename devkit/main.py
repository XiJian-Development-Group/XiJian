"""Pywebview entry point for the Developer Kit.

Run with::

    python -m devkit                                  # open the window
    xijian-devkit                                     # same, via console_scripts

CLI flags::

    --smtp-host HOST     override XIJIAN_DEV_SMTP_HOST
    --smtp-port PORT     override XIJIAN_DEV_SMTP_PORT (int)
    --no-smtp-tls        disable STARTTLS
    --smtp-user USER     override SMTP auth user
    --recipient ADDR     override recipient address
    --width N            window width (default 1280)
    --height N           window height (default 820)
    --headless           skip start(); just print the resolved config and exit

The DevKit is intentionally *standalone* — it is its own top-level
``devkit`` package, does not import ``xijian_api`` at all, never opens
a Flask server, and never reads the main ``Config`` object.  The window
you get here is the only thing that runs, which is what lets it ship as
a self-contained PyInstaller binary (function list v2.3, C5).

Why pywebview + local HTTP server rather than a Flask blueprint
---------------------------------------------------------------

* 0 http surface for the *application* logic (function list v2.2 requirement).
* Cross-platform: pywebview picks the native webview on macOS
  (``WKWebView``), Windows (``WebView2``) and Linux (``webkitgtk``).
* Direct ``window.pywebview.api.<method>()`` calls from JS — no
  JSON envelopes, no CORS, no auth header.
* Local HTTP server only serves static UI assets (HTML/JS/CSS/vendor),
  avoiding WKWebView's strict ``file://`` CORS restrictions.

Failure modes
-------------

* If ``pywebview`` is not installed, :func:`run` raises a clear
  ``RuntimeError`` pointing the user at ``pip install pywebview``.
* If the GUI toolkit can't open (e.g. headless CI), ``pywebview``
  itself raises — we let it bubble so the operator sees the real
  error.
"""

from __future__ import annotations

import argparse
import http.server
import logging
import os
import socket
import sys
import threading
from typing import Any, Sequence

from devkit.api import DevKitApi
from devkit import (
    DEV_SUBMIT_RECIPIENT,
    DEV_SUBMIT_SMTP_HOST,
    DEV_SUBMIT_SMTP_PORT,
    DEV_SUBMIT_SMTP_USE_TLS,
    DEV_SUBMIT_SMTP_USER,
    ui_dir,
)

_LOGGER = logging.getLogger("devkit.main")


#: Default window geometry.  Picked to fit a 13" laptop screen with
#: a small margin; user-resizable at runtime.
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 820


class _UIHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve the DevKit UI directory with no caching (dev-friendly)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ui_dir()), **kwargs)

    def end_headers(self):
        # Disable caching so devs see JS/CSS changes immediately on reload.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Quiet the default "GET / HTTP/1.1" logs; use our logger instead.
        _LOGGER.debug("%s - %s", self.address_string(), format % args)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _UIServer:
    """Background HTTP server for the DevKit UI assets."""

    def __init__(self, port: int):
        self._port = port
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), _UIHTTPRequestHandler
        )
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        _LOGGER.info("UI HTTP server listening on http://127.0.0.1:%d", self._port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(argv: Sequence[str] | None = None) -> int:
    """Start the DevKit window and block until the user closes it.

    Parameters
    ----------
    argv:
        CLI arguments (``--smtp-host foo --no-smtp-tls ...``).
        When ``None`` falls back to ``sys.argv[1:]``.
    """
    args = _parse_args(argv)

    # Apply CLI overrides onto the module-level constants BEFORE the
    # UI is constructed, so :func:`DevKitApi.whoami` returns them.
    if args.smtp_host:
        os.environ["XIJIAN_DEV_SMTP_HOST"] = args.smtp_host
        import devkit as _devkit_mod

        _devkit_mod.DEV_SUBMIT_SMTP_HOST = args.smtp_host
    if args.smtp_port is not None:
        os.environ["XIJIAN_DEV_SMTP_PORT"] = str(args.smtp_port)
        import devkit as _devkit_mod

        _devkit_mod.DEV_SUBMIT_SMTP_PORT = args.smtp_port
    if args.no_smtp_tls:
        os.environ["XIJIAN_DEV_SMTP_USE_TLS"] = "0"
        import devkit as _devkit_mod

        _devkit_mod.DEV_SUBMIT_SMTP_USE_TLS = False
    if args.smtp_user:
        os.environ["XIJIAN_DEV_SMTP_USER"] = args.smtp_user
        import devkit as _devkit_mod

        _devkit_mod.DEV_SUBMIT_SMTP_USER = args.smtp_user
    if args.recipient:
        os.environ["XIJIAN_DEV_RECIPIENT"] = args.recipient
        import devkit as _devkit_mod

        _devkit_mod.DEV_SUBMIT_RECIPIENT = args.recipient

    if args.headless:
        _print_config()
        return 0

    try:
        import webview  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — depends on environment
        raise RuntimeError(
            "pywebview is required to launch the DevKit window.\n"
            "Install it with: pip install pywebview\n"
            f"Original error: {exc}"
        ) from exc

    from devkit import state as _dk_state

    # Load persisted state (submissions, cooldowns, last session) BEFORE
    # constructing the API so the window restores the previous login and
    # the per-developer submit cooldown survives a restart.
    work_dir = DevKitApi()._work_dir()
    _dk_state.load(work_dir)
    api = DevKitApi()
    if api._active_developer:
        _LOGGER.info("restored DevKit session for developer %s", api._active_developer)
    _LOGGER.info("starting DevKit window (%sx%s)", args.width, args.height)

    # Start local HTTP server for UI assets (avoids file:// CORS issues on WKWebView)
    port = _pick_free_port()
    ui_server = _UIServer(port)
    ui_server.start()

    try:
        webview.create_window(
            title="隙间 · 开发者工具",
            url=f"http://127.0.0.1:{port}/index.html",
            width=args.width,
            height=args.height,
            resizable=True,
            js_api=api,
            confirm_close=True,
            text_select=True,
        )
        webview.start(debug=False) # Use "debug=True" while debugging
    finally:
        ui_server.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; sets up logging then calls :func:`run`."""
    logging.basicConfig(
        level=os.environ.get("XIJIAN_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return run(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ui_url() -> str:
    """Return the URL the window should load.

    pywebview's ``create_window`` accepts either a URL or a local
    path; we hand it a ``file://`` URL pointing at ``ui/index.html``
    inside the package so the DevKit ships as a single wheel.

    When the package is frozen by PyInstaller, :func:`ui_dir` resolves
    to ``sys._MEIPASS`` automatically — no special-casing here.
    """
    here = ui_dir() / "index.html"
    if not here.is_file():  # pragma: no cover — packaging sanity
        raise RuntimeError(f"DevKit ui/index.html not found at {here!s}")
    # pywebview's load_url wants a file:// URL it can read.
    return here.as_uri()


def _print_config() -> None:
    """Dump the resolved DevKit config to stdout (for `xijian-devkit --headless`)."""
    cfg: dict[str, Any] = {
        "smtp_host": DEV_SUBMIT_SMTP_HOST,
        "smtp_port": int(DEV_SUBMIT_SMTP_PORT),
        "smtp_use_tls": bool(DEV_SUBMIT_SMTP_USE_TLS),
        "smtp_user": DEV_SUBMIT_SMTP_USER,
        "smtp_password_set": bool(
            os.environ.get("XIJIAN_DEV_SMTP_PASSWORD") and
            os.environ.get("XIJIAN_DEV_SMTP_PASSWORD") != "REPLACE_BEFORE_DEPLOY"
        ),
        "recipient": DEV_SUBMIT_RECIPIENT,
    }
    import json as _json

    print(_json.dumps(cfg, ensure_ascii=False, indent=2))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the CLI; isolated so tests can exercise it without pywebview."""
    parser = argparse.ArgumentParser(
        prog="xijian-devkit",
        description="Launch the 隙间 Developer Kit (Pywebview window).",
    )
    parser.add_argument(
        "--smtp-host",
        default=None,
        help="Override XIJIAN_DEV_SMTP_HOST (and the value rendered in the UI).",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=None,
        help="Override XIJIAN_DEV_SMTP_PORT.",
    )
    parser.add_argument(
        "--no-smtp-tls",
        action="store_true",
        help="Disable STARTTLS on the SMTP connection.",
    )
    parser.add_argument(
        "--smtp-user",
        default=None,
        help="Override SMTP auth user (XIJIAN_DEV_SMTP_USER).",
    )
    parser.add_argument(
        "--recipient",
        default=None,
        help="Override the developer-group recipient address.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"Window width in pixels (default {DEFAULT_WIDTH}).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help=f"Window height in pixels (default {DEFAULT_HEIGHT}).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Don't open the window — print the resolved config and exit.",
    )
    ns = parser.parse_args(list(argv) if argv is not None else None)
    if ns.width <= 0 or ns.height <= 0:
        raise SystemExit("--width / --height must be positive")
    return ns


__all__ = ["run", "main", "DEFAULT_WIDTH", "DEFAULT_HEIGHT"]


if __name__ == "__main__":  # pragma: no cover — script execution
    raise SystemExit(main(sys.argv[1:]))
