"""Route registration entry point. / 路由注册入口点。

This module exposes :func:`register_routes` which the app factory
calls after middleware is installed. We always register ``root``;
every other route module is optional so that downstream tasks
(``oai-routes``, ``xijian-routes``, ``websocket``) can land
independently without breaking the foundation build.

本模块暴露 :func:`register_routes`，由应用工厂在安装中间件后调用。我们总是注册 ``root``；
其他每个路由模块都是可选的，以便下游任务
(``oai-routes``, ``xijian-routes``, ``websocket``) 能独立落地而不破坏基础构建。

A missing route module is logged as a warning — never raised —
because the foundation deliverable must remain importable and
runnable on its own.

缺失的路由模块仅记录警告——绝不抛出异常——
因为基础交付件必须保持可导入且可独立运行。
"""

from __future__ import annotations

import importlib
from typing import Iterable

from flask import Flask

from xijian_api.routes.root import root_bp
from xijian_api.utils.log import get_logger

_LOGGER = get_logger()


#: Optional route modules that may or may not exist on disk. Each is
#: imported via :func:`importlib.import_module`; if the import fails
#: for any reason (module not yet implemented, syntax error in a
#: sibling task's WIP, missing dependency) we log a warning and move
#: on. The order doesn't matter — Flask blueprints attach their
#: routes when registered, so duplicate paths across modules will
#: raise at registration time, not import time.
#: 可选路由模块，可能存在也可能不存在于磁盘上。每个都通过
#: :func:`importlib.import_module` 导入；如果导入因任何原因失败
#: (模块尚未实现、同事 WIP 中的语法错误、缺少依赖) 我们记录警告并继续。
#: 顺序不重要——Flask 蓝图在注册时附加路由，因此跨模块的重复路径
#: 会在注册时而非导入时抛出异常。
_OPTIONAL_ROUTE_MODULES: tuple[str, ...] = (
    "xijian_api.routes.models",
    "xijian_api.routes.chat",
    "xijian_api.routes.completions",
    "xijian_api.routes.embeddings",
    "xijian_api.routes.audio",
    "xijian_api.routes.images",
    "xijian_api.routes.videos",
    "xijian_api.routes.files",
    "xijian_api.routes.batches",
    "xijian_api.routes.fine_tuning",
    "xijian_api.routes.assistants",
    "xijian_api.routes.xijian_characters",
    "xijian_api.routes.xijian_interactions",
    "xijian_api.routes.xijian_worlds",
    "xijian_api.routes.xijian_npcs",
    "xijian_api.routes.xijian_economy",
    "xijian_api.routes.xijian_events",
    "xijian_api.routes.xijian_memory",
    "xijian_api.routes.xijian_sessions",
    "xijian_api.routes.xijian_settings",
    "xijian_api.routes.xijian_resources",
    "xijian_api.routes.xijian_generation",
    "xijian_api.routes.xijian_overload",
    "xijian_api.routes.xijian_safety",
    "xijian_api.routes.xijian_mcp",
    "xijian_api.routes.mcp_server",
    "xijian_api.routes.xijian_backups",
    "xijian_api.routes.xijian_manual_backups",
    "xijian_api.routes.xijian_scenes",
    "xijian_api.routes.ws_routes",
    "xijian_api.routes.xijian_devkit",
    "xijian_api.routes.xijian_plot",
    "xijian_api.routes.multimodal",
    # A6 / A7 / A8 modules (added 2026-08-01).
    "xijian_api.routes.xijian_voice_calls",
    "xijian_api.routes.xijian_initiated",
    "xijian_api.routes.xijian_desktop",
    # 存储统一迁移 / 资源包系统 (added 2026-08-03).
    "xijian_api.routes.xijian_migration",
    "xijian_api.routes.xijian_packs",
)


def register_routes(app: Flask, *, optional_modules: Iterable[str] | None = None) -> None:
    """Register every available blueprint on ``app``.

    Always installs the root blueprint. Iterates through the optional
    module list, importing each in turn. Any ``ImportError`` (or
    ``ModuleNotFoundError``, which is an ``ImportError`` subclass) is
    logged but never re-raised so the foundation build stays green
    even if no other worker has landed their routes yet.

    在 ``app`` 上注册所有可用的蓝图。

    总是安装根蓝图。遍历可选模块列表，依次导入每个。任何
    ``ImportError`` (或 ``ModuleNotFoundError``，它是 ``ImportError`` 的子类)
    都会被记录但绝不重新抛出，这样即使没有其他工作人员落地他们的路由，
    基础构建也能保持绿色。

    Parameters
    ----------
    app:
        The Flask app to install the blueprints on. / 要安装蓝图的 Flask 应用。
    optional_modules:
        Override the default list of optional modules — useful for
        tests that want to inject a different ordering or skip a
        module on purpose.
        覆盖默认的可选模块列表——对想要注入不同顺序或故意跳过模块的测试很有用。
    """
    app.register_blueprint(root_bp)
    _LOGGER.info("registered blueprint: root")

    modules = tuple(optional_modules) if optional_modules is not None else _OPTIONAL_ROUTE_MODULES
    for module_name in modules:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            _LOGGER.warning("optional route module %s unavailable: %s", module_name, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — broad catch is intentional
            _LOGGER.warning(
                "optional route module %s failed to import (%s): %s",
                module_name,
                type(exc).__name__,
                exc,
            )
            continue

        blueprint = getattr(module, "bp", None)
        if blueprint is None:
            _LOGGER.warning(
                "optional route module %s has no `bp` attribute; skipping",
                module_name,
            )
            continue

        try:
            app.register_blueprint(blueprint)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "optional route module %s failed to register (%s): %s",
                module_name,
                type(exc).__name__,
                exc,
            )
            continue

        # Some modules (notably the WebSocket handler) need an explicit
        # ``init_app`` step to attach their routes to a Sock instance.
        # 某些模块（尤其是 WebSocket 处理器）需要显式的
        # ``init_app`` 步骤将它们的路由附加到 Sock 实例。
        init_app_fn = getattr(module, "init_app", None)
        if callable(init_app_fn):
            try:
                init_app_fn(app)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "optional route module %s init_app failed (%s): %s",
                    module_name,
                    type(exc).__name__,
                    exc,
                )
        _LOGGER.info("registered blueprint: %s", module_name)


__all__ = ["register_routes"]