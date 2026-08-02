"""Tests for plot_runtime stub module."""

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset state before each test."""
    state.reset_for_testing()
    yield
    state.reset_for_testing()


@pytest.fixture
def temp_work_dir():
    """Create a temporary devkit work directory with sample plot data."""
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        plots_dir = work_dir / "plots"
        plots_dir.mkdir(parents=True)

        # Create a sample plot
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

        # Create a world via the real worlds stub API.
        worlds_stub.create(world_id="world_test", name="测试世界")

        yield str(work_dir)


# ---------------------------------------------------------------------------
# Test: list_available_plots
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
# Test: get_plot_design
# ---------------------------------------------------------------------------


def test_get_plot_design(temp_work_dir):
    design = plot_stub.get_plot_design(temp_work_dir, "plot_test_001")
    assert design is not None
    assert design["id"] == "plot_test_001"
    assert design["name"] == "测试剧情"

    # Not found
    assert plot_stub.get_plot_design(temp_work_dir, "nonexistent") is None


# ---------------------------------------------------------------------------
# Test: get_plot_design_nodes / edges
# ---------------------------------------------------------------------------


def test_get_plot_design_nodes(temp_work_dir):
    nodes = plot_stub.get_plot_design_nodes(temp_work_dir, "plot_test_001")
    assert len(nodes) == 4
    assert nodes[0]["id"] == "node_start"
    assert nodes[0]["type"] == "start"

    # Not found
    assert plot_stub.get_plot_design_nodes(temp_work_dir, "nonexistent") == []


def test_get_plot_design_edges(temp_work_dir):
    edges = plot_stub.get_plot_design_edges(temp_work_dir, "plot_test_001")
    assert len(edges) == 4
    assert edges[0]["source"] == "node_start"
    assert edges[0]["target"] == "node_event_1"

    # Not found
    assert plot_stub.get_plot_design_edges(temp_work_dir, "nonexistent") == []


# ---------------------------------------------------------------------------
# Test: create_plot_runtime
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
# Test: get_plot_runtime / list_plot_runtimes
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

    # Not found
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

    # Filter by world_id
    filtered = plot_stub.list_plot_runtimes(world_id="world_test")
    assert len(filtered) == 2

    # Filter by status
    filtered = plot_stub.list_plot_runtimes(status=plot_stub.PLOT_RUNTIME_STATUS_RUNNING)
    assert len(filtered) == 2


# ---------------------------------------------------------------------------
# Test: advance_plot_runtime (linear progression)
# ---------------------------------------------------------------------------


def test_advance_plot_runtime_linear(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    # Start node -> event node
    result = plot_stub.advance_plot_runtime(rt["id"])
    assert result["current_node_id"] == "node_event_1"
    assert result["next_node_id"] == "node_event_1"
    assert "node_start" in result["completed_nodes"]
    assert len(result["executed_rewards"]) == 0  # start node has no rewards

    # Event node -> choice node (has rewards and effects)
    result = plot_stub.advance_plot_runtime(rt["id"])
    assert result["current_node_id"] == "node_choice"
    assert "node_event_1" in result["completed_nodes"]
    # Check rewards executed
    assert len(result["executed_rewards"]) > 0
    # Check effects executed (plot_variable)
    assert any(e["type"] == "plot_variable" for e in result["executed_effects"])


def test_advance_plot_runtime_choice_node(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"player_choice": "accept"},
    )

    # Advance to choice node
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    result = plot_stub.advance_plot_runtime(rt["id"])  # event -> choice

    # At choice node, need to specify edge
    with pytest.raises(plot_stub.PlotError, match="choice 节点必须指定 choose_edge_id"):
        plot_stub.advance_plot_runtime(rt["id"])

    # Choose edge_3a (accept)
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

    # Try to choose edge_3a with wrong condition
    with pytest.raises(plot_stub.PlotError, match="边条件不满足"):
        plot_stub.advance_plot_runtime(rt["id"], choose_edge_id="edge_3a")

    # Choose edge_3b (reject) - should work
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
    # Complete the plot
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    plot_stub.advance_plot_runtime(rt["id"])  # event -> choice
    plot_stub.advance_plot_runtime(rt["id"], choose_edge_id="edge_3a")  # choice -> end

    # Try to advance completed plot
    with pytest.raises(plot_stub.PlotError, match="不可推进"):
        plot_stub.advance_plot_runtime(rt["id"])


# ---------------------------------------------------------------------------
# Test: pause / resume
# ---------------------------------------------------------------------------


def test_pause_resume_plot_runtime(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )

    # Pause
    paused = plot_stub.pause_plot_runtime(rt["id"])
    assert paused["status"] == plot_stub.PLOT_RUNTIME_STATUS_PAUSED

    # Try to advance paused plot
    with pytest.raises(plot_stub.PlotError, match="不可推进"):
        plot_stub.advance_plot_runtime(rt["id"])

    # Resume
    resumed = plot_stub.resume_plot_runtime(rt["id"])
    assert resumed["status"] == plot_stub.PLOT_RUNTIME_STATUS_RUNNING

    # Should be able to advance now
    result = plot_stub.advance_plot_runtime(rt["id"])
    assert result["current_node_id"] == "node_event_1"


def test_pause_non_running(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )
    plot_stub.pause_plot_runtime(rt["id"])

    # Try to pause again
    with pytest.raises(plot_stub.PlotError, match="只能暂停运行中的剧情"):
        plot_stub.pause_plot_runtime(rt["id"])


# ---------------------------------------------------------------------------
# Test: delete_plot_runtime
# ---------------------------------------------------------------------------


def test_delete_plot_runtime(temp_work_dir):
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
    )
    assert plot_stub.delete_plot_runtime(rt["id"]) is True
    assert plot_stub.get_plot_runtime(rt["id"]) is None
    assert plot_stub.delete_plot_runtime(rt["id"]) is False  # already deleted


# ---------------------------------------------------------------------------
# Test: get_plot_node / get_plot_edges
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

    # Not found
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
# Test: evaluate_plot_triggers
# ---------------------------------------------------------------------------


def test_evaluate_plot_triggers_condition(temp_work_dir):
    """Test trigger evaluation with condition trigger."""
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 1},  # Condition: quest_stage == 1
    )

    # Current node is start (no trigger), advance to event node which has condition trigger
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    # Now current node is node_event_1 with condition trigger
    # Manually set variables so trigger matches
    bucket = plot_stub._get_bucket()
    rt_state = bucket[rt["id"]]
    rt_state["variables"]["quest_stage"] = 1

    # Evaluate triggers
    activated = plot_stub.evaluate_plot_triggers("world_test")
    assert len(activated) == 1
    assert activated[0]["runtime_id"] == rt["id"]
    assert activated[0]["node_id"] == "node_event_1"
    assert activated[0]["new_node_id"] == "node_choice"


def test_evaluate_plot_triggers_no_match(temp_work_dir):
    """Test trigger evaluation when condition doesn't match."""
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 99},  # Condition: quest_stage == 1 (won't match)
    )

    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    activated = plot_stub.evaluate_plot_triggers("world_test")
    assert len(activated) == 0


def test_evaluate_plot_triggers_only_running(temp_work_dir):
    """Test that only running runtimes are evaluated."""
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 1},
    )
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    # Pause it
    plot_stub.pause_plot_runtime(rt["id"])

    activated = plot_stub.evaluate_plot_triggers("world_test")
    assert len(activated) == 0


# ---------------------------------------------------------------------------
# Test: Rewards execution (currency, item, experience, relationship)
# ---------------------------------------------------------------------------


def test_rewards_currency(temp_work_dir):
    """Test currency reward execution."""
    from xijian_api.stubs import world_currencies as wc_stub
    from xijian_api.stubs import wallets as wallets_stub

    # Setup currency and wallet using the real world_currencies/wallets APIs.
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

    # Advance to event node which has currency reward
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    result = plot_stub.advance_plot_runtime(rt["id"])  # event -> choice (executes rewards)

    # Check reward executed
    currency_rewards = [r for r in result["executed_rewards"] if r["type"] == "currency"]
    assert len(currency_rewards) == 1
    assert currency_rewards[0]["ok"] is True
    assert currency_rewards[0]["amount"] == 100

    # Check wallet balance (rewards default to the local user wallet)
    wallet = wallets_stub.get(
        wallets_stub.OWNER_USER,
        wallets_stub.LOCAL_USER_ID,
        "world_test",
        "currency_gold",
    )
    assert wallet["balance"] == 100


def test_rewards_item(temp_work_dir):
    """Test item reward execution (stored in plot variables)."""
    # Create a plot with item reward
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

        # Check plot variable
        bucket = plot_stub._get_bucket()
        rt_state = bucket[rt["id"]]
        assert rt_state["variables"].get("inventory_item_sword") == 1


# ---------------------------------------------------------------------------
# Test: Effects execution (npc_mood, npc_status, world_state, plot_variable)
# ---------------------------------------------------------------------------


def test_effects_npc_mood(temp_work_dir):
    """Test NPC mood effect execution."""
    from xijian_api.stubs import npcs as npcs_stub

    # Create an NPC using the real npcs stub API.
    npcs_stub.create(
        world_id="world_test",
        name="测试NPC",
        persona_doc="友善的NPC",
        state_json={"mood": 50},
        npc_id="npc_1",
    )

    # Create plot with npc_mood effect
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

        # Verify NPC mood changed
        npc = npcs_stub.get("npc_1")
        assert npc["state_json"]["mood"] == 70


# ---------------------------------------------------------------------------
# Test: PlotError exception
# ---------------------------------------------------------------------------


def test_plot_error_inheritance():
    """PlotError should be a ValueError subclass."""
    assert issubclass(plot_stub.PlotError, ValueError)
    try:
        raise plot_stub.PlotError("test error")
    except ValueError:
        pass  # Expected
    except Exception:
        pytest.fail("PlotError not caught as ValueError")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])