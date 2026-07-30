"""XiJian Flask API server — package init.

隙间 Flask API 服务 — 包初始化。

Exposes :func:`create_app` and the package :data:`__version__` for
external callers (entry points, tests).

对外暴露 :func:`create_app` 和包版本号 :data:`__version__`，供外部调用者（入口点、测试）使用。
"""

from __future__ import annotations

__version__ = "0.1.0"

from xijian_api.app import create_app, main

__all__ = ["__version__", "create_app", "main"]
