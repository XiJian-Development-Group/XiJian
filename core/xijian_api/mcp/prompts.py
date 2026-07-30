"""MCP prompts — 可复用的 XiJian 工作流提示词模板。

提示词是带参数的模板，模型（或桌面客户端用户）可用特定参数渲染。
封装了角色创建、世界构建、记忆回忆等常见任务的最佳实践指令模式。
"""

from __future__ import annotations

from typing import Any


_PROMPTS: dict[str, dict[str, Any]] = {}


def _register(
    name: str,
    description: str,
    arguments: list[dict[str, Any]],
    builder,
) -> None:
    _PROMPTS[name] = {
        "name": name,
        "description": description,
        "arguments": arguments,
        "_builder": builder,
    }


def list_prompts() -> list[dict[str, Any]]:
    """返回所有已注册提示词的规格 (不含构建器)。"""
    _seed_prompts()
    return [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in _PROMPTS.values()
    ]


def get_prompt(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """按名称渲染提示词。

    返回 ``{"messages": [{"role": ..., "content": {...}}]}``。
    若提示词不存在，抛出 ``ValueError``。
    """
    _seed_prompts()
    record = _PROMPTS.get(name)
    if record is None:
        raise ValueError("unknown prompt: %s" % name)
    builder = record["_builder"]
    messages = builder(arguments or {})
    return {"messages": messages}


# ---------------------------------------------------------------------------
# Prompt builders / 提示词构建器
# ---------------------------------------------------------------------------


def _build_character_setup(args: dict[str, Any]) -> list[dict[str, Any]]:
    name = args.get("name", "新角色")
    persona = args.get("persona", "")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    "请帮我创建一个角色，名字叫「%s」。\n"
                    "人设概述：%s\n\n"
                    "请补充以下信息：\n"
                    "1. 性格特点（3-5 个关键词）\n"
                    "2. 语音风格描述\n"
                    "3. 默认情绪\n"
                    "4. 标签\n"
                    "然后用 character_create 工具创建。"
                    % (name, persona or "（待补充）")
                ),
            },
        }
    ]


def _build_world_setup(args: dict[str, Any]) -> list[dict[str, Any]]:
    name = args.get("name", "新世界")
    theme = args.get("theme", "")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    "请帮我创建一个世界，名字叫「%s」。\n"
                    "主题/风格：%s\n\n"
                    "请补充以下信息：\n"
                    "1. 世界设定概述\n"
                    "2. 主要场景（至少 3 个 POI）\n"
                    "3. 出场 NPC（至少 2 个）\n"
                    "4. 经济体系（货币、初始资金）\n"
                    "然后用 world_create 工具创建，并依次创建 NPC 和 POI。"
                    % (name, theme or "（待补充）")
                ),
            },
        }
    ]


def _build_memory_recall(args: dict[str, Any]) -> list[dict[str, Any]]:
    character_id = args.get("character_id", "")
    topic = args.get("topic", "")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    "请回忆角色 %s 关于「%s」的记忆。\n"
                    "使用 memory_search 工具检索相关记忆，"
                    "然后在回复中引用检索到的 entry_id。"
                    % (character_id or "（当前角色）", topic or "近期事件")
                ),
            },
        }
    ]


def _build_npc_tick(args: dict[str, Any]) -> list[dict[str, Any]]:
    world_id = args.get("world_id", "")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    "请推进世界 %s 的时间。\n"
                    "1. 使用 npc_tick_world 工具执行 NPC 调度\n"
                    "2. 检查是否有事件触发\n"
                    "3. 总结本 tick 发生的变化"
                    % (world_id or "（当前世界）")
                ),
            },
        }
    ]


# ---------------------------------------------------------------------------
# Seed / 种子数据
# ---------------------------------------------------------------------------


def _seed_prompts() -> None:
    if _PROMPTS:
        return
    _register(
        "character_setup",
        "引导模型创建一个新角色，补充人设细节后调用工具创建。",
        [
            {"name": "name", "description": "角色名字", "required": False},
            {"name": "persona", "description": "人设概述", "required": False},
        ],
        _build_character_setup,
    )
    _register(
        "world_setup",
        "引导模型创建一个新世界，包括场景、NPC 和经济体系。",
        [
            {"name": "name", "description": "世界名字", "required": False},
            {"name": "theme", "description": "主题/风格", "required": False},
        ],
        _build_world_setup,
    )
    _register(
        "memory_recall",
        "引导模型检索角色记忆并引用真实 entry_id。",
        [
            {"name": "character_id", "description": "角色 ID", "required": False},
            {"name": "topic", "description": "回忆主题", "required": False},
        ],
        _build_memory_recall,
    )
    _register(
        "npc_tick",
        "引导模型推进世界时间，执行 NPC 调度并总结变化。",
        [
            {"name": "world_id", "description": "世界 ID", "required": False},
        ],
        _build_npc_tick,
    )


__all__ = ["list_prompts", "get_prompt"]