"""开发者工具的 Pywebview 入口点。

运行方式::

    python -m devkit                                  # 打开窗口
    xijian-devkit                                     # 同上，通过 console_scripts

CLI 标志::

    --smtp-host HOST     覆盖 XIJIAN_DEV_SMTP_HOST
    --smtp-port PORT     覆盖 XIJIAN_DEV_SMTP_PORT（整数）
    --no-smtp-tls        禁用 STARTTLS
    --smtp-user USER     覆盖 SMTP 认证用户
    --recipient ADDR     覆盖收件人地址
    --width N            窗口宽度（默认 1280）
    --height N           窗口高度（默认 820）
    --headless           跳过 start()；仅打印解析后的配置并退出

DevKit **刻意保持独立** —— 它是自己的顶层 ``devkit`` 包，完全不导入
``xijian_api``，从不开启 Flask 服务器，也从不读取主 ``Config`` 对象。
这里得到的窗口是唯一运行的东西，这正是它能作为自包含 PyInstaller
二进制包发布的原因（功能清单 v2.3，C5）。

为什么用 pywebview + 本地 HTTP 服务器而不是 Flask 蓝图
---------------------------------------------------------------

* *应用*逻辑的 HTTP 暴露面为 0（功能清单 v2.2 要求）。
* 跨平台：pywebview 在 macOS 上选择原生 webview
  （``WKWebView``），Windows 上为 ``WebView2``，Linux 上为 ``webkitgtk``。
* 从 JS 直接调用 ``window.pywebview.api.<method>()`` —— 没有
  JSON 信封、没有 CORS、没有认证头。
* 本地 HTTP 服务器只提供静态 UI 资源（HTML/JS/CSS/vendor），
  避免 WKWebView 严格的 ``file://`` CORS 限制。

失败模式
-------------

* 如果未安装 ``pywebview``，:func:`run` 会抛出清晰的
  ``RuntimeError``，引导用户执行 ``pip install pywebview``。
* 如果 GUI 工具包无法打开（例如无头 CI），``pywebview``
  自身会抛出异常——我们让它向上冒泡，以便操作者看到真实错误。
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


#: 默认窗口几何尺寸。选取为适合 13 英寸笔记本屏幕并留少量边距；
#: 运行时用户可调整大小。
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 820


class _UIHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """提供 DevKit UI 目录，不缓存（对开发友好）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ui_dir()), **kwargs)

    def end_headers(self):
        # 禁用缓存，让开发者刷新后立即可见 JS/CSS 更改。
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # 静默默认的 "GET / HTTP/1.1" 日志；改用我们自己的日志器。
        _LOGGER.debug("%s - %s", self.address_string(), format % args)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _UIServer:
    """DevKit UI 资源的后台 HTTP 服务器。"""

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
# 公共入口点
# ---------------------------------------------------------------------------


def run(argv: Sequence[str] | None = None) -> int:
    """启动 DevKit 窗口并阻塞直到用户关闭它。

    参数
    ----------
    argv:
        CLI 参数（``--smtp-host foo --no-smtp-tls ...``）。
        为 ``None`` 时回退到 ``sys.argv[1:]``。
    """
    args = _parse_args(argv)

    # 在构造 UI **之前**将 CLI 覆盖应用到模块级常量上，
    # 以便 :func:`DevKitApi.whoami` 能返回它们。
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
        # 提交收件人在代码中硬编码，无法被覆盖（配置文件或 CLI 都不行）。
        # 静默忽略此标志。
        import sys as _sys

        print(
            "warning: --recipient is ignored; the submission recipient is fixed in code.",
            file=_sys.stderr,
        )

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

    # 在构造 API 之前加载持久化状态（提交、冷却、上次会话），
    # 以便窗口恢复之前的登录状态，且每个开发者的提交冷却
    # 在重启后仍然有效。
    work_dir = DevKitApi()._work_dir()
    _dk_state.load(work_dir)
    api = DevKitApi()
    if api._active_developer:
        _LOGGER.info("restored DevKit session for developer %s", api._active_developer)
    _LOGGER.info("starting DevKit window (%sx%s)", args.width, args.height)

    # 为 UI 资源启动本地 HTTP 服务器（避免 WKWebView 上的 file:// CORS 问题）
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
        webview.start(debug=False) # 调试时使用 "debug=True"
    finally:
        ui_server.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口点；设置日志后调用 :func:`run`。"""
    logging.basicConfig(
        level=os.environ.get("XIJIAN_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return run(argv)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _ui_url() -> str:
    """返回窗口应加载的 URL。

    pywebview 的 ``create_window`` 接受 URL 或本地路径；我们传入
    指向包内 ``ui/index.html`` 的 ``file://`` URL，使 DevKit
    以单个 wheel 形式发布。

    当包被 PyInstaller 冻结时，:func:`ui_dir` 会自动解析到
    ``sys._MEIPASS`` —— 这里无需特殊处理。
    """
    here = ui_dir() / "index.html"
    if not here.is_file():  # pragma: no cover — packaging sanity
        raise RuntimeError(f"DevKit ui/index.html not found at {here!s}")
    # pywebview 的 load_url 需要一个它可读取的 file:// URL。
    return here.as_uri()


def _print_config() -> None:
    """将解析后的 DevKit 配置输出到 stdout（供 `xijian-devkit --headless` 使用）。"""
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
    """解析 CLI；隔离出来以便测试无需 pywebview 即可运行。"""
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
