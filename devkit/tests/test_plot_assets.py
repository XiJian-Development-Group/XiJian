"""P0-3 资产登记与 manifest 测试。"""

import json
import os

import pytest

from devkit import plot_editor as pe
from devkit import DevKitError


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "devkit"
    d.mkdir()
    # 建一个剧情占位
    plot_id = "plot_test"
    plot_dir = d / "plots" / plot_id
    plot_dir.mkdir(parents=True)
    (plot_dir / "plot.json").write_text(json.dumps({"id": plot_id, "name": "测试", "status": "draft"}))
    (plot_dir / "nodes.json").write_text(json.dumps([
        {"id": "n1", "type": "start", "title": "t", "bind_character_id": "char_x"},
    ]))
    (plot_dir / "edges.json").write_text(json.dumps([]))
    return str(d), plot_id


def _make_file(tmp_path, name, content=b"dummy"):
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


def test_register_sprite(work, tmp_path):
    wd, pid = work
    src = _make_file(tmp_path, "char_a_smile.png")
    rec = pe.register_asset(wd, pid, "sprite", src)
    assert rec["kind"] == "sprite"
    assert rec["file"].startswith("assets/sprites/")
    assert rec["hash"]
    assert rec["size"] == 5
    # 文件确实拷贝了
    full = os.path.join(wd, "plots", pid, rec["file"])
    assert os.path.isfile(full)


def test_register_with_external_ref(work, tmp_path):
    wd, pid = work
    src = _make_file(tmp_path, "bg1.png", b"bgdata")
    rec = pe.register_asset(
        wd, pid, "bg", src,
        external_ref={"kind": "character", "source_id": "char_x", "original_path": src},
    )
    assert rec["external_ref"]["source_id"] == "char_x"
    # index 里也有
    idx = pe._load_assets_index(wd, pid)
    assert any(r["id"] == rec["id"] and "external_ref" in r for r in idx)


def test_register_bad_kind(work, tmp_path):
    wd, pid = work
    src = _make_file(tmp_path, "x.png")
    with pytest.raises(DevKitError):
        pe.register_asset(wd, pid, "live2d", src)


def test_register_missing_src(work, tmp_path):
    wd, pid = work
    with pytest.raises(DevKitError):
        pe.register_asset(wd, pid, "sprite", "/no/such/file.png")


def test_duplicate_name_gets_suffix(work, tmp_path):
    wd, pid = work
    src = _make_file(tmp_path, "same.png", b"aaa")
    r1 = pe.register_asset(wd, pid, "sprite", src)
    r2 = pe.register_asset(wd, pid, "sprite", src)  # 同名不同内容
    assert r1["file"] != r2["file"]


def test_build_manifest(work, tmp_path):
    wd, pid = work
    src = _make_file(tmp_path, "bg1.png", b"x")
    pe.register_asset(wd, pid, "bg", src)
    m = pe.build_manifest(wd, pid)
    assert m["plot_id"] == pid
    assert m["start_node"] == "n1"
    assert m["asset_count"] == 1
    assert m["dependencies"]["characters"] == ["char_x"]
    assert m["dependencies"]["world"] is None
