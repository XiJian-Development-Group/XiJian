"""XiJian Flask API server — package init.

Exposes :func:`create_app` and the package :data:`__version__` for
external callers (entry points, tests).
"""

from __future__ import annotations

__version__ = "0.1.0"

from xijian_api.app import create_app, main

__all__ = ["__version__", "create_app", "main"]