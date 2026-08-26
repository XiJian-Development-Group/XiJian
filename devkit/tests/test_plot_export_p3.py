"""P3 导出打包测试：自包含剧情包（assets + manifest 内联）。"""

import json
import os

import pytest

from devkit import plot_editor as pe


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "devkit"
    plot = d / "plots" / "plot_exp"
    plot.mkdir(parents=True)
    (plot / "plot.json").write_text(json.dumps({
        "id": "plot_exp", "name": "导出测试", "description": "desc",
    }, ensure_ascii=False), encoding="utf-8")
    (plot / "nodes.json").write_text(json.dumps([
        {"id": "n1", "type": "start", "title": "t", "bind_character_id": "char_a"},
        {"id": "n2", "type": "end", "title": "e"},
    ]), encoding="utf-8")
    (plot / "edges.json").write_text("[]", encoding="utf-8")
    return str(d)


def _arcnames(files):
    return {f["arcname"] for f in files}


def test_export_basic_includes_manifest(work):
    out = pe.export_plot_for_submit(work, "plot_exp")
    arcs = _arcnames(out["files"])
    assert f"plots/plot_exp/plot.json" in arcs
    assert f"plots/plot_exp/nodes.json" in arcs
    assert f"plots/plot_exp/edges.json" in arcs
    assert f"plots/plot_exp/manifest.json" in arcs
    # 无资产时不包含 assets_index
    assert f"plots/plot_exp/assets/assets_index.json" not in arcs
    # manifest 内容正确
    m = out["manifest"]
    assert m["plot_id"] == "plot_exp"
    assert m["dependencies"]["characters"] == ["char_a"]
    assert m["dependencies"]["world"] is None
    assert m["start_node"] == "n1"
    assert m["warnings"] == []


def test_export_with_assets(work, tmp_path):
    src = tmp_path / "bg.png"
    src.write_bytes(b"\x89PNG-fake")
    rec = pe.register_asset(work, "plot_exp", "bg", str(src),
                            external_ref={"kind": "world", "source_id": "world_1",
                                          "original_path": str(src)})
    src2 = tmp_path / "hero.png"
    src2.write_bytes(b"\x89PNG-hero")
    pe.register_asset(work, "plot_exp", "sprite", str(src2))

    out = pe.export_plot_for_submit(work, "plot_exp")
    arcs = _arcnames(out["files"])
    # 资产索引 + 每个资产文件都进包（相对路径保留）
    assert "plots/plot_exp/assets/assets_index.json" in arcs
    assert f"plots/plot_exp/{rec['file']}" in arcs
    sprite_files = [a for a in arcs if "/sprites/" in a]
    assert len(sprite_files) == 1
    # manifest 记录资产数与外部出处
    m = out["manifest"]
    assert m["asset_count"] == 2
    ext = [a for a in m["assets"] if "external_ref" in a]
    assert len(ext) == 1 and ext[0]["external_ref"]["source_id"] == "world_1"


def test_export_missing_asset_skipped(work, tmp_path):
    src = tmp_path / "gone.png"
    src.write_bytes(b"x")
    rec = pe.register_asset(work, "plot_exp", "audio", str(src))
    # 删包内拷贝（register_asset 已把源文件复制进来），模拟资产文件缺失
    os.remove(os.path.join(work, "plots", "plot_exp", rec["file"]))

    out = pe.export_plot_for_submit(work, "plot_exp")
    arcs = _arcnames(out["files"])
    assert f"plots/plot_exp/{rec['file']}" not in arcs
    assert any("缺失" in w for w in out["manifest"]["warnings"])
    # 其他文件照常导出
    assert "plots/plot_exp/plot.json" in arcs


def test_export_nonexistent(work):
    from devkit import DevKitError
    with pytest.raises(DevKitError):
        pe.export_plot_for_submit(work, "plot_nope")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
