from __future__ import annotations

import json
import os
import secrets
from typing import Any

from devkit import DevKitError


_PLOT_SUBDIR = "plots"


def _gen_id() -> str:
    return f"plot_{secrets.token_hex(8)}"


def _plot_dir(work_dir: str, plot_id: str) -> str:
    return os.path.join(work_dir, _PLOT_SUBDIR, plot_id)


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
    # The UI only sends the node id, so locate the owning plot first.
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
    # The UI only sends the edge id, so locate the owning plot first.
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


def validate_plot_bindings(work_dir: str, plot_id: str) -> dict[str, Any]:
    """Validate node/edge bindings against real characters & worlds (C3 AC-2).

    Nodes and edges may carry ``bind_character_id`` / ``bind_world_id`` /
    ``bind_event_id``.  A binding is *broken* when it points at a
    character/world that does not exist in the developer's workspace.  The
    check is advisory (it reports problems) and never deletes anything — the
    UI surfaces the warnings so the developer can fix dangling references
    before submitting.
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
    meta = get_plot(work_dir, plot_id)
    if not meta:
        raise DevKitError(404, f"剧情不存在: {plot_id}", code="not_found")

    files: list[dict[str, Any]] = []
    plot_dir = _plot_dir(work_dir, plot_id)

    meta_file = _meta_path(work_dir, plot_id)
    if os.path.isfile(meta_file):
        files.append({
            "path": meta_file,
            "arcname": f"plots/{plot_id}/plot.json",
            "size": os.path.getsize(meta_file),
        })

    nodes_file = _nodes_path(work_dir, plot_id)
    if os.path.isfile(nodes_file):
        files.append({
            "path": nodes_file,
            "arcname": f"plots/{plot_id}/nodes.json",
            "size": os.path.getsize(nodes_file),
        })

    edges_file = _edges_path(work_dir, plot_id)
    if os.path.isfile(edges_file):
        files.append({
            "path": edges_file,
            "arcname": f"plots/{plot_id}/edges.json",
            "size": os.path.getsize(edges_file),
        })

    return {
        "target_kind": "plot",
        "files": files,
        "payload": {
            "notes": meta.get("description", ""),
            "files": [f["path"] for f in files],
        },
    }
