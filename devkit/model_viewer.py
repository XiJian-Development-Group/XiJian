"""开发者工具的 3D 模型查看器后端。

管理本地 3D 模型文件引用（VRM、GLB 等），并提供文件列表
供 UI 通过 three.js 渲染。
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
        # 允许注册 FBX，但标记为需要转换为 VRM。
        # UI 会显示警告：FBX 无法直接预览，必须使用 Blender/Unity
        # （UniVRM）在外部转换为 VRM 1.0。
        pass  # 继续注册
    elif ext not in (".vrm", ".glb", ".gltf"):
        raise DevKitError(400, f"不支持的模型格式: {ext}（仅支持 .vrm / .glb / .gltf / .fbx）", code="bad_format")
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
        "needs_conversion": ext == ".fbx",
    }
    # C2.8 AC-3：强制模型大小限制（必须 < 50 MB，建议 < 20 MB）
    size_mb = entry["size_bytes"] / (1024 * 1024)
    if size_mb > 50:
        raise DevKitError(
            400,
            f"模型文件过大: {size_mb:.1f} MB，超过 50 MB 上限（推荐 < 20 MB）",
            code="model_too_large",
        )
    elif size_mb > 20:
        # 仅警告 - 不阻止
        entry["size_warning"] = f"模型大小 {size_mb:.1f} MB 超过推荐的 20 MB，可能影响加载性能"

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


#: UI 的 three.js 加载器处理的 3D 格式的 MIME 类型。
_FORMAT_MIMES = {
    ".vrm": "model/gltf-binary",   # VRM 0.x / 1.0 是带 extras 的 GLB
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".fbx": "application/octet-stream",  # three.js 中无法直接查看 FBX
}


def _read_gltf_json(path: str) -> dict[str, Any] | None:
    """尽力提取 glTF/VRM JSON 描述。

    同时处理纯 ``.gltf``（JSON 文本）和二进制 ``.glb`` / ``.vrm``
    （GLB 容器：12 字节头 + JSON 块）。任何解析失败都返回 ``None``，
    以便调用方报告清晰的验证错误。
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
            # 块 0：长度（uint32 小端）+ 类型（4 字节，'JSON'）
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
    """对照 VRM 1.0 规范验证已注册的模型（C2.8 AC-4）。

    ``.vrm`` / ``.glb`` / ``.gltf`` 会被解析并检查 VRM 扩展
    （``VRM`` / ``VRMC_vrm`` / ``VRMC_vrm_animation``）。``.fbx``
    无法直接作为 VRM 验证——必须先转换，因此该检查返回非阻塞警告
    而非硬性失败。

    返回 ``{"ok": bool, "format": str, "errors": [...], "warnings": [...]}``。
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

    # 更深入的 VRM 1.0 验证：检查必需的 VRM 扩展字段
    vrm_ext_key = next((k for k in ("VRM", "VRMC_vrm") if k in gltf.get("extensions", {})), None)
    if vrm_ext_key:
        vrm_ext = gltf["extensions"][vrm_ext_key]
        errors = []
        warnings = []

        # 检查 specVersion
        spec_version = vrm_ext.get("specVersion", "1.0")
        if not spec_version.startswith("1."):
            warnings.append(f"VRM 规范版本为 {spec_version}，建议使用 1.0")

        # 检查 meta（VRM 1.0 中必需）
        meta = vrm_ext.get("meta")
        if not meta:
            errors.append("缺少 VRM meta 信息（标题、版本、作者等）")
        else:
            if not meta.get("title"):
                warnings.append("VRM meta 缺少 title（标题）")
            if not meta.get("version"):
                warnings.append("VRM meta 缺少 version（版本号）")
            if not meta.get("author"):
                warnings.append("VRM meta 缺少 author（作者）")

        # 检查 humanoid（动作重定向所必需）
        humanoid = vrm_ext.get("humanoid")
        if not humanoid:
            warnings.append("缺少 humanoid 信息（动作重定向可能受影响）")
        else:
            human_bones = humanoid.get("humanBones", [])
            if not human_bones:
                warnings.append("humanoid.humanBones 为空（动作重定向可能受影响）")

        # 检查 firstPerson（可选但建议）
        first_person = vrm_ext.get("firstPerson")
        if not first_person:
            warnings.append("缺少 firstPerson 设置（第一人称视角配置）")

        # 检查 blendShapeMaster（表情可选但建议）
        blend_shape = vrm_ext.get("blendShapeMaster")
        if not blend_shape:
            warnings.append("缺少 blendShapeMaster（表情/BlendShape 可能无法使用）")

        if errors:
            return {"ok": False, "format": ext.lstrip("."), "errors": errors, "warnings": warnings}

        if warnings:
            return {"ok": True, "format": ext.lstrip("."), "errors": [], "warnings": warnings}

    return {"ok": True, "format": ext.lstrip("."), "errors": [], "warnings": []}


def read_model_bytes(work_dir: str, model_id: str) -> dict[str, Any] | None:
    """返回已注册模型的原始文件字节 + MIME。

    JS 预览器调用此函数来绕开 ``file://`` CORS 墙——pywebview 的
    WKWebView 不会 ``fetch()`` 本地文件路径，因此我们通过 ``js_api``
    桥传递 base64，让它构建 object URL。

    如果模型 id 未知则返回 ``None``。调用方应负责呈现清晰的错误；
    这里不抛出异常，因为 UI 将“模型消失”视为软失败（重新列出）。
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
    """C2.8 AI 生成 3D 模型（VRM 1.0）。

    通过 AI 生成服务抽象层执行，fallback 链：
    远程服务（Tripo/Meshy）→ 本地管线 → HuggingFace 下载 → 确定性生成（永远成功）。
    生成成功后注册到模型索引并返回注册记录。

    返回格式与 ``register_model`` 一致（含 id / path / format / size_bytes）。
    """
    if not description.strip():
        raise DevKitError(400, "描述文本不能为空", code="empty_description")

    from devkit.ai_generation.model_generation import (
        ModelGenerationStatus,
        create_model_generation_service,
    )

    service = create_model_generation_service()
    job = service.generate_and_wait(description, name=name)
    if job.status != ModelGenerationStatus.SUCCEEDED:
        raise DevKitError(
            502,
            f"AI 生成失败: {job.error_message or '未知错误'}",
            code="generation_failed",
        )
    if not job.result_path or not os.path.isfile(job.result_path):
        raise DevKitError(502, "AI 生成成功但结果文件缺失", code="generation_failed")

    # 生成结果移入 work_dir 的模型目录并注册
    models_dir = os.path.join(work_dir, _MODELS_SUBDIR)
    os.makedirs(models_dir, exist_ok=True)
    dest = os.path.join(models_dir, os.path.basename(job.result_path))
    import shutil
    if os.path.abspath(dest) != os.path.abspath(job.result_path):
        shutil.copy2(job.result_path, dest)
    else:
        dest = job.result_path

    return register_model(work_dir, dest)


def _download_model_from_hf(description: str) -> str | None:
    """尝试从 Hugging Face 下载匹配的模型。

    为中国用户使用 HF_MIRROR 环境变量（默认为 hf-mirror.com）。
    成功时返回本地文件路径，否则返回 None。
    """
    try:
        from huggingface_hub import hf_hub_download, login
    except ImportError:
        return None

    # 搜索与描述匹配的模型（简化——实际上你会使用 HF API 搜索）
    # 目前，我们尝试几个已知的角色模型仓库
    hf_token = os.environ.get("HF_TOKEN")
    mirror = os.environ.get("HF_MIRROR", "https://hf-mirror.com")

    if hf_token:
        try:
            login(token=hf_token)
        except Exception:
            pass

    # 已知的 VRM/GLB 角色模型仓库
    repos = [
        "p1atdev/dart-3d-character",
        "shinkon/vrm-characters",
        "hf-hub/vrm-models",
    ]

    for repo in repos:
        try:
            # 尝试找到匹配的 .vrm 或 .glb 文件
            # 这是简化版本——实际实现会按标签搜索
            files = hf_hub_download(
                repo_id=repo,
                filename="model.vrm",  # 或 model.glb
                token=hf_token,
                endpoint=mirror,
            )
            if files and os.path.isfile(files):
                return files
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# C2.8：FBX/GLB → VRM 转换（外部工具编排）
# ---------------------------------------------------------------------------

def convert_fbx_to_vrm(
    fbx_path: str,
    output_path: str | None = None,
    tool: str = "univrm",
) -> str:
    """使用外部工具（Blender/UniVRM/bvh2vrm）将 FBX 转换为 VRM。

    这是一个调用外部 CLI 工具的包装器。实际转换必须在外部完成；
    此函数只负责编排调用。

    支持的工具：
    - "univrm"：Unity 的 UniVRM CLI（需要 Unity + UniVRM 包）
    - "blender"：带 VRM 插件的 Blender Python 脚本
    - "vrm-validator"：VRM 验证器 CLI（仅用于验证）

    返回输出的 VRM 文件路径。
    """
    if not os.path.isfile(fbx_path):
        raise DevKitError(400, f"FBX 文件不存在: {fbx_path}", code="file_not_found")

    ext = os.path.splitext(fbx_path)[1].lower()
    if ext != ".fbx":
        raise DevKitError(400, "输入文件必须是 .fbx 格式", code="bad_format")

    if output_path is None:
        output_path = os.path.splitext(fbx_path)[0] + ".vrm"

    if tool == "univrm":
        # 带 UniVRM 的 Unity 命令行批处理模式
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
    """使用 bvh2vrm 将 BVH 动作捕捉数据转换为 VRM 动画。

    参数：
        bvh_path: .bvh 动作文件路径
        vrm_template: 用于应用动画的 VRM 模型路径
        output_path: 输出的 .vrm 或 .vrmc_animation 路径

    返回输出文件路径。
    """
    if not os.path.isfile(bvh_path):
        raise DevKitError(400, f"BVH 文件不存在: {bvh_path}", code="file_not_found")
    if not os.path.isfile(vrm_template):
        raise DevKitError(400, f"VRM 模板不存在: {vrm_template}", code="file_not_found")

    if output_path is None:
        output_path = os.path.splitext(bvh_path)[0] + ".vrm"

    # 如果可用，使用 bvh2vrm（Python 包）
    try:
        import bvh2vrm
        bvh2vrm.convert(bvh_path, vrm_template, output_path)
        return output_path
    except ImportError:
        pass

    # 回退：如果 CLI 可用则调用
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
    """导入 FBX 文件，可选择转换为 VRM。

    返回已注册的模型条目。
    """
    if convert_to_vrm:
        vrm_path = os.path.splitext(fbx_path)[0] + ".vrm"
        convert_fbx_to_vrm(fbx_path, vrm_path, tool)
        return register_model(work_dir, vrm_path)
    else:
        # 仅注册 FBX（将被标记为需要转换）
        return register_model(work_dir, fbx_path)


def import_glb_model(
    work_dir: str,
    glb_path: str,
) -> dict[str, Any]:
    """导入 GLB/GLTF 模型（可能兼容 VRM）。"""
    return register_model(work_dir, glb_path)


def import_vrm_model(
    work_dir: str,
    vrm_path: str,
) -> dict[str, Any]:
    """直接导入 VRM 模型。"""
    return register_model(work_dir, vrm_path)
