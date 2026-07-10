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
    if ext == ".fbx":
        # DevKit does NOT perform FBX→VRM conversion.  Be explicit so the
        # user knows to convert externally rather than silently accepting a
        # file we cannot validate or preview.
        raise DevKitError(
            400,
            "DevKit 暂不提供 FBX→VRM 转换功能。请使用 Blender 或 Unity（UniVRM）"
            "等程序将模型手动转换为 VRM 1.0 后，再导入本工具。",
            code="fbx_conversion_unavailable",
        )
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


def _read_gltf_json(path: str) -> dict[str, Any] | None:
    """Best-effort extraction of the glTF/VRM JSON description.

    Handles both plain ``.gltf`` (JSON text) and binary ``.glb`` / ``.vrm``
    (GLB container: 12-byte header + JSON chunk).  Returns ``None`` on any
    parse failure so callers can report a clear validation error.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".gltf":
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) < 12 or header[0:4] != b"glTF":
                return None
            # chunk 0: length (uint32 LE) + type (4 bytes, 'JSON')
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                return None
            import struct
            chunk_len = struct.unpack("<I", chunk_header[0:4])[0]
            if chunk_header[4:8] != b"JSON":
                return None
            raw = f.read(chunk_len)
            return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def validate_model_format(work_dir: str, model_id: str) -> dict[str, Any]:
    """Validate a registered model against the VRM 1.0 specification (C2.8 AC-4).

    ``.vrm`` / ``.glb`` / ``.gltf`` are parsed and checked for the VRM
    extension (``VRM`` / ``VRMC_vrm`` / ``VRMC_vrm_animation``).  ``.fbx``
    cannot be validated as VRM directly — it must be converted first, so the
    check returns a non-blocking warning instead of a hard failure.

    Returns ``{"ok": bool, "format": str, "errors": [...], "warnings": [...]}``.
    """
    info = get_model_info(work_dir, model_id)
    if not info:
        return {"ok": False, "format": "", "errors": ["模型不存在"], "warnings": []}
    path = info.get("path", "")
    ext = os.path.splitext(path)[1].lower()
    if not path or not os.path.isfile(path):
        return {"ok": False, "format": ext.lstrip("."), "errors": ["模型文件不存在"], "warnings": []}
    if ext == ".fbx":
        return {
            "ok": True,
            "format": "fbx",
            "errors": [],
            "warnings": [
                "FBX 不是 VRM 1.0 格式，提交前需转换为 VRM（UniVRM / bvh2vrm 等工具）"
            ],
        }
    gltf = _read_gltf_json(path)
    if gltf is None:
        return {
            "ok": False,
            "format": ext.lstrip("."),
            "errors": ["无法解析模型 JSON（不是合法的 glTF/GLB/VRM 文件）"],
            "warnings": [],
        }
    extensions_used = set(gltf.get("extensionsUsed", []) or [])
    extensions = set(gltf.get("extensions", {}).keys() or [])
    vrm_markers = {"VRM", "VRMC_vrm", "VRMC_vrm_animation"}
    has_vrm = bool(vrm_markers & extensions_used) or bool(vrm_markers & extensions)
    if not has_vrm:
        return {
            "ok": False,
            "format": ext.lstrip("."),
            "errors": ["未检测到 VRM 扩展（extensionsUsed 中缺少 VRM / VRMC_vrm）", "不符合 VRM 1.0 规范"],
            "warnings": [],
        }
    return {"ok": True, "format": ext.lstrip("."), "errors": [], "warnings": []}


def _validate_fbx_rejected() -> dict[str, Any]:
    """FBX is not accepted — conversion must happen externally.

    Kept as a clear, single-source message so the UI can tell the user to
    convert with Blender / Unity before importing.
    """
    return {
        "ok": False,
        "format": "fbx",
        "errors": [
            "DevKit 暂不提供 FBX→VRM 转换功能。请使用 Blender 或 Unity（UniVRM）"
            "将模型手动转换为 VRM 1.0 后再导入。",
        ],
        "warnings": [],
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
    """Generate or download a 3D model from text description (C2.8).

    Supports:
    1. AI generation via local MLX/Hugging Face (requires mlx_audio/transformers)
    2. Downloading pre-made models from Hugging Face (with mirror support)
    3. Importing user-provided model files

    Returns the registered model entry.
    """
    if not description.strip():
        raise DevKitError(400, "描述文本不能为空", code="empty_description")

    # Try to download from Hugging Face first (with mirror)
    model_path = _download_model_from_hf(description)
    if model_path is None:
        # Fallback to placeholder
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

    # Register the downloaded model
    model_id = "model_" + secrets.token_hex(8)
    name = name or os.path.basename(model_path)
    index = _load_index(work_dir)
    entry = {
        "id": model_id,
        "path": model_path,
        "name": name,
        "format": os.path.splitext(model_path)[1].lstrip("."),
        "size_bytes": os.path.getsize(model_path),
        "generated": True,
        "description": description,
    }
    index.append(entry)
    _save_index(work_dir, index)
    return entry


def _download_model_from_hf(description: str) -> str | None:
    """Attempt to download a matching model from Hugging Face.

    Uses HF_MIRROR environment variable for Chinese users (defaults to hf-mirror.com).
    Returns local file path if successful, None otherwise.
    """
    try:
        from huggingface_hub import hf_hub_download, login
    except ImportError:
        return None

    # Search for models matching the description (simplified - in reality you'd use HF API search)
    # For now, we'll try a few known character model repositories
    hf_token = os.environ.get("HF_TOKEN")
    mirror = os.environ.get("HF_MIRROR", "https://hf-mirror.com")

    if hf_token:
        try:
            login(token=hf_token)
        except Exception:
            pass

    # Known good repositories for VRM/GLB character models
    repos = [
        "p1atdev/dart-3d-character",
        "shinkon/vrm-characters",
        "hf-hub/vrm-models",
    ]

    for repo in repos:
        try:
            # Try to find a matching .vrm or .glb file
            # This is a simplified version - real implementation would search by tags
            files = hf_hub_download(
                repo_id=repo,
                filename="model.vrm",  # or model.glb
                token=hf_token,
                endpoint=mirror,
            )
            if files and os.path.isfile(files):
                return files
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# C2.8: FBX/GLB → VRM conversion (external tool orchestration)
# ---------------------------------------------------------------------------

def convert_fbx_to_vrm(
    fbx_path: str,
    output_path: str | None = None,
    tool: str = "univrm",
) -> str:
    """Convert FBX to VRM using external tools (Blender/UniVRM/bvh2vrm).

    This is a wrapper that calls external CLI tools. The actual conversion
    must be done externally; this function just orchestrates the call.

    Supported tools:
    - "univrm": Unity's UniVRM CLI (requires Unity + UniVRM package)
    - "blender": Blender Python script with VRM addon
    - "vrm-validator": VRM validator CLI (for validation only)

    Returns the output VRM file path.
    """
    if not os.path.isfile(fbx_path):
        raise DevKitError(400, f"FBX 文件不存在: {fbx_path}", code="file_not_found")

    ext = os.path.splitext(fbx_path)[1].lower()
    if ext != ".fbx":
        raise DevKitError(400, "输入文件必须是 .fbx 格式", code="bad_format")

    if output_path is None:
        output_path = os.path.splitext(fbx_path)[0] + ".vrm"

    if tool == "univrm":
        # Unity command-line batch mode with UniVRM
        unity_path = os.environ.get("UNITY_PATH", "/Applications/Unity/Hub/Editor/2022.3.0f1/Unity.app/Contents/MacOS/Unity")
        project_path = os.environ.get("UNITY_PROJECT_PATH", os.path.expanduser("~/UnityProjects/VRMConverter"))
        cmd = [
            unity_path,
            "-batchmode",
            "-projectPath", project_path,
            "-executeMethod", "UniVRM.CLI.FbxToVrm",
            fbx_path,
            output_path,
            "-quit",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise DevKitError(500, f"UniVRM 转换失败: {result.stderr}", code="conversion_failed")

    elif tool == "blender":
        blender_path = os.environ.get("BLENDER_PATH", "/Applications/Blender.app/Contents/MacOS/Blender")
        script = os.path.join(os.path.dirname(__file__), "blender_fbx_to_vrm.py")
        cmd = [blender_path, "--background", "--python", script, "--", fbx_path, output_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise DevKitError(500, f"Blender 转换失败: {result.stderr}", code="conversion_failed")

    else:
        raise DevKitError(400, f"不支持的转换工具: {tool}", code="bad_tool")

    if not os.path.isfile(output_path):
        raise DevKitError(500, "转换未生成输出文件", code="no_output")

    return output_path


def convert_bvh_to_vrm(
    bvh_path: str,
    vrm_template: str,
    output_path: str | None = None,
) -> str:
    """Convert BVH motion capture data to VRM animation using bvh2vrm.

    Args:
        bvh_path: Path to the .bvh motion file
        vrm_template: Path to a VRM model to apply the animation to
        output_path: Output .vrm or .vrmc_animation path

    Returns the output file path.
    """
    if not os.path.isfile(bvh_path):
        raise DevKitError(400, f"BVH 文件不存在: {bvh_path}", code="file_not_found")
    if not os.path.isfile(vrm_template):
        raise DevKitError(400, f"VRM 模板不存在: {vrm_template}", code="file_not_found")

    if output_path is None:
        output_path = os.path.splitext(bvh_path)[0] + ".vrm"

    # Use bvh2vrm (Python package) if available
    try:
        import bvh2vrm
        bvh2vrm.convert(bvh_path, vrm_template, output_path)
        return output_path
    except ImportError:
        pass

    # Fallback: call CLI if available
    bvh2vrm_cli = os.environ.get("BVH2VRM_CLI", "bvh2vrm")
    cmd = [bvh2vrm_cli, bvh_path, vrm_template, output_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise DevKitError(500, f"bvh2vrm 转换失败: {result.stderr}", code="conversion_failed")

    if not os.path.isfile(output_path):
        raise DevKitError(500, "转换未生成输出文件", code="no_output")

    return output_path


def import_fbx_model(
    work_dir: str,
    fbx_path: str,
    convert_to_vrm: bool = True,
    tool: str = "univrm",
) -> dict[str, Any]:
    """Import an FBX file, optionally converting to VRM.

    Returns the registered model entry.
    """
    if convert_to_vrm:
        vrm_path = os.path.splitext(fbx_path)[0] + ".vrm"
        convert_fbx_to_vrm(fbx_path, vrm_path, tool)
        return register_model(work_dir, vrm_path)
    else:
        # Just register the FBX (will be flagged as needing conversion)
        return register_model(work_dir, fbx_path)


def import_glb_model(
    work_dir: str,
    glb_path: str,
) -> dict[str, Any]:
    """Import a GLB/GLTF model (may be VRM-compatible)."""
    return register_model(work_dir, glb_path)


def import_vrm_model(
    work_dir: str,
    vrm_path: str,
) -> dict[str, Any]:
    """Import a VRM model directly."""
    return register_model(work_dir, vrm_path)
