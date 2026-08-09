"""C3 剧情调度挂接（2026-08-09）测试。

覆盖：tick 时运行中剧情节点被评估/激活、无剧情 world 不受影响、
plot 评估异常不阻断事件调度。挂点：events.tick_world 每 tick 一次
``plot_runtime.evaluate_plot_triggers``，激活记录带
``object: "plot.activation"`` 并入返回列表。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from xijian_api.stubs import events as events_stub
from xijian_api.stubs import plot_runtime as plot_stub
from xijian_api.stubs import worlds as worlds_stub


# ---------------------------------------------------------------------------
# 夹具（与 test_xijian_plot.py 同构：临时 devkit 工作目录 + 世界）
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_work_dir():
    """创建一个带有示例剧情数据的临时 devkit 工作目录。

    剧情结构：node_start → node_event_1（condition quest_stage==1）
    → node_choice（choice，需指定边）→ node_end。
    """
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        plots_dir = work_dir / "plots"
        plots_dir.mkdir(parents=True)

        plot_id = "plot_test_001"
        plot_dir = plots_dir / plot_id
        plot_dir.mkdir()

        with open(plot_dir / "plot.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": plot_id,
                    "name": "测试剧情",
                    "description": "用于测试的剧情",
                    "genre": "测试",
                    "setting": "测试世界",
                    "tags": ["test"],
                    "status": "draft",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                f,
                ensure_ascii=False,
            )

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
                "trigger": {
                    "type": "condition",
                    "field": "quest_stage",
                    "op": "eq",
                    "value": 1,
                },
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
                "condition": None,
                "label": "接受",
            },
        ]
        with open(plot_dir / "edges.json", "w", encoding="utf-8") as f:
            json.dump(edges, f, ensure_ascii=False)

        worlds_stub.create(world_id="world_test", name="测试世界")
        yield str(work_dir)


def _create_runtime_at_trigger_node(work_dir: str) -> dict:
    """创建剧情运行时并推进到带触发条件的 node_event_1。"""
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=work_dir,
        initial_variables={"quest_stage": 1},
    )
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event
    assert plot_stub.get_plot_runtime(rt["id"])["current_node_id"] == "node_event_1"
    return rt


# ---------------------------------------------------------------------------
# a) tick 时运行中剧情节点被评估/激活
# ---------------------------------------------------------------------------


def test_tick_world_evaluates_plot_triggers(temp_work_dir):
    rt = _create_runtime_at_trigger_node(temp_work_dir)

    result = events_stub.tick_world("world_test")

    # 激活记录并入 tick 返回列表，带标记与运行时/节点信息。
    activations = [r for r in result if r.get("object") == "plot.activation"]
    assert len(activations) == 1
    assert activations[0]["runtime_id"] == rt["id"]
    assert activations[0]["node_id"] == "node_event_1"
    assert activations[0]["new_node_id"] == "node_choice"

    # 剧情确实被推进：当前节点前进、触发节点进入完成列表。
    after = plot_stub.get_plot_runtime(rt["id"])
    assert after["current_node_id"] == "node_choice"
    assert "node_event_1" in after["completed_nodes"]


def test_tick_world_skips_plot_when_trigger_not_met(temp_work_dir):
    """条件不满足时剧情不被激活。

    注意：``worlds.create`` 会给新世界播种默认事件（market_day /
    rain_storm / festival），所以 tick 返回列表里可能含有播种事件
    实例——这里只断言不含 plot 激活记录、剧情未被推进。
    """
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_test",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 99},  # 条件 quest_stage==1 不匹配
    )
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    result = events_stub.tick_world("world_test")
    assert all(r.get("object") != "plot.activation" for r in result)
    assert (
        plot_stub.get_plot_runtime(rt["id"])["current_node_id"] == "node_event_1"
    )


# ---------------------------------------------------------------------------
# b) tick_all：并入 plot-only world；无剧情 world 不受影响
# ---------------------------------------------------------------------------


def test_tick_all_includes_plot_world_without_events(temp_work_dir):
    """剧情 world 没有事件定义，tick_all 也必须覆盖它。"""
    worlds_stub.create(world_id="world_plot_only", name="纯剧情世界")
    rt = plot_stub.create_plot_runtime(
        plot_id="plot_test_001",
        world_id="world_plot_only",
        work_dir=temp_work_dir,
        initial_variables={"quest_stage": 1},
    )
    plot_stub.advance_plot_runtime(rt["id"])  # start -> event

    out = events_stub.tick_all()
    assert "world_plot_only" in out
    activations = [
        r for r in out["world_plot_only"] if r.get("object") == "plot.activation"
    ]
    assert len(activations) == 1
    assert activations[0]["runtime_id"] == rt["id"]


def test_tick_world_plot_free_world_unaffected(temp_work_dir):
    """无剧情 world：事件正常触发，返回列表不含 plot 标记。

    任何新建 world 都会被 ``worlds.create`` 播种默认事件，因此这里
    不假设返回为空，只断言：全是事件实例、绝无 plot 激活混入。
    """
    events_stub.create_event(
        world_id="world_test",
        kind="common",
        name="普通事件",
        trigger_config={"type": "interval", "seconds": 60},
    )
    result = events_stub.tick_world("world_test")
    assert result, "事件应正常触发"
    assert all(r.get("object") != "plot.activation" for r in result)
    assert all("event_id" in r for r in result)

    # 纯播种事件的 world（无剧情）同样只返回事件实例。
    worlds_stub.create(world_id="world_empty", name="空世界")
    empty_result = events_stub.tick_world("world_empty")
    assert all(r.get("object") != "plot.activation" for r in empty_result)
    assert all("event_id" in r for r in empty_result)


# ---------------------------------------------------------------------------
# c) plot 异常不阻断事件调度
# ---------------------------------------------------------------------------


def test_plot_failure_does_not_block_events(temp_work_dir, monkeypatch):
    """plot 评估抛异常时，事件调度照常进行、tick 不崩。"""
    _create_runtime_at_trigger_node(temp_work_dir)
    events_stub.create_event(
        world_id="world_test",
        kind="common",
        name="普通事件",
        trigger_config={"type": "interval", "seconds": 60},
    )

    def boom(world_id):
        raise RuntimeError("simulated plot crash")

    monkeypatch.setattr(plot_stub, "evaluate_plot_triggers", boom)

    result = events_stub.tick_world("world_test")
    # 事件照常触发，且没有 plot 激活记录混入。
    assert result
    assert all(r.get("object") != "plot.activation" for r in result)
    assert all("event_id" in r for r in result)

    # tick_all 同样不崩。
    out = events_stub.tick_all()
    assert "world_test" in out


def test_plot_failure_isolated_from_other_worlds(temp_work_dir, monkeypatch):
    """一个 world 的 plot 异常不影响其他 world 的 tick。"""
    worlds_stub.create(world_id="world_good", name="好世界")
    events_stub.create_event(
        world_id="world_good",
        kind="common",
        name="好事件",
        trigger_config={"type": "interval", "seconds": 60},
    )

    def boom(world_id):
        raise RuntimeError("simulated plot crash")

    monkeypatch.setattr(plot_stub, "evaluate_plot_triggers", boom)

    out = events_stub.tick_all()
    assert "world_good" in out
    assert out["world_good"], "world_good 的事件应正常触发"
