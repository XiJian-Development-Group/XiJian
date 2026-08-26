"""P0-2 Step 数据模型与节点 script 校验测试。"""

import pytest

from xijian_api.stubs import plot_runtime as pr


def test_validate_step_say_ok():
    pr._validate_step({"type": "say", "speaker": "c1", "content": "hello"})
    pr._validate_step({"type": "narration", "content": "旁白"})


def test_validate_step_say_missing_content():
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "say", "speaker": "c1"})


def test_validate_step_unknown_type():
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "flying", "content": "x"})


def test_validate_step_setfigure_ok_and_bad():
    pr._validate_step({"type": "setFigure", "figure": {"name": "a", "pos": "left"}})
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "setFigure", "figure": {"pos": "left"}})  # 缺 name
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "setFigure", "figure": {"name": "a", "pos": "top"}})  # 非法 pos


def test_validate_step_setbg_ok_and_bad():
    pr._validate_step({"type": "setBg", "bg": "bg1.png"})
    pr._validate_step({"type": "setBg", "videoBg": "bg1.mp4"})
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "setBg"})


def test_validate_step_choice_ok_and_bad():
    pr._validate_step({"type": "choice", "choices": [
        {"text": "A", "target": "n2"},
        {"text": "B", "target": "n3", "cond": "affinity>=5"},
    ]})
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "choice", "choices": [{"target": "n2"}]})  # 缺 text
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "choice", "choices": []})


def test_validate_step_setvar_ok_and_bad():
    pr._validate_step({"type": "setVar", "setVar": [{"name": "x", "op": "+=", "value": 1}]})
    # 缺 op 时默认 "="，应放行
    pr._validate_step({"type": "setVar", "setVar": [{"name": "x", "value": 1}]})
    # 下面故意非法 op
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "setVar", "setVar": [{"name": "x", "op": "**", "value": 1}]})


def test_validate_step_transition_ok_and_bad():
    pr._validate_step({"type": "transition", "transition": "fade"})
    with pytest.raises(pr.PlotError):
        pr._validate_step({"type": "transition", "transition": "explode"})


def test_validate_node_with_script():
    node = {
        "id": "n1", "type": "event", "title": "t",
        "script": [
            {"type": "setBg", "bg": "bg.png"},
            {"type": "say", "speaker": "c1", "content": "hi"},
            {"type": "choice", "choices": [{"text": "A", "target": "n2"}]},
        ],
    }
    pr._validate_node(node)  # 不应抛


def test_validate_node_script_bad_step():
    node = {
        "id": "n1", "type": "event", "title": "t",
        "script": [{"type": "say", "speaker": "c1"}],  # 缺 content
    }
    with pytest.raises(pr.PlotError):
        pr._validate_node(node)


def test_validate_node_no_script_is_ok():
    node = {"id": "n1", "type": "reward", "title": "t"}
    pr._validate_node(node)  # 旧节点无 script 仍合法


def test_runtime_state_has_script_index():
    # 构造一个最小 runtime 状态，确认 script_index 字段存在且默认 0
    rt = {"id": "r1", "plot_id": "p1", "world_id": "w1",
          "current_node_id": "n1", "script_index": 0, "completed_nodes": [],
          "available_edges": [], "variables": {}, "unlocked_nodes": ["n1"],
          "status": "running", "started_at": 0.0, "updated_at": 0.0,
          "last_tick_at": 0.0, "_nodes": {}, "_edges": []}
    ser = pr._serialize_runtime(rt)
    assert ser["script_index"] == 0
