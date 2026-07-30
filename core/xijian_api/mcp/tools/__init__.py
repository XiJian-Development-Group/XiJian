"""MCP tool modules — domain tools that wrap the XiJian stubs.
MCP 工具模块 — 封装隙间桩层的领域工具。

Every module in this package registers its tools at import time
via :func:`xijian_api.mcp.registry.register_tool`.  The
``__init__`` imports every module so that simply importing
``xijian_api.mcp.tools`` makes every tool available.
本包中的每个模块在导入时通过 :func:`xijian_api.mcp.registry.register_tool` 注册其工具。
``__init__`` 导入每个模块，使只需导入 ``xijian_api.mcp.tools`` 即可使用所有工具。

Tool naming convention: ``<domain>_<action>``
(e.g. ``character_create``, ``world_list``, ``memory_search``).
工具命名约定：``<domain>_<action>``
(如 ``character_create``、``world_list``、``memory_search``)。
"""

from __future__ import annotations

# Import every tool module so registration side-effects run.
# The order doesn't matter — each module is self-contained.
# 导入每个工具模块以触发注册副作用。顺序无关紧要 —— 每个模块自包含。
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