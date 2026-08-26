"""P1 Galgame 播放契约 + 32 档存档测试。

直接用 DevKit 工作目录构造带 script 的剧情，经 create_plot_runtime 启动后测
get_current_playback / advance_playback / 存档读写。
"""

import json
import tempfile
from pathlib import Path

import pytest

from xijian_api.stubs import plot_runtime as plot_stub
from xijian_api.stubs import worlds as worlds_stub
from xijian_api.stubs import state


@pytest.fixture(autouse=True)
def _reset_state():
    state.reset_for_testing()
    yield
    state.reset_for_testing()


def _make_plot(tmp_path, nodes, edges, plot_id="plot_p1"):
    work = Path(tmp_path) / "devkit"
    plots = work / "plots" / plot_id
    plots.mkdir(parents=True)
    (plots / "plot.json").write_text(json.dumps({
        "id": plot_id, "name": "P1", "status": "draft",
    }, ensure_ascii=False))
    (plots / "nodes.json").write_text(json.dumps(nodes, ensure_ascii=False))
    (plots / "edges.json").write_text(json.dumps(edges, ensure_ascii=False))
    return str(work), plot_id


def _make_world():
    worlds_stub.create(name="W", world_id="world_p1")


def test_playback_choice_and_save(tmp_path):
    nodes = [
        {"id": "n_start", "type": "start", "title": "t",
         "script": [
             {"type": "say", "speaker": "c1", "content": "开始"},
             {"type": "choice", "choices": [
                 {"text": "去左", "target": "e_left"},
                 {"text": "去右", "target": "e_right", "cond": "flag==1"},
             ]},
         ]},
        {"id": "n_left", "type": "end", "title": "l"},
        {"id": "n_right", "type": "end", "title": "r"},
    ]
    edges = [
        {"id": "e_left", "source": "n_start", "target": "n_left"},
        {"id": "e_right", "source": "n_start", "target": "n_right"},
    ]
    wd, pid = _make_plot(tmp_path, nodes, edges)
    _make_world()
    rt = plot_stub.create_plot_runtime(pid, "world_p1", work_dir=wd)

    # 第一句 say
    pb = plot_stub.get_current_playback(rt["id"])
    assert pb["current_step"]["type"] == "say"
    assert pb["script_index"] == 0
    plot_stub.advance_playback(rt["id"])
    # 第二句 choice
    pb = plot_stub.get_current_playback(rt["id"])
    assert pb["is_choice"] is True
    # flag!=1 时只看到"去左"
    assert len(pb["choices"]) == 1
    # 选择去左
    plot_stub.advance_playback(rt["id"], choose_edge_id="e_left")
    pb = plot_stub.get_current_playback(rt["id"])
    assert pb["status"] == "completed"

    # 存档（自动档 slot0 已由 advance 写入；再存手动档 slot1）
    plot_stub.save_plot_save(rt["id"], 1, label="我的档")
    saves = plot_stub.list_plot_saves(pid)
    assert any(s["slot"] == 0 for s in saves)  # 自动档
    assert any(s["slot"] == 1 for s in saves)   # 手动档


def test_save_slot_out_of_range(tmp_path):
    nodes = [{"id": "n_start", "type": "start", "title": "t", "script": [
        {"type": "say", "speaker": "c", "content": "x"}]}]
    edges = []
    wd, pid = _make_plot(tmp_path, nodes, edges)
    _make_world()
    rt = plot_stub.create_plot_runtime(pid, "world_p1", work_dir=wd)
    with pytest.raises(plot_stub.PlotError):
        plot_stub.save_plot_save(rt["id"], 99)
    with pytest.raises(plot_stub.PlotError):
        plot_stub.save_plot_save(rt["id"], -1)


def test_load_overwrites_runtime(tmp_path):
    nodes = [
        {"id": "n_start", "type": "start", "title": "t", "script": [
            {"type": "say", "speaker": "c", "content": "第一句"},
            {"type": "say", "speaker": "c", "content": "第二句"},
        ]},
        {"id": "n_end", "type": "end", "title": "e"},
    ]
    edges = [{"id": "e1", "source": "n_start", "target": "n_end"}]
    wd, pid = _make_plot(tmp_path, nodes, edges)
    _make_world()
    rt = plot_stub.create_plot_runtime(pid, "world_p1", work_dir=wd)
    plot_stub.advance_playback(rt["id"])  # 到第2句
    assert plot_stub.get_current_playback(rt["id"])["script_index"] == 1

    # 存手动档 slot 1，然后推进到最后一句触发节点流转（结束），再读档应回到第2句
    plot_stub.save_plot_save(rt["id"], 1, label="我的档")
    with pytest.raises(plot_stub.PlotError):
        plot_stub.save_plot_save(rt["id"], 0)  # 手动写 slot 0 被拒绝
    plot_stub.advance_playback(rt["id"])  # 已是最后一步 → 节点流转 → completed
    assert plot_stub.get_current_playback(rt["id"])["status"] == "completed"

    loaded = plot_stub.load_plot_save(pid, 1)
    assert loaded["script_index"] == 1
    assert loaded["status"] == "running"


def test_auto_save_on_advance(tmp_path):
    """每次 advance 成功自动写 slot 0。"""
    nodes = [{"id": "n_start", "type": "start", "title": "t", "script": [
        {"type": "say", "speaker": "c", "content": "a"},
        {"type": "say", "speaker": "c", "content": "b"},
    ]}, {"id": "n_end", "type": "end", "title": "e"}]
    edges = [{"id": "e1", "source": "n_start", "target": "n_end"}]
    wd, pid = _make_plot(tmp_path, nodes, edges)
    _make_world()
    rt = plot_stub.create_plot_runtime(pid, "world_p1", work_dir=wd)
    plot_stub.advance_playback(rt["id"])
    saves = plot_stub.list_plot_saves(pid)
    assert any(s["slot"] == 0 for s in saves)
