"""Route registration entry point.

This module exposes :func:`register_routes` which the app factory
calls after middleware is installed.  We always register ``root``;
every other route module is optional so that downstream tasks
(``oai-routes``, ``xijian-routes``, ``websocket``) can land
independently without breaking the foundation build.

A missing route module is logged as a warning — never raised —
because the foundation deliverable must remain importable and
runnable on its own.
"""

from __future__ import annotations

import importlib
from typing import Iterable

from flask import Flask

from xijian_api.routes.root import root_bp
from xijian_api.utils.log import get_logger

_LOGGER = get_logger()


#: Optional route modules that may or may not exist on disk.  Each is
#: imported via :func:`importlib.import_module`; if the import fails
#: for any reason (module not yet implemented, syntax error in a
#: sibling task's WIP, missing dependency) we log a warning and move
#: on.  The order doesn't matter — Flask blueprints attach their
#: routes when registered, so duplicate paths across modules will
#: raise at registration time, not import time.
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
    "xijian_api.routes.xijian_events",
    "xijian_api.routes.xijian_memory",
    "xijian_api.routes.xijian_protection",
    "xijian_api.routes.xijian_sessions",
    "xijian_api.routes.xijian_settings",
    "xijian_api.routes.xijian_resources",
    "xijian_api.routes.xijian_generation",
    "xijian_api.routes.xijian_overload",
    "xijian_api.routes.xijian_scenes",
    "xijian_api.routes.ws_routes",
)


def register_routes(app: Flask, *, optional_modules: Iterable[str] | None = None) -> None:
    """Register every available blueprint on ``app``.

    Always installs the root blueprint.  Iterates through the optional
    module list, importing each in turn.  Any ``ImportError`` (or
    ``ModuleNotFoundError``, which is an ``ImportError`` subclass) is
    logged but never re-raised so the foundation build stays green
    even if no other worker has landed their routes yet.

    Parameters
    ----------
    app:
        The Flask app to install the blueprints on.
    optional_modules:
        Override the default list of optional modules — useful for
        tests that want to inject a different ordering or skip a
        module on purpose.
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