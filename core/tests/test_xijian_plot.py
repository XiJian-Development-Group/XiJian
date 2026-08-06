"""针对 plot_runtime stub 模块的测试。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from xijian_api.stubs import plot_runtime as plot_stub
from xijian_api.stubs import worlds as worlds_stub
from xijian_api.stubs import state


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state():
    """每个测试之前重置状态。"""
    state.reset_for_testing()
    yield
    state.reset_for_testing()


@pytest.fixture
def temp_work_dir():
    """创建一个带有示例剧情数据的临时 devkit 工作目录。"""
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        plots_dir = work_dir / "plots"
        plots_dir.mkdir(parents=True)

        # 创建一个示例剧情
        plot_id = "plot_test_001"
        plot_dir = plots_dir / plot_id
        plot_dir.mkdir()

        # plot.json
        plot_meta = {
            "id": plot_id,
            "name": "测试剧情",
            "description": "用于测试的剧情",
            "genre": "测试",
            "setting": "测试世界",
            "tags": ["test"],
            "status": "draft",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        with open(plot_dir / "plot.json", "w", encoding="utf-8") as f:
            json.dump(plot_meta, f, ensure_ascii=False)

        # nodes.json
        nodes = [
            {
                "id": "node_start",
                "plot_id": plot_id,
                "type": "start",
                "title": "开始",
                "description": "剧情起点",
                "position": {"x": 0, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
            {
                "id": "node_event_1",
                "plot_id": plot_id,
                "type": "event",
                "title": "事件节点",
                "description": "一个事件节点",
                "position": {"x": 100, "y": 0},
                "trigger": {"type": "condition", "field": "quest_stage", "op": "eq", "value": 1},
                "rewards": [
                    {"type": "currency", "currency_id": "currency_gold", "amount": 100}
                ],
                "effects": [
                    {"type": "plot_variable", "key": "quest_stage", "value": 2}
                ],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
            {
                "id": "node_choice",
                "plot_id": plot_id,
                "type": "choice",
                "title": "选择",
                "description": "玩家选择分支",
                "position": {"x": 200, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
            {
                "id": "node_end",
                "plot_id": plot_id,
                "type": "end",
                "title": "结束",
                "description": "剧情终点",
                "position": {"x": 300, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
        ]
        with open(plot_dir / "nodes.json", "w", encoding="utf-8") as f:
            json.dump(nodes, f, ensure_ascii=False)

        # edges.json
        edges = [
            {
                "id": "edge_1",
                "plot_id": plot_id,
                "source": "node_start",
                "target": "node_event_1",
                "condition": None,
                "label": "",
            },
            {
                "id": "edge_2",
                "plot_id": plot_id,
                "source": "node_event_1",
                "target": "node_choice",
                "condition": None,
                "label": "",
            },
            {
                "id": "edge_3a",
                "plot_id": plot_id,
                "source": "node_choice",
                "target": "node_end",
                "condition": {"field": "player_choice", "op": "eq", "value": "accept"},
                "label": "接受",
            },
            {
                "id": "edge_3b",
                "plot_id": plot_id,
                "source": "node_choice",
                "target": "node_end",
                "condition": {"field": "player_choice", "op": "eq", "value": "reject"},
                "label": "拒绝",
            },
        ]
        with open(plot_dir / "edges.json", "w", encoding="utf-8") as f:
            json.dump(edges, f, ensure_ascii=False)

        # 通过真实的 worlds stub API 创建一个世界。
        worlds_stub.create(world_id="world_test", name="测试世界")

        yield str(work_dir)


# ---------------------------------------------------------------------------
# 测试：list_available_plots
# ---------------------------------------------------------------------------


def test_list_available_plots(temp_work_dir):
    plots = plot_stub.list_available_plots(temp_work_dir)
    assert len(plots) == 1
    assert plots[0]["id"] == "plot_test_001"
    assert plots[0]["name"] == "测试剧情"


def test_list_available_plots_empty_dir(tmp_path):
    plots = plot_stub.list_available_plots(str(tmp_path))
    assert plots == []


# ---------------------------------------------------------------------------
# 测试：get_plot_design
# ---------------------------------------------------------------------------


def test_get_plot_design(temp_work_dir):
    design = plot_stub.get_plot_design(temp_work_dir, "plot_test_001")
    assert design is not None
    assert design["id"] == "plot_test_001"
    assert design["name"] == "测试剧情"

    # 未找到
    assert plot_stub.get_plot_design(temp_work_dir, "nonexistent") is None


# ---------------------------------------------------------------------------
# 测试：get_plot_design_nodes / edges
# ---------------------------------------------------------------------------


def test_get_plot_design_nodes(temp_work_dir):
    nodes = plot_stub.get_plot_design_nodes(temp_work_dir, "plot_test_001")
    assert len(nodes) == 4
    assert nodes[0]["id"] == "node_start"
    assert nodes[0]["type"] == "start"

    # 未找到
    assert plot_stub.get_plot_design_nodes(temp_work_dir, "nonexistent") == []


def test_get_plot_design_edges(temp_work_dir):
    edges = plot_stub.get_plot_design_edges(temp_work_dir, "plot_test_001")
    assert len(edges) == 4
    assert edges[0]["source"] == "node_start"
    assert edges[0]["target"] == "node_event_1"

    # 未找到
    assert plot_stub.get_plot_design_edges(temp_work_dir, "nonexistent") == []


# ---------------------------------------------------------------------------
# 测试：create_plot_runtime
# ---------------------------------------------------------------------------


def test_create_plot_runtime(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )
    assert rt["id"].startswith("plot_rt_")
    assert rt["plot_id"] == "plot_test_001"
    assert rt["world_id"] == "world_test"
    assert rt["current_node_id"] == "node_start"
    assert rt["status"] == plot_stub.PLOT_RUNTIME_STATUS_RUNNING
    assert rt["completed_nodes"] == []
    assert rt["unlocked_nodes"] == ["node_start"]


def test_create_plot_runtime_invalid_world(temp_work_dir):
    with pytest.raises(plot_stub.PlotError, match="世界不存在"):
        plot_stub.create_plot_runtime(
            plot_id="plot_test_001",
            world_id="nonexistent",
            work_dir=temp_work_dir,
        )


def test_create_plot_runtime_invalid_plot(temp_work_dir):
    with pytest.raises(plot_stub.PlotError, match="剧情不存在"):
        plot_stub.create_plot_runtime(
            plot_id="nonexistent",
            world_id="world_test",
            work_dir=temp_work_dir,
        )


def test_create_plot_runtime_with_initial_variables(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 1, "player_name": "测试玩家"},
    )
    assert rt["variables"]["quest_stage"] == 1
    assert rt["variables"]["player_name"] == "测试玩家"


# ---------------------------------------------------------------------------
# 测试：get_plot_runtime / list_plot_runtimes
# ---------------------------------------------------------------------------


def test_get_plot_runtime(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )
    fetched = plot_stub.get_plot_runtime(rt["id"])
    assert fetched is not None
    assert fetched["id"] == rt["id"]
    assert fetched["plot_id"] == "plot_test_001"

    # 未找到
    assert plot_stub.get_plot_runtime("nonexistent") is None


def test_list_plot_runtimes(temp_work_dir):
    rt1 = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )
    rt2 = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    all_rts = plot_stub.list_plot_runtimes()
    assert len(all_rts) == 2

    # 按 world_id 过滤
    filtered = plot_stub.list_plot_runtimes(world_id="world_test")
    assert len(filtered) == 2

    # 按状态过滤
    filtered = plot_stub.list_plot_runtimes(status=plot_stub.PLOT_RUNTIME_STATUS_RUNNING)
    assert len(filtered) == 2


# ---------------------------------------------------------------------------
# 测试：advance_plot_runtime（线性推进）
# ---------------------------------------------------------------------------


def test_advance_plot_runtime_linear(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    # 起始节点 → 事件节点
    result = plot_stub.advance_plot_runtime(rt["id"])
    assert result["current_node_id"] == "node_event_1"
    assert result["next_node_id"] == "node_event_1"
    assert "node_start" in result["completed_nodes"]
    assert len(result["executed_rewards"]) == 0  # 起始节点没有奖励

    # 事件节点 → 选择节点（有奖励和效果）
    result = plot_stub.advance_plot_runtime(rt["id"])
    assert result["current_node_id"] == "node_choice"
    assert "node_event_1" in result["completed_nodes"]
    # 检查奖励已执行
    assert len(result["executed_rewards"]) > 0
    # 检查效果已执行（plot_variable）
    assert any(e["type"] == "plot_variable" for e in result["executed_effects"])


def test_advance_plot_runtime_choice_node(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"player_choice": "accept"},
    )

    # 推进到选择节点
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    result = plot_stub.advance_plot_runtime(rt["id"])  # event -> choice

    # 在选择节点处，需要指定边
    with pytest.raises(plot_stub.PlotError, match="choice 节点必须指定 choose_edge_id"):
        plot_stub.advance_plot_runtime(rt["id"])

    # 选择 edge_3a（接受）
    result = plot_stub.advance_plot_runtime(rt["id"], choose_edge_id="edge_3a")
    assert result["current_node_id"] == "node_end"
    assert result["status"] == plot_stub.PLOT_RUNTIME_STATUS_COMPLETED


def test_advance_plot_runtime_choice_wrong_condition(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"player_choice": "reject"},
    )

    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    plot_stub.advance_plot_runtime(rt["id"])  # event -> choice

    # 尝试在条件不满足时选择 edge_3a
    with pytest.raises(plot_stub.PlotError, match="边条件不满足"):
        plot_stub.advance_plot_runtime(rt["id"], choose_edge_id="edge_3a")

    # 选择 edge_3b（拒绝）—— 应成功
    result = plot_stub.advance_plot_runtime(rt["id"], choose_edge_id="edge_3b")
    assert result["current_node_id"] == "node_end"


def test_advance_plot_runtime_invalid_runtime(temp_work_dir):
    with pytest.raises(plot_stub.PlotError, match="剧情运行时不存在"):
        plot_stub.advance_plot_runtime("nonexistent")


def test_advance_plot_runtime_not_running(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"player_choice": "accept"},
    )
    # 完成剧情
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    plot_stub.advance_plot_runtime(rt["id"])  # event -> choice
    plot_stub.advance_plot_runtime(rt["id"], choose_edge_id="edge_3a")  # choice -> end

    # 尝试推进已完成的剧情
    with pytest.raises(plot_stub.PlotError, match="不可推进"):
        plot_stub.advance_plot_runtime(rt["id"])


# ---------------------------------------------------------------------------
# 测试：暂停 / 恢复
# ---------------------------------------------------------------------------


def test_pause_resume_plot_runtime(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    # 暂停
    paused = plot_stub.pause_plot_runtime(rt["id"])
    assert paused["status"] == plot_stub.PLOT_RUNTIME_STATUS_PAUSED

    # 尝试推进已暂停的剧情
    with pytest.raises(plot_stub.PlotError, match="不可推进"):
        plot_stub.advance_plot_runtime(rt["id"])

    # 恢复
    resumed = plot_stub.resume_plot_runtime(rt["id"])
    assert resumed["status"] == plot_stub.PLOT_RUNTIME_STATUS_RUNNING

    # 现在应可推进
    result = plot_stub.advance_plot_runtime(rt["id"])
    assert result["current_node_id"] == "node_event_1"


def test_pause_non_running(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )
    plot_stub.pause_plot_runtime(rt["id"])

    # 尝试再次暂停
    with pytest.raises(plot_stub.PlotError, match="只能暂停运行中的剧情"):
        plot_stub.pause_plot_runtime(rt["id"])


# ---------------------------------------------------------------------------
# 测试：delete_plot_runtime
# ---------------------------------------------------------------------------


def test_delete_plot_runtime(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )
    assert plot_stub.delete_plot_runtime(rt["id"]) is True
    assert plot_stub.get_plot_runtime(rt["id"]) is None
    assert plot_stub.delete_plot_runtime(rt["id"]) is False  # 已删除


# ---------------------------------------------------------------------------
# 测试：get_plot_node / get_plot_edges
# ---------------------------------------------------------------------------


def test_get_plot_node(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    node = plot_stub.get_plot_node(rt["id"], "node_start")
    assert node is not None
    assert node["id"] == "node_start"
    assert node["is_current"] is True
    assert node["is_completed"] is False
    assert node["is_unlocked"] is True

    node = plot_stub.get_plot_node(rt["id"], "node_event_1")
    assert node["is_current"] is False
    assert node["is_unlocked"] is False

    # 未找到
    assert plot_stub.get_plot_node(rt["id"], "nonexistent") is None
    assert plot_stub.get_plot_node("nonexistent", "node_start") is None


def test_get_plot_edges(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    edges = plot_stub.get_plot_edges(rt["id"])
    assert len(edges) == 4

    edges_from_start = plot_stub.get_plot_edges(rt["id"], "node_start")
    assert len(edges_from_start) == 1
    assert edges_from_start[0]["target"] == "node_event_1"

    edges_from_choice = plot_stub.get_plot_edges(rt["id"], "node_choice")
    assert len(edges_from_choice) == 2


# ---------------------------------------------------------------------------
# 测试：evaluate_plot_triggers
# ---------------------------------------------------------------------------


def test_evaluate_plot_triggers_condition(temp_work_dir):
    """测试带条件触发器的触发器评估。"""
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 1},  # 条件：quest_stage == 1
    )

    # 当前节点是 start（无触发器），推进到带条件触发器的事件节点
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    # 现在当前节点是带条件触发器的 node_event_1
    # 手动设置变量使触发器匹配
    bucket = plot_stub._get_bucket()
    rt_state = bucket[rt["id"]]
    rt_state["variables"]["quest_stage"] = 1

    # 评估触发器
    activated = plot_stub.evaluate_plot_triggers("world_test")
    assert len(activated) == 1
    assert activated[0]["runtime_id"] == rt["id"]
    assert activated[0]["node_id"] == "node_event_1"
    assert activated[0]["new_node_id"] == "node_choice"


def test_evaluate_plot_triggers_no_match(temp_work_dir):
    """测试条件不匹配时的触发器评估。"""
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 99},  # 条件：quest_stage == 1（不会匹配）
    )

    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    activated = plot_stub.evaluate_plot_triggers("world_test")
    assert len(activated) == 0


def test_evaluate_plot_triggers_only_running(temp_work_dir):
    """测试只有运行中的 runtime 才会被评估。"""
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 1},
    )
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    # 暂停它
    plot_stub.pause_plot_runtime(rt["id"])

    activated = plot_stub.evaluate_plot_triggers("world_test")
    assert len(activated) == 0


# ---------------------------------------------------------------------------
# 测试：奖励执行（货币、物品、经验、关系）
# ---------------------------------------------------------------------------


def test_rewards_currency(temp_work_dir):
    """测试货币奖励的执行。"""
    from xijian_api.stubs import world_currencies as wc_stub
    from xijian_api.stubs import wallets as wallets_stub

    # 使用真实的 world_currencies/wallets API 设置货币和钱包。
    wc_stub.create(
        world_id="world_test",
        code="currency_gold",
        name="金币",
        symbol="货币",
        decimals=0,
    )
    wallets_stub.ensure_wallet(
        wallets_stub.OWNER_USER,
        wallets_stub.LOCAL_USER_ID,
        "world_test",
        "currency_gold",
        initial_balance=0,
    )

    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    # 推进到带货币奖励的事件节点
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    result = plot_stub.advance_plot_runtime(rt["id"])  # event -> choice（执行奖励）

    # 检查奖励已执行
    currency_rewards = [r for r in result["executed_rewards"] if r["type"] == "currency"]
    assert len(currency_rewards) == 1
    assert currency_rewards[0]["ok"] is True
    assert currency_rewards[0]["amount"] == 100

    # 检查钱包余额（奖励默认进入本地用户钱包）
    wallet = wallets_stub.get(
        wallets_stub.OWNER_USER,
        wallets_stub.LOCAL_USER_ID,
        "world_test",
        "currency_gold",
    )
    assert wallet["balance"] == 100


def test_rewards_item(temp_work_dir):
    """测试物品奖励执行（存储在剧情变量中）。"""
    # 创建一个带物品奖励的剧情
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        plots_dir = work_dir / "plots"
        plots_dir.mkdir(parents=True)

        plot_id = "plot_item_test"
        plot_dir = plots_dir / plot_id
        plot_dir.mkdir()

        plot_meta = {
            "id": plot_id,
            "name": "物品奖励测试",
            "description": "测试物品奖励",
            "genre": "测试",
            "setting": "测试",
            "tags": ["test"],
            "status": "draft",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        with open(plot_dir / "plot.json", "w", encoding="utf-8") as f:
            json.dump(plot_meta, f)

        nodes = [
            {
                "id": "node_start",
                "plot_id": plot_id,
                "type": "start",
                "title": "开始",
                "description": "",
                "position": {"x": 0, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
            {
                "id": "node_reward",
                "plot_id": plot_id,
                "type": "reward",
                "title": "给物品",
                "description": "给予物品",
                "position": {"x": 100, "y": 0},
                "trigger": None,
                "rewards": [
                    {"type": "item", "item_id": "item_sword", "quantity": 1}
                ],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
            {
                "id": "node_end",
                "plot_id": plot_id,
                "type": "end",
                "title": "结束",
                "description": "",
                "position": {"x": 200, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
        ]
        with open(plot_dir / "nodes.json", "w", encoding="utf-8") as f:
            json.dump(nodes, f)

        edges = [
            {"id": "e1", "plot_id": plot_id, "source": "node_start", "target": "node_reward", "condition": None, "label": ""},
            {"id": "e2", "plot_id": plot_id, "source": "node_reward", "target": "node_end", "condition": None, "label": ""},
        ]
        with open(plot_dir / "edges.json", "w", encoding="utf-8") as f:
            json.dump(edges, f)

        rt = plot_stub.create_plot_runtime(
            plot_id=plot_id,
            world_id="world_test",
            work_dir=str(work_dir),
        )

        plot_stub.advance_plot_runtime(rt["id"])  # start -> reward
        result = plot_stub.advance_plot_runtime(rt["id"])  # reward -> end

        item_rewards = [r for r in result["executed_rewards"] if r["type"] == "item"]
        assert len(item_rewards) == 1
        assert item_rewards[0]["ok"] is True
        assert item_rewards[0]["item_id"] == "item_sword"
        assert item_rewards[0]["quantity"] == 1

        # 检查剧情变量
        bucket = plot_stub._get_bucket()
        rt_state = bucket[rt["id"]]
        assert rt_state["variables"].get("inventory_item_sword") == 1


# ---------------------------------------------------------------------------
# 测试：效果执行（npc_mood、npc_status、world_state、plot_variable）
# ---------------------------------------------------------------------------


def test_effects_npc_mood(temp_work_dir):
    """测试 NPC 心情效果的执行。"""
    from xijian_api.stubs import npcs as npcs_stub

    # 使用真实的 npcs stub API 创建一个 NPC。
    npcs_stub.create(
        world_id="world_test",
        name="测试NPC",
        persona_doc="友善的NPC",
        state_json={"mood": 50},
        npc_id="npc_1",
    )

    # 创建带 npc_mood 效果的剧情
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        plots_dir = work_dir / "plots"
        plots_dir.mkdir(parents=True)

        plot_id = "plot_mood_test"
        plot_dir = plots_dir / plot_id
        plot_dir.mkdir()

        plot_meta = {
            "id": plot_id,
            "name": "心情测试",
            "description": "测试NPC心情变化",
            "genre": "测试",
            "setting": "测试",
            "tags": ["test"],
            "status": "draft",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        with open(plot_dir / "plot.json", "w", encoding="utf-8") as f:
            json.dump(plot_meta, f)

        nodes = [
            {
                "id": "node_start",
                "plot_id": plot_id,
                "type": "start",
                "title": "开始",
                "description": "",
                "position": {"x": 0, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
            {
                "id": "node_effect",
                "plot_id": plot_id,
                "type": "event",
                "title": "改变心情",
                "description": "NPC心情变化",
                "position": {"x": 100, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [
                    {"type": "npc_mood", "target": "npc_1", "delta": 20}
                ],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
            {
                "id": "node_end",
                "plot_id": plot_id,
                "type": "end",
                "title": "结束",
                "description": "",
                "position": {"x": 200, "y": 0},
                "trigger": None,
                "rewards": [],
                "effects": [],
                "bind_character_id": None,
                "bind_world_id": None,
                "bind_event_id": None,
                "metadata": {},
            },
        ]
        with open(plot_dir / "nodes.json", "w", encoding="utf-8") as f:
            json.dump(nodes, f)

        edges = [
            {"id": "e1", "plot_id": plot_id, "source": "node_start", "target": "node_effect", "condition": None, "label": ""},
            {"id": "e2", "plot_id": plot_id, "source": "node_effect", "target": "node_end", "condition": None, "label": ""},
        ]
        with open(plot_dir / "edges.json", "w", encoding="utf-8") as f:
            json.dump(edges, f)

        rt = plot_stub.create_plot_runtime(
            plot_id=plot_id,
            world_id="world_test",
            work_dir=str(work_dir),
        )

        plot_stub.advance_plot_runtime(rt["id"])  # start -> effect
        result = plot_stub.advance_plot_runtime(rt["id"])  # effect -> end

        mood_effects = [e for e in result["executed_effects"] if e["type"] == "npc_mood"]
        assert len(mood_effects) == 1
        assert mood_effects[0]["ok"] is True
        assert mood_effects[0]["new_mood"] == 70

        # 验证 NPC 心情已改变
        npc = npcs_stub.get("npc_1")
        assert npc["state_json"]["mood"] == 70


# ---------------------------------------------------------------------------
# 测试：PlotError 异常
# ---------------------------------------------------------------------------


def test_plot_error_inheritance():
    """PlotError 应为 ValueError 的子类。"""
    assert issubclass(plot_stub.PlotError, ValueError)
    try:
        raise plot_stub.PlotError("test error")
    except ValueError:
        pass  # 符合预期
    except Exception:
        pytest.fail("PlotError not caught as ValueError")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])