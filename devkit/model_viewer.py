"""3D model viewer backend for the Developer Kit.

Manages local 3D model file references (VRM, GLB, etc.) and
provides a file listing for the UI to render via three.js.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

from devkit import DevKitError


_MODELS_SUBDIR = "models"


def _gen_id() -> str:
    return f"model_{secrets.token_hex(8)}"


def _models_index_path(work_dir: str) -> str:
    base = os.path.join(work_dir, _MODELS_SUBDIR)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "index.json")


def _load_index(work_dir: str) -> list[dict[str, Any]]:
    fpath = _models_index_path(work_dir)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(work_dir: str, index: list[dict[str, Any]]) -> None:
    fpath = _models_index_path(work_dir)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def list_models(work_dir: str) -> list[dict[str, Any]]:
    return _load_index(work_dir)


def register_model(work_dir: str, path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise DevKitError(400, f"文件不存在: {path}", code="file_not_found")
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".vrm", ".glb", ".gltf"):
        raise DevKitError(400, f"不支持的模型格式: {ext}（仅支持 .vrm / .glb / .gltf）", code="bad_format")
    index = _load_index(work_dir)
    for entry in index:
        if entry.get("path") == path:
            return entry
    model_id = _gen_id()
    entry = {
        "id": model_id,
        "path": path,
        "name": os.path.basename(path),
        "format": ext.lstrip("."),
        "size_bytes": os.path.getsize(path),
    }
    index.append(entry)
    _save_index(work_dir, index)
    return entry


def unregister_model(work_dir: str, model_id: str) -> bool:
    index = _load_index(work_dir)
    before = len(index)
    index = [e for e in index if e.get("id") != model_id]
    if len(index) < before:
        _save_index(work_dir, index)
        return True
    return False


def get_model_info(work_dir: str, model_id: str) -> dict[str, Any] | None:
    index = _load_index(work_dir)
    for entry in index:
        if entry.get("id") == model_id:
            return dict(entry)
    return None


#: MIME types for the 3D formats the UI's three.js loader handles.
_FORMAT_MIMES = {
    ".vrm": "model/gltf-binary",   # VRM 0.x / 1.0 are GLB with extras
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}


def read_model_bytes(work_dir: str, model_id: str) -> dict[str, Any] | None:
    """Return the raw file bytes + MIME for a registered model.

    The JS previewer calls this to dodge the ``file://`` CORS wall —
    pywebview's WKWebView will not ``fetch()`` a local file path, so
    we hand it base64 over the ``js_api`` bridge and let it build an
    object URL.

    Returns ``None`` if the model id is unknown.  The caller is
    expected to surface a clean error; we do not raise here because
    the UI treats "model vanished" as a soft failure (re-list).
    """
    info = get_model_info(work_dir, model_id)
    if not info:
        return None
    path = info.get("path", "")
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        raw = f.read()
    import base64
    return {
        "id": model_id,
        "name": info.get("name", ""),
        "format": info.get("format", ext.lstrip(".")),
        "path": path,
        "size_bytes": len(raw),
        "mime": _FORMAT_MIMES.get(ext, "application/octet-stream"),
        "data_b64": base64.b64encode(raw).decode("ascii"),
    }


def export_model_for_submit(work_dir: str, model_id: str) -> dict[str, Any]:
    info = get_model_info(work_dir, model_id)
    if not info:
        raise DevKitError(404, f"模型不存在: {model_id}", code="not_found")

    path = info.get("path", "")
    if not path or not os.path.isfile(path):
        raise DevKitError(400, "模型文件不存在", code="file_not_found")

    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)

    return {
        "target_kind": "character",
        "files": [{
            "path": path,
            "arcname": f"models/{model_id}{ext}",
            "size": size,
        }],
        "payload": {
            "notes": f"3D 模型: {info.get('name', '')} ({info.get('format', '')})",
            "files": [path],
        },
    }


def generate_model_from_text(
    work_dir: str,
    description: str,
    name: str = "",
) -> dict[str, Any]:
    if not description.strip():
        raise DevKitError(400, "描述文本不能为空", code="empty_description")

    import tempfile
    model_id = "model_" + secrets.token_hex(8)
    name = name or f"AI生成_{model_id[:8]}"

    placeholder_path = os.path.join(tempfile.gettempdir(), f"xijian_ai_model_{model_id}.glb")
    with open(placeholder_path, "w") as f:
        f.write(json.dumps({
            "asset": {"version": "2.0", "generator": "XiJian AI Model Generator"},
            "generated_from": description,
            "model_id": model_id,
            "note": "Placeholder — full AI VRM generation requires MLX backend",
        }))

    index = _load_index(work_dir)
    entry = {
        "id": model_id,
        "path": placeholder_path,
        "name": name,
        "format": "glb",
        "size_bytes": os.path.getsize(placeholder_path),
        "generated": True,
        "description": description,
    }
    index.append(entry)
    _save_index(work_dir, index)
    return entry
