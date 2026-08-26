"""P2 节点校验/字段映射 + 资产管理 API 测试。"""

import os

import pytest

from devkit import DevKitError
from devkit import plot_editor as pe


# ---------------------------------------------------------------------------
# validate_node_payload：字段映射 + 类型对齐 Core
# ---------------------------------------------------------------------------


def test_validate_maps_legacy_fields():
    node = pe.validate_node_payload({"type": "event", "name": "开场", "content": "描述"})
    assert node["title"] == "开场"
    assert node["description"] == "描述"
    assert "name" not in node and "content" not in node


def test_validate_rejects_bad_type():
    with pytest.raises(DevKitError) as ei:
        pe.validate_node_payload({"type": "dialogue"})
    assert "节点类型非法" in str(ei.value) and "dialogue" in str(ei.value)


def test_validate_all_six_types():
    for t in ("start", "branch", "event", "choice", "reward", "end"):
        assert pe.validate_node_payload({"type": t})["type"] == t


def test_validate_script_steps():
    ok = {"script": [
        {"type": "say", "speaker": "c1", "content": "你好"},
        {"type": "wait", "waitMs": 300},
        {"type": "choice", "choices": [{"text": "A", "target": "n2"}]},
    ]}
    assert pe.validate_node_payload(ok) is ok
    # say 空台词
    with pytest.raises(DevKitError):
        pe.validate_node_payload({"script": [{"type": "say", "content": " "}]} )
    # choice 无选项
    with pytest.raises(DevKitError):
        pe.validate_node_payload({"script": [{"type": "choice", "choices": []}]})
    # 未知步类型
    with pytest.raises(DevKitError):
        pe.validate_node_payload({"script": [{"type": "boom"}]})


# ---------------------------------------------------------------------------
# 资产 API
# ---------------------------------------------------------------------------


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "devkit"
    d.mkdir()
    plot = d / "plots" / "plot_x"
    plot.mkdir(parents=True)
    (plot / "plot.json").write_text('{"id": "plot_x", "name": "X"}', encoding="utf-8")
    return str(d)


def _make_png(path, color=b"\x89PNG fake"):
    path.write_bytes(color)
    return str(path)


def test_asset_roundtrip(work, tmp_path):
    src = _make_png(tmp_path / "a.png")
    rec = pe.register_asset(work, "plot_x", "sprite", src)
    grouped = pe.list_plot_assets(work, "plot_x")
    assert [r["id"] for r in grouped["sprite"]] == [rec["id"]]
    assert grouped["bg"] == [] and grouped["audio"] == []

    url = pe.read_asset_data_url(work, "plot_x", rec["id"])
    assert url["data_url"].startswith("data:image/png;base64,")

    assert pe.delete_plot_asset(work, "plot_x", rec["id"]) is True
    assert not os.path.exists(os.path.join(work, "plots", "plot_x", rec["file"]))
    assert pe.list_plot_assets(work, "plot_x")["sprite"] == []


def test_import_external_ref(work, tmp_path):
    src = _make_png(tmp_path / "hero.png")
    rec = pe.import_external_asset(
        work, "plot_x", "bg", src, source_kind="character", source_id="char_abc",
    )
    assert rec["external_ref"]["kind"] == "character"
    assert rec["external_ref"]["source_id"] == "char_abc"
    assert rec["external_ref"]["original_path"] == src

    with pytest.raises(DevKitError):
        pe.import_external_asset(work, "plot_x", "bg", src, source_kind="alien", source_id="x")


def test_read_data_url_missing_or_big(work, tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * (9 * 1024 * 1024))  # >8MB
    rec = pe.register_asset(work, "plot_x", "bg", str(big))
    with pytest.raises(DevKitError) as ei:
        pe.read_asset_data_url(work, "plot_x", rec["id"])
    assert "文件过大" in str(ei.value)

    with pytest.raises(DevKitError):
        pe.read_asset_data_url(work, "plot_x", "asset_nope")


def test_save_node_validates_script(work):
    from devkit.plot_editor import save_plot_node, get_plot_nodes
    bad = {"id": "node_bad", "type": "dialogue"}  # 旧非法类型
    with pytest.raises(DevKitError):
        save_plot_node(work, "plot_x", bad)
    good = {"id": "node_ok", "name": "旧名", "content": "旧内容",
            "script": [{"type": "say", "content": "hi"}]}
    saved = save_plot_node(work, "plot_x", good)
    assert saved["title"] == "旧名"  # 映射生效
    stored = get_plot_nodes(work, "plot_x")[0]
    assert stored["description"] == "旧内容"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
