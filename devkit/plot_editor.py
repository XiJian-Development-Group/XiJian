from __future__ import annotations

import hashlib
import json
import os
import secrets
from typing import Any

from devkit import DevKitError


_PLOT_SUBDIR = "plots"

# Galgame 资产子目录（相对 plots/<plot_id>/）
_ASSET_SUBDIR = "assets"
_ASSET_SPRITES = "sprites"   # 立绘（PNG/JPG，含表情差分）
_ASSET_BG = "bg"             # 背景（PNG/JPG/视频）
_ASSET_AUDIO = "audio"       # BGM/SE/Voice
_ASSET_KINDS = ("sprite", "bg", "audio")

# 外部资产引用（来自绑定角色/世界）的登记标记
_EXTERNAL_REF_MARKER = "__external__"


def _gen_id() -> str:
    return f"plot_{secrets.token_hex(8)}"


def _plot_dir(work_dir: str, plot_id: str) -> str:
    return os.path.join(work_dir, _PLOT_SUBDIR, plot_id)


def _assets_dir(work_dir: str, plot_id: str) -> str:
    """剧情包资产根目录。"""
    return os.path.join(_plot_dir(work_dir, plot_id), _ASSET_SUBDIR)


def _assets_index_path(work_dir: str, plot_id: str) -> str:
    return os.path.join(_assets_dir(work_dir, plot_id), "assets_index.json")


def _asset_kind_subdir(kind: str) -> str:
    """资产 kind → 子目录名。"""
    if kind == "sprite":
        return _ASSET_SPRITES
    if kind == "bg":
        return _ASSET_BG
    if kind == "audio":
        return _ASSET_AUDIO
    raise DevKitError(400, f"未知资产类型: {kind}", code="bad_asset_kind")


def register_asset(
    work_dir: str, plot_id: str, kind: str, src_path: str,
    asset_id: str | None = None, external_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """登记一个资产到剧情包。

    - 从 ``src_path`` 拷贝文件到 ``assets/<subdir>/``
    - 在 ``assets_index.json`` 追加一条记录（含哈希/大小/外部引用）

    ``external_ref`` 形如 ``{"kind": "character", "source_id": "char_x",
    "original_path": "/abs/..."}``，用于标记该资产来自绑定的角色/世界，
    导出时由调用方负责把源文件传进来（这里只登记与拷贝）。
    """
    if kind not in _ASSET_KINDS:
        raise DevKitError(400, f"未知资产类型: {kind}", code="bad_asset_kind")
    if not os.path.isfile(src_path):
        raise DevKitError(400, f"资产源文件不存在: {src_path}", code="asset_not_found")

    sub = _asset_kind_subdir(kind)
    dest_dir = os.path.join(_assets_dir(work_dir, plot_id), sub)
    os.makedirs(dest_dir, exist_ok=True)

    fname = os.path.basename(src_path)
    dest_path = os.path.join(dest_dir, fname)
    # 避免覆盖：重名时加短哈希后缀
    if os.path.exists(dest_path):
        base, ext = os.path.splitext(fname)
        suffix = hashlib.sha256(fname.encode()).hexdigest()[:6]
        fname = f"{base}_{suffix}{ext}"
        dest_path = os.path.join(dest_dir, fname)

    import shutil
    shutil.copy2(src_path, dest_path)

    digest = hashlib.sha256()
    size = 0
    with open(dest_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)

    record = {
        "id": asset_id or f"asset_{secrets.token_hex(6)}",
        "kind": kind,
        "file": os.path.join(_ASSET_SUBDIR, sub, fname),
        "hash": digest.hexdigest(),
        "size": size,
    }
    if external_ref:
        record["external_ref"] = external_ref

    index = _load_assets_index(work_dir, plot_id)
    index.append(record)
    _save_assets_index(work_dir, plot_id, index)
    return record


def _load_assets_index(work_dir: str, plot_id: str) -> list[dict[str, Any]]:
    p = _assets_index_path(work_dir, plot_id)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_assets_index(work_dir: str, plot_id: str, index: list[dict[str, Any]]) -> None:
    d = _assets_dir(work_dir, plot_id)
    os.makedirs(d, exist_ok=True)
    with open(_assets_index_path(work_dir, plot_id), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def build_manifest(work_dir: str, plot_id: str) -> dict[str, Any]:
    """生成剧情包 manifest（自包含元信息）。"""
    meta = get_plot(work_dir, plot_id) or {}
    nodes = get_plot_nodes(work_dir, plot_id)
    edges = get_plot_edges(work_dir, plot_id)
    assets = _load_assets_index(work_dir, plot_id)
    start = next((n.get("id") for n in nodes if n.get("type") == "start"), None)
    return {
        "plot_id": plot_id,
        "name": meta.get("name", plot_id),
        "version": "1.0.0",
        "resolution": "1920x1080",
        "start_node": start,
        "ui_theme": "default",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "asset_count": len(assets),
        "assets": assets,
        "dependencies": {
            "characters": sorted({
                n.get("bind_character_id") for n in nodes if n.get("bind_character_id")
            }),
            "world": next(
                (n.get("bind_world_id") for n in nodes if n.get("bind_world_id")), None
            ),
        },
    }



def _meta_path(work_dir: str, plot_id: str) -> str:
    return os.path.join(_plot_dir(work_dir, plot_id), "plot.json")


def _nodes_path(work_dir: str, plot_id: str) -> str:
    return os.path.join(_plot_dir(work_dir, plot_id), "nodes.json")


def _edges_path(work_dir: str, plot_id: str) -> str:
    return os.path.join(_plot_dir(work_dir, plot_id), "edges.json")


def _load_json(path: str) -> Any:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_plots(work_dir: str) -> list[dict[str, Any]]:
    base = os.path.join(work_dir, _PLOT_SUBDIR)
    if not os.path.isdir(base):
        return []
    result: list[dict[str, Any]] = []
    for name in sorted(os.listdir(base)):
        meta = _load_json(os.path.join(base, name, "plot.json"))
        if meta:
            result.append(meta)
    return result


def get_plot(work_dir: str, plot_id: str) -> dict[str, Any] | None:
    return _load_json(_meta_path(work_dir, plot_id))


def save_plot(work_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    plot_id = data.get("id", "")
    name = data.get("name", "").strip()
    if not name:
        raise DevKitError(400, "剧情名称不能为空", code="missing_name")

    if plot_id:
        existing = get_plot(work_dir, plot_id)
        if not existing:
            raise DevKitError(404, f"剧情不存在: {plot_id}", code="not_found")
    else:
        plot_id = _gen_id()

    now = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()  # type: ignore
    record = {
        "id": plot_id,
        "name": name,
        "description": data.get("description", ""),
        "genre": data.get("genre", ""),
        "setting": data.get("setting", ""),
        "tags": data.get("tags", []),
        "status": data.get("status", "draft"),
        "created_at": data.get("created_at", now) if plot_id else now,
        "updated_at": now,
    }

    d = _plot_dir(work_dir, plot_id)
    os.makedirs(d, exist_ok=True)
    _save_json(_meta_path(work_dir, plot_id), record)

    if "nodes" in data:
        nodes = data["nodes"]
        if not isinstance(nodes, list):
            raise DevKitError(400, "nodes 必须是列表", code="bad_nodes")
        _save_json(_nodes_path(work_dir, plot_id), nodes)

    if "edges" in data:
        edges = data["edges"]
        if not isinstance(edges, list):
            raise DevKitError(400, "edges 必须是列表", code="bad_edges")
        _save_json(_edges_path(work_dir, plot_id), edges)

    return record


def delete_plot(work_dir: str, plot_id: str) -> bool:
    d = _plot_dir(work_dir, plot_id)
    if not os.path.isdir(d):
        return False
    import shutil
    shutil.rmtree(d)
    return True


def get_plot_nodes(work_dir: str, plot_id: str) -> list[dict[str, Any]]:
    nodes = _load_json(_nodes_path(work_dir, plot_id))
    return nodes if isinstance(nodes, list) else []


def save_plot_node(work_dir: str, plot_id: str, node: dict[str, Any]) -> dict[str, Any]:
    if not get_plot(work_dir, plot_id):
        raise DevKitError(404, f"剧情不存在: {plot_id}", code="not_found")

    node = validate_node_payload(node)

    nodes = get_plot_nodes(work_dir, plot_id)
    node_id = node.get("id", _gen_id())
    node["id"] = node_id

    existing_idx = next((i for i, n in enumerate(nodes) if n.get("id") == node_id), -1)
    if existing_idx >= 0:
        nodes[existing_idx] = node
    else:
        nodes.append(node)

    _save_json(_nodes_path(work_dir, plot_id), nodes)
    return node


def delete_plot_node(work_dir: str, node_id: str, *, plot_id: str | None = None) -> bool:
    # UI 只发送节点 ID，因此先定位所属剧情。
    if plot_id is None:
        plot_id = _find_plot_with_node(work_dir, node_id)
    if plot_id is None:
        return False
    nodes = get_plot_nodes(work_dir, plot_id)
    before = len(nodes)
    nodes = [n for n in nodes if n.get("id") != node_id]
    if len(nodes) < before:
        _save_json(_nodes_path(work_dir, plot_id), nodes)
        return True
    return False


def _find_plot_with_node(work_dir: str, node_id: str) -> str | None:
    for plot in list_plots(work_dir):
        pid = plot.get("id", "")
        if any(n.get("id") == node_id for n in get_plot_nodes(work_dir, pid)):
            return pid
    return None


def get_plot_edges(work_dir: str, plot_id: str) -> list[dict[str, Any]]:
    edges = _load_json(_edges_path(work_dir, plot_id))
    return edges if isinstance(edges, list) else []


def save_plot_edge(work_dir: str, plot_id: str, edge: dict[str, Any]) -> dict[str, Any]:
    if not get_plot(work_dir, plot_id):
        raise DevKitError(404, f"剧情不存在: {plot_id}", code="not_found")

    edges = get_plot_edges(work_dir, plot_id)
    edge_id = edge.get("id", _gen_id())
    edge["id"] = edge_id

    existing_idx = next((i for i, e in enumerate(edges) if e.get("id") == edge_id), -1)
    if existing_idx >= 0:
        edges[existing_idx] = edge
    else:
        edges.append(edge)

    _save_json(_edges_path(work_dir, plot_id), edges)
    return edge


def delete_plot_edge(work_dir: str, edge_id: str, *, plot_id: str | None = None) -> bool:
    # UI 只发送边 ID，因此先定位所属剧情。
    if plot_id is None:
        plot_id = _find_plot_with_edge(work_dir, edge_id)
    if plot_id is None:
        return False
    edges = get_plot_edges(work_dir, plot_id)
    before = len(edges)
    edges = [e for e in edges if e.get("id") != edge_id]
    if len(edges) < before:
        _save_json(_edges_path(work_dir, plot_id), edges)
        return True
    return False


def _find_plot_with_edge(work_dir: str, edge_id: str) -> str | None:
    for plot in list_plots(work_dir):
        pid = plot.get("id", "")
        if any(e.get("id") == edge_id for e in get_plot_edges(work_dir, pid)):
            return pid
    return None


# ---------------------------------------------------------------------------
# P2 资产管理 API（GUI 用）
# ---------------------------------------------------------------------------

#: 各资产 kind 允许的扩展名（上传校验用）。
ASSET_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "sprite": (".png", ".jpg", ".jpeg", ".webp"),
    "bg": (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov"),
    "audio": (".wav", ".mp3", ".ogg", ".m4a", ".flac"),
}


def list_plot_assets(work_dir: str, plot_id: str) -> dict[str, list[dict[str, Any]]]:
    """按 kind 分组返回剧情包资产（GUI 三列展示）。"""
    grouped: dict[str, list[dict[str, Any]]] = {"sprite": [], "bg": [], "audio": []}
    for rec in _load_assets_index(work_dir, plot_id):
        kind = rec.get("kind")
        if kind in grouped:
            grouped[kind].append(rec)
    return grouped


def delete_plot_asset(work_dir: str, plot_id: str, asset_id: str) -> bool:
    """删除一个资产：移除文件 + 索引记录。"""
    index = _load_assets_index(work_dir, plot_id)
    target = next((r for r in index if r.get("id") == asset_id), None)
    if not target:
        return False
    fpath = os.path.join(_plot_dir(work_dir, plot_id), target["file"])
    if os.path.isfile(fpath):
        os.remove(fpath)
    index = [r for r in index if r.get("id") != asset_id]
    _save_assets_index(work_dir, plot_id, index)
    return True


def import_external_asset(
    work_dir: str, plot_id: str, kind: str,
    src_path: str, source_kind: str, source_id: str,
) -> dict[str, Any]:
    """导入绑定角色/世界的资产：拷贝进包并登记 external_ref 出处。"""
    if source_kind not in ("character", "world"):
        raise DevKitError(400, f"外部资产来源类型非法: {source_kind}", code="bad_source_kind")
    return register_asset(
        work_dir, plot_id, kind, src_path,
        external_ref={"kind": source_kind, "source_id": source_id,
                      "original_path": src_path},
    )


#: data URL 预览上限（8 MB——音频/图片预览足够，防止内存暴涨）。
_PREVIEW_MAX_BYTES = 8 * 1024 * 1024

_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".m4a": "audio/mp4", ".flac": "audio/flac",
}


def read_asset_data_url(work_dir: str, plot_id: str, asset_id: str) -> dict[str, Any]:
    """读取资产内容为 data URL（GUI 试听/预览用，限 8MB）。"""
    import base64
    index = _load_assets_index(work_dir, plot_id)
    target = next((r for r in index if r.get("id") == asset_id), None)
    if not target:
        raise DevKitError(404, f"资产不存在: {asset_id}", code="asset_not_found")
    fpath = os.path.join(_plot_dir(work_dir, plot_id), target["file"])
    if not os.path.isfile(fpath):
        raise DevKitError(404, f"资产文件缺失: {target['file']}", code="asset_file_missing")
    size = os.path.getsize(fpath)
    if size > _PREVIEW_MAX_BYTES:
        raise DevKitError(413, f"文件过大（{size} 字节），超过预览上限 8MB", code="too_large")
    ext = os.path.splitext(fpath)[1].lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    with open(fpath, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return {"data_url": f"data:{mime};base64,{b64}", "mime": mime, "size": size}


# ---------------------------------------------------------------------------
# 节点校验（对齐 Core 六类 + script 结构）
# ---------------------------------------------------------------------------

#: 与 Core ``plot_runtime.NODE_TYPES`` 一致的合法节点类型。
PLOT_NODE_TYPES = ("start", "branch", "event", "choice", "reward", "end")

#: 与 Core ``plot_runtime.STEP_TYPES`` 一致的演出步类型。
PLOT_STEP_TYPES = (
    "say", "narration", "setBg", "setFigure", "setBgm", "playSe",
    "playVoice", "setVar", "transition", "shake", "flash", "cg",
    "wait", "choice",
)

_FIGURE_POSITIONS = ("left", "center", "right")


def validate_node_payload(node: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化 GUI 提交的节点（对齐 Core 契约）。

    兼容旧字段：``name`` → ``title``、``content`` → ``description``。
    校验 ``type`` ∈ 六类；校验可选 ``script`` 列表的每一步结构。
    """
    # 旧字段映射（不丢数据）
    if "name" in node and "title" not in node:
        node["title"] = node.pop("name")
    if "content" in node and "description" not in node:
        node["description"] = node.pop("content")
    node.setdefault("title", "")
    node.setdefault("description", "")

    ntype = node.get("type", "event")
    if ntype not in PLOT_NODE_TYPES:
        raise DevKitError(
            400, f"节点类型非法: {ntype}（允许: {', '.join(PLOT_NODE_TYPES)}）",
            code="bad_node_type",
        )
    node["type"] = ntype

    script = node.get("script")
    if script is not None:
        if not isinstance(script, list):
            raise DevKitError(400, "script 必须是列表", code="bad_script")
        for i, step in enumerate(script):
            _validate_step_shape(step, i)
    return node


def _validate_step_shape(step: Any, idx: int) -> None:
    """轻量校验单个演出步（DevKit 侧重结构，语义由 Core 把关）。"""
    if not isinstance(step, dict):
        raise DevKitError(400, f"演出步 #{idx} 格式错误", code="bad_step")
    stype = step.get("type")
    if stype not in PLOT_STEP_TYPES:
        raise DevKitError(
            400, f"演出步 #{idx} 类型非法: {stype}",
            code="bad_step_type",
        )
    if stype == "say" and not str(step.get("content") or "").strip():
        raise DevKitError(400, f"演出步 #{idx}（say）台词不能为空", code="empty_line")
    if stype == "choice":
        choices = step.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DevKitError(400, f"演出步 #{idx}（choice）至少需要一个选项", code="no_choices")
        for j, ch in enumerate(choices):
            if not isinstance(ch, dict) or not str(ch.get("text") or "").strip():
                raise DevKitError(400, f"演出步 #{idx} 选项 #{j} 缺少文本", code="bad_choice")


def validate_plot_bindings(work_dir: str, plot_id: str) -> dict[str, Any]:
    """对照真实的角色与世界验证节点/连线绑定（C3 AC-2）。

    节点和连线可能携带 ``bind_character_id`` / ``bind_world_id`` /
    ``bind_event_id``。当绑定指向开发者工作区中不存在的角色/世界时，
    该绑定即为*断裂*。此检查仅为建议性（只报告问题），绝不删除任何
    内容——UI 会展示警告，让开发者在提交前修复悬空引用。
    """
    from devkit.character_editor import get_character
    from devkit.world_editor import get_world, list_world_events

    def _check_binding(binding: dict[str, Any], problems: list[str], where: str) -> None:
        cid = binding.get("bind_character_id")
        if cid and get_character(work_dir, cid) is None:
            problems.append(f"{where}: 绑定的角色不存在 ({cid})")
        wid = binding.get("bind_world_id")
        if wid:
            world = get_world(work_dir, wid)
            if world is None:
                problems.append(f"{where}: 绑定的世界不存在 ({wid})")
            else:
                eid = binding.get("bind_event_id")
                if eid:
                    events = list_world_events(work_dir, wid)
                    if not any(e.get("id") == eid for e in events):
                        problems.append(f"{where}: 绑定的世界事件不存在 ({eid})")

    problems: list[str] = []
    for node in get_plot_nodes(work_dir, plot_id):
        _check_binding(node, problems, f"节点 {node.get('id', '?')}")
    for edge in get_plot_edges(work_dir, plot_id):
        _check_binding(edge, problems, f"连线 {edge.get('id', '?')}")

    return {"plot_id": plot_id, "ok": len(problems) == 0, "problems": problems}


def export_plot_for_submit(work_dir: str, plot_id: str) -> dict[str, Any]:
    """导出剧情包为提交文件条目（P3：自包含打包）。

    包含：plot.json / nodes.json / edges.json / manifest.json /
    assets_index.json / 全部已登记资产文件。

    - 资产文件缺失时跳过并记入 ``warnings``（不阻断导出）
    - 无资产的旧剧情照常导出（向后兼容）
    - 带 ``external_ref`` 的资产已在包内（上传时拷贝），manifest 记录出处；
      绑定角色/世界的*原生*资产目录扫描待角色侧定稿后补（见 notes）。
    """
    import logging

    meta = get_plot(work_dir, plot_id)
    if not meta:
        raise DevKitError(404, f"剧情不存在: {plot_id}", code="not_found")

    logger = logging.getLogger("devkit.plot_editor")
    warnings: list[str] = []
    files: list[dict[str, Any]] = []
    prefix = f"plots/{plot_id}"
    plot_dir = _plot_dir(work_dir, plot_id)

    def _add(src_path: str, arcname: str) -> None:
        if os.path.isfile(src_path):
            files.append({
                "path": src_path,
                "arcname": arcname,
                "size": os.path.getsize(src_path),
            })
        else:
            warnings.append(f"缺失文件已跳过: {arcname}")
            logger.warning("export %s: missing file skipped: %s", plot_id, src_path)

    # 1. 核心三件套
    _add(_meta_path(work_dir, plot_id), f"{prefix}/plot.json")
    _add(_nodes_path(work_dir, plot_id), f"{prefix}/nodes.json")
    _add(_edges_path(work_dir, plot_id), f"{prefix}/edges.json")

    # 2. 剧情包自描述 manifest（与归档根部提交 manifest 不同层）
    manifest = build_manifest(work_dir, plot_id)
    manifest["warnings"] = warnings
    manifest_path = os.path.join(plot_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    files.append({
        "path": manifest_path,
        "arcname": f"{prefix}/manifest.json",
        "size": os.path.getsize(manifest_path),
    })

    # 3. 资产索引 + 资产文件（自包含：下载即离线可用）
    assets = _load_assets_index(work_dir, plot_id)
    if assets:
        idx_path = _assets_index_path(work_dir, plot_id)
        files.append({
            "path": idx_path,
            "arcname": f"{prefix}/assets/assets_index.json",
            "size": os.path.getsize(idx_path),
        })
        for rec in assets:
            rel = rec.get("file", "")
            _add(os.path.join(plot_dir, rel), f"{prefix}/{rel}")

    return {
        "target_kind": "plot",
        "files": files,
        "payload": {
            "notes": meta.get("description", ""),
            "files": [f["path"] for f in files],
        },
        "manifest": manifest,
    }
