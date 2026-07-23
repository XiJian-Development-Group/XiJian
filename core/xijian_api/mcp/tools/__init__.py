"""MCP tool modules — domain tools that wrap the XiJian stubs.

Every module in this package registers its tools at import time
via :func:`xijian_api.mcp.registry.register_tool`.  The
``__init__`` imports every module so that simply importing
``xijian_api.mcp.tools`` makes every tool available.

Tool naming convention: ``<domain>_<action>``
(e.g. ``character_create``, ``world_list``, ``memory_search``).
"""

from __future__ import annotations

# Import every tool module so registration side-effects run.
# The order doesn't matter — each module is self-contained.
from xijian_api.mcp.tools import (  # noqa: F401
    characters,
    desktop,
    economy,
    events,
    files,
    memory,
    npcs,
    protection,
    sessions,
    settings,
    worlds,
)

__all__: list[str] = []
