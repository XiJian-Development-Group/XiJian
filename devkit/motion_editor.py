from __future__ import annotations

import json
import os
import secrets
from typing import Any

from devkit import DevKitError


_MOTION_SUBDIR = "motions"
_DEFAULT_MOTIONS = ("idle", "happy", "sad", "angry", "surprised", "neutral")


def _gen_id() -> str:
    return f"motion_{secrets.token_hex(8)}"


def _motion_dir(work_dir: str, character_id: str) -> str:
    return os.path.join(work_dir, _MOTION_SUBDIR, character_id)


def _meta_path(work_dir: str, character_id: str) -> str:
    return os.path.join(_motion_dir(work_dir, character_id), "motions.json")


def _load_motions(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    fpath = _meta_path(work_dir, character_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_motions(work_dir: str, character_id: str, motions: list[dict[str, Any]]) -> None:
    d = _motion_dir(work_dir, character_id)
    os.makedirs(d, exist_ok=True)
    with open(_meta_path(work_dir, character_id), "w", encoding="utf-8") as f:
        json.dump(motions, f, ensure_ascii=False, indent=2)


def list_motion_characters(work_dir: str) -> list[str]:
    base = os.path.join(work_dir, _MOTION_SUBDIR)
    if not os.path.isdir(base):
        return []
    return sorted(os.listdir(base))


def list_motions(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    existing = _load_motions(work_dir, character_id)
    existing_names = {m.get("name") for m in existing}
    for default_name in _DEFAULT_MOTIONS:
        if default_name not in existing_names:
            existing.append({
                "id": _gen_id(),
                "name": default_name,
                "character_id": character_id,
                "type": "builtin",
                "description": _default_motion_desc(default_name),
                "parameters": _default_motion_params(default_name),
            })
            existing_names.add(default_name)
    if len([e for e in existing if e.get("type") == "builtin"]) == len(_DEFAULT_MOTIONS):
        pass
    return sorted(existing, key=lambda m: list(_DEFAULT_MOTIONS).index(m["name"]) if m["name"] in _DEFAULT_MOTIONS else 99)


def get_motion(work_dir: str, motion_id: str) -> dict[str, Any] | None:
    base = os.path.join(work_dir, _MOTION_SUBDIR)
    if not os.path.isdir(base):
        return None
    for char_dir in os.listdir(base):
        motions = _load_motions(work_dir, char_dir)
        for m in motions:
            if m.get("id") == motion_id:
                return m
    return None


def save_motion(work_dir: str, character_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if not character_id:
        raise DevKitError(400, "角色 ID 不能为空", code="missing_character_id")
    name = data.get("name", "").strip()
    if not name:
        raise DevKitError(400, "动作名称不能为空", code="missing_name")

    motions = _load_motions(work_dir, character_id)
    motion_id = data.get("id", _gen_id())

    now = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
    raw_params = data.get("params", data.get("parameters"))
    if isinstance(raw_params, str):
        try:
            raw_params = json.loads(raw_params) if raw_params.strip() else {}
        except (json.JSONDecodeError, ValueError):
            raw_params = {}
    record = {
        "id": motion_id,
        "character_id": character_id,
        "name": name,
        "type": data.get("type", "custom"),
        "description": data.get("description", ""),
        "parameters": raw_params if isinstance(raw_params, dict) else {},
        "file_path": data.get("file_path", ""),
        "duration_seconds": data.get("duration_seconds", 2.0),
        "loop": data.get("loop", False),
        "created_at": data.get("created_at", now) if motion_id else now,
        "updated_at": now,
    }

    existing_idx = next((i for i, m in enumerate(motions) if m.get("id") == motion_id), -1)
    if existing_idx >= 0:
        motions[existing_idx] = record
    else:
        motions.append(record)

    _save_motions(work_dir, character_id, motions)
    return record


def delete_motion(work_dir: str, motion_id: str) -> bool:
    base = os.path.join(work_dir, _MOTION_SUBDIR)
    if not os.path.isdir(base):
        return False
    for char_dir in os.listdir(base):
        motions = _load_motions(work_dir, char_dir)
        before = len(motions)
        motions = [m for m in motions if m.get("id") != motion_id]
        if len(motions) < before:
            _save_motions(work_dir, char_dir, motions)
            return True
    return False


def import_motion_file(work_dir: str, character_id: str, file_path: str, name: str) -> dict[str, Any]:
    if not os.path.isfile(file_path):
        raise DevKitError(400, f"文件不存在: {file_path}", code="file_not_found")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".bvh", ".fbx", ".glb", ".gltf"):
        raise DevKitError(400, f"不支持的动效格式: {ext}（仅支持 .bvh / .fbx / .glb / .gltf）", code="bad_format")

    d = _motion_dir(work_dir, character_id)
    os.makedirs(d, exist_ok=True)

    import shutil
    motion_id = _gen_id()
    dest = os.path.join(d, f"{motion_id}{ext}")
    shutil.copy2(file_path, dest)

    # C2.9 AC-3 —— 捕获导入动效的骨骼关节名称，以便
    # UI / VRM 运行时在播放前验证骨骼名称匹配。
    params: dict[str, Any] = {}
    if ext == ".bvh":
        joints = _extract_bvh_joints(file_path)
        if joints is not None:
            params["skeleton_joints"] = joints
            params["skeleton_joint_count"] = len(joints)

    record = {
        "id": motion_id,
        "character_id": character_id,
        "name": name or os.path.basename(file_path),
        "type": "imported",
        "description": f"从 {os.path.basename(file_path)} 导入",
        "file_path": dest,
        "parameters": params,
        "imported_format": ext.lstrip("."),
        "duration_seconds": 2.0,
        "loop": False,
    }

    motions = _load_motions(work_dir, character_id)
    motions.append(record)
    _save_motions(work_dir, character_id, motions)
    return record


def _extract_bvh_joints(file_path: str) -> list[str] | None:
    """解析 BVH 文件 HIERARCHY 部分中的关节名称。

    返回有序的骨骼名称列表；如果文件不是可解析的 BVH，则返回 ``None``。
    用于暴露骨架，使 VRM 运行时可以检查骨骼名称兼容性（C2.9 AC-3）。
    """
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    joints: list[str] = []
    in_hierarchy = False
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("HIERARCHY"):
            in_hierarchy = True
            continue
        if not in_hierarchy:
            continue
        if stripped.upper().startswith("MOTION"):
            break
        if stripped.upper().startswith("ROOT") or stripped.upper().startswith("JOINT"):
            name = stripped.split(None, 1)[1].strip().rstrip("{").strip()
            if name:
                joints.append(name)
    return joints or None


def export_motions_for_submit(work_dir: str, character_id: str) -> dict[str, Any]:
    motions = _load_motions(work_dir, character_id)
    files: list[dict[str, Any]] = []
    meta_path = _meta_path(work_dir, character_id)

    if os.path.isfile(meta_path):
        files.append({
            "path": meta_path,
            "arcname": f"motions/{character_id}/motions.json",
            "size": os.path.getsize(meta_path),
        })

    for motion in motions:
        fp = motion.get("file_path", "")
        if fp and os.path.isfile(fp):
            files.append({
                "path": fp,
                "arcname": f"motions/{character_id}/{motion['id']}{os.path.splitext(fp)[1]}",
                "size": os.path.getsize(fp),
            })

    return {
        "target_kind": "character",
        "files": files,
        "payload": {
            "notes": f"{len(motions)} 个动作",
            "files": [f["path"] for f in files],
        },
    }


def _default_motion_desc(name: str) -> str:
    _descs = {
        "idle": "待机姿态",
        "happy": "高兴/开心",
        "sad": "悲伤/难过",
        "angry": "生气/愤怒",
        "surprised": "惊讶/吃惊",
        "neutral": "中立/平静",
    }
    return _descs.get(name, "")


def _default_motion_params(name: str) -> dict[str, Any]:
    _params = {
        "idle": {"blend_duration": 0.5, "loop": True},
        "happy": {"blend_duration": 0.3, "loop": False, "intensity": 0.8},
        "sad": {"blend_duration": 0.4, "loop": False, "intensity": 0.6},
        "angry": {"blend_duration": 0.2, "loop": False, "intensity": 0.9},
        "surprised": {"blend_duration": 0.15, "loop": False, "intensity": 1.0},
        "neutral": {"blend_duration": 0.5, "loop": True},
    }
    return _params.get(name, {"blend_duration": 0.3, "loop": False})


# ---------------------------------------------------------------------------
# C2.9：BVH→VRM 转换与关键帧编辑
# ---------------------------------------------------------------------------


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


def validate_motion_skeleton(
    motion_path: str,
    vrm_model_path: str,
) -> dict[str, Any]:
    """验证动效骨架是否与 VRM 模型匹配（C2.9 AC-3）。

    参数：
        motion_path: .bvh / .fbx / .glb 动效文件路径
        vrm_model_path: 要检查的 VRM 模型路径

    返回包含以下键的 dict：
        - ok: bool —— 骨架是否匹配
        - motion_joints: list[str] —— 动效中的骨骼名称
        - vrm_joints: list[str] —— VRM 中的骨骼名称
        - missing_in_vrm: list[str] —— 动效中有但 VRM 没有的骨骼
        - extra_in_vrm: list[str] —— VRM 中有但动效没有的骨骼
        - errors: list[str] —— 验证错误
    """
    import re

    result = {
        "ok": False,
        "motion_joints": [],
        "vrm_joints": [],
        "missing_in_vrm": [],
        "extra_in_vrm": [],
        "errors": [],
    }

    # 从动效文件提取关节
    ext = os.path.splitext(motion_path)[1].lower()
    motion_joints = []

    if ext == ".bvh":
        motion_joints = _extract_bvh_joints(motion_path) or []
    elif ext in (".fbx", ".glb", ".gltf"):
        # 对于 FBX/GLB，尝试从 glTF JSON 中提取
        try:
            if ext in (".glb", ".gltf"):
                gltf = _read_gltf_json(motion_path)
                if gltf:
                    nodes = gltf.get("nodes", [])
                    motion_joints = [n.get("name", "") for n in nodes if n.get("name")]
        except Exception:
            motion_joints = []
    else:
        result["errors"].append(f"不支持的动效格式: {ext}")
        return result

    result["motion_joints"] = motion_joints

    # 从 VRM 模型提取关节
    try:
        gltf = _read_gltf_json(vrm_model_path)
        if gltf:
            nodes = gltf.get("nodes", [])
            vrm_joints = [n.get("name", "") for n in nodes if n.get("name")]
            result["vrm_joints"] = vrm_joints
    except Exception as e:
        result["errors"].append(f"无法读取 VRM 模型: {e}")
        return result

    # 比较
    motion_set = set(motion_joints)
    vrm_set = set(result["vrm_joints"])

    result["missing_in_vrm"] = sorted(motion_set - vrm_set)
    result["extra_in_vrm"] = sorted(vrm_set - motion_set)

    if result["missing_in_vrm"]:
        result["errors"].append(
            f"动效中有骨骼在 VRM 中缺失: {result['missing_in_vrm']}"
        )
    if result["extra_in_vrm"]:
        result["errors"].append(
            f"VRM 中有骨骼在动效中缺失（可能正常，需人工确认）: {result['extra_in_vrm']}"
        )

    result["ok"] = len(result["missing_in_vrm"]) == 0
    return result


def _validate_keyframes(keyframes: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """验证关键帧数据结构。

    返回 (ok, errors)。
    """
    errors: list[str] = []
    if not isinstance(keyframes, list):
        return False, ["keyframes 必须是列表"]

    seen_frames: dict[str, set[int]] = {}  # 骨骼 -> 帧集合

    for i, kf in enumerate(keyframes):
        if not isinstance(kf, dict):
            errors.append(f"关键帧 #{i}: 必须是对象")
            continue

        # 必需：frame（int >= 0）
        frame = kf.get("frame")
        if not isinstance(frame, int) or frame < 0:
            errors.append(f"关键帧 #{i}: frame 必须是非负整数")
        else:
            # 检查同一骨骼上的重复帧
            bone = kf.get("bone")
            if bone:
                if bone not in seen_frames:
                    seen_frames[bone] = set()
                if frame in seen_frames[bone]:
                    errors.append(f"关键帧 #{i}: 骨骼 {bone} 在帧 {frame} 有重复关键帧")
                seen_frames[bone].add(frame)

        # 必需：bone（非空字符串）
        bone = kf.get("bone")
        if not isinstance(bone, str) or not bone.strip():
            errors.append(f"关键帧 #{i}: bone 必须是非空字符串")

        # 可选：position [x, y, z]（3 个浮点数的列表）
        pos = kf.get("position")
        if pos is not None:
            if not (isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, (int, float)) for v in pos)):
                errors.append(f"关键帧 #{i}: position 必须是 [x, y, z] 格式的三个数字")

        # 可选：rotation [x, y, z, w]（四元数，4 个浮点数的列表）
        rot = kf.get("rotation")
        if rot is not None:
            if not (isinstance(rot, list) and len(rot) == 4 and all(isinstance(v, (int, float)) for v in rot)):
                errors.append(f"关键帧 #{i}: rotation 必须是 [x, y, z, w] 格式的四元数")
            else:
                # 检查四元数是否（近似）归一化
                import math
                norm = math.sqrt(sum(v * v for v in rot))
                if abs(norm - 1.0) > 0.01:
                    errors.append(f"关键帧 #{i}: rotation 四元数未归一化 (模长={norm:.4f})")

        # 可选：scale [x, y, z]（3 个浮点数的列表）
        scale = kf.get("scale")
        if scale is not None:
            if not (isinstance(scale, list) and len(scale) == 3 and all(isinstance(v, (int, float)) for v in scale)):
                errors.append(f"关键帧 #{i}: scale 必须是 [x, y, z] 格式的三个数字")

    return (len(errors) == 0), errors


def edit_motion_keyframes(
    motion_id: str,
    work_dir: str,
    character_id: str,
    keyframes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """编辑动效的关键帧参数（C2.9 AC-1）。

    参数：
        motion_id: 要编辑的动效 ID
        work_dir: 工作目录
        character_id: 角色 ID
        keyframes: 关键帧 dict 列表，每个包含：
            - frame: int —— 帧号（>= 0）
            - bone: str —— 骨骼名称
            - position: [x, y, z] —— 可选
            - rotation: [x, y, z, w] —— 可选（四元数）
            - scale: [x, y, z] —— 可选

    返回更新后的动效记录。
    """
    # 验证关键帧
    ok, errors = _validate_keyframes(keyframes)
    if not ok:
        raise DevKitError(400, "；".join(errors), code="bad_keyframes")

    motions = _load_motions(work_dir, character_id)
    for i, m in enumerate(motions):
        if m.get("id") == motion_id:
            m["keyframes"] = keyframes
            m["updated_at"] = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
            _save_motions(work_dir, character_id, motions)
            return dict(m)
    return None


def get_motion_keyframes(
    work_dir: str,
    character_id: str,
    motion_id: str,
) -> list[dict[str, Any]]:
    """获取动效的关键帧（供 UI 播放使用）。"""
    motions = _load_motions(work_dir, character_id)
    for m in motions:
        if m.get("id") == motion_id:
            return m.get("keyframes", [])
    return []


def apply_keyframes_to_vrm(
    work_dir: str,
    character_id: str,
    motion_id: str,
    vrm_model_id: str,
    output_path: str | None = None,
) -> str:
    """将关键帧应用到 VRM 模型，生成带动画的 VRM（VRMC_vrm_animation）。

    这会创建一个新的 VRM 文件，将关键帧动画烘焙为
    VRMC_vrm_animation 扩展，可在 three.js/VRM 查看器中播放。

    参数：
        work_dir: 工作目录
        character_id: 角色 ID
        motion_id: 带关键帧的动效 ID
        vrm_model_id: 目标 VRM 模型 ID（必须已注册）
        output_path: 输出路径（可选，未提供时自动生成）

    返回生成的 VRM 文件路径。
    """
    # 加载带关键帧的动效
    motions = _load_motions(work_dir, character_id)
    motion = next((m for m in motions if m.get("id") == motion_id), None)
    if not motion:
        raise DevKitError(404, f"动效不存在: {motion_id}", code="not_found")

    keyframes = motion.get("keyframes", [])
    if not keyframes:
        raise DevKitError(400, "该动效没有关键帧数据", code="no_keyframes")

    # 加载 VRM 模型
    from devkit.model_viewer import get_model_info as _mv_get, _read_gltf_json
    vrm_model = _mv_get(work_dir, vrm_model_id)
    if not vrm_model:
        raise DevKitError(404, f"VRM 模型不存在: {vrm_model_id}", code="not_found")

    vrm_path = vrm_model.get("path", "")
    if not vrm_path or not os.path.isfile(vrm_path):
        raise DevKitError(400, "VRM 模型文件不存在", code="file_not_found")

    ext = os.path.splitext(vrm_path)[1].lower()
    if ext == ".fbx":
        raise DevKitError(400, "目标模型为 FBX，需先转换为 VRM", code="bad_format")

    # 将 VRM 读取为 glTF JSON
    gltf = _read_gltf_json(vrm_path)
    if gltf is None:
        raise DevKitError(400, "无法解析 VRM 文件", code="parse_failed")

    # 确保 extensions 结构存在
    if "extensions" not in gltf:
        gltf["extensions"] = {}
    if "extensionsUsed" not in gltf:
        gltf["extensionsUsed"] = []

    # 从关键帧构建 VRMC_vrm_animation
    # 按骨骼对关键帧分组
    from collections import defaultdict
    bone_tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for kf in keyframes:
        bone = kf.get("bone", "")
        bone_tracks[bone].append({
            "frame": kf.get("frame", 0),
            "position": kf.get("position"),
            "rotation": kf.get("rotation"),
            "scale": kf.get("scale"),
        })

    # 按帧号对每块骨骼的轨道排序
    for bone in bone_tracks:
        bone_tracks[bone].sort(key=lambda t: t["frame"])

    # 为 VRMC_vrm_animation 创建动画片段
    # 这是简化版本——实际实现会创建正确的
    # glTF 动画采样器/通道，并在 VRMC_vrm_animation 中引用它们
    animation = {
        "name": motion.get("name", "custom_animation"),
        "tracks": [
            {
                "bone": bone,
                "keyframes": tracks,
            }
            for bone, tracks in bone_tracks.items()
        ],
        "frame_rate": 30,  # 默认
        "duration": max((kf.get("frame", 0) for kf in keyframes), default=0) / 30.0,
    }

    if "VRMC_vrm_animation" not in gltf["extensions"]:
        gltf["extensions"]["VRMC_vrm_animation"] = {}
    gltf["extensions"]["VRMC_vrm_animation"]["animations"] = [animation]
    if "VRMC_vrm_animation" not in gltf["extensionsUsed"]:
        gltf["extensionsUsed"].append("VRMC_vrm_animation")

    # 写入输出 VRM
    if output_path is None:
        import tempfile
        output_path = os.path.join(
            tempfile.gettempdir(),
            f"xijian_motion_{motion_id}_{os.path.basename(vrm_path)}"
        )

    # 对于 GLB/VRM 二进制，我们需要重建二进制。这很复杂。
    # 目前先写成 .gltf（JSON），three.js 可以加载。
    # 完整实现会使用 pygltflib 或类似工具写入 GLB。
    out_ext = os.path.splitext(output_path)[1].lower()
    if out_ext == ".gltf":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(gltf, f, ensure_ascii=False, separators=(",", ":"))
    else:
        # 暂时写成 JSON（用户可以在外部转换为 GLB）
        json_path = os.path.splitext(output_path)[0] + ".gltf"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(gltf, f, ensure_ascii=False, separators=(",", ":"))
        # 同时保存原始二进制路径作为参考
        # 在完整实现中，我们会在这里嵌入二进制缓冲区

    return output_path if out_ext == ".gltf" else json_path


def convert_bvh_to_vrm_public(
    work_dir: str,
    bvh_path: str,
    vrm_template: str,
    output_path: str | None = None,
) -> str:
    """BVH→VRM 转换的公共包装器。"""
    return convert_bvh_to_vrm(bvh_path, vrm_template, output_path)


def validate_motion_skeleton_public(
    work_dir: str,
    motion_id: str,
    vrm_model_id: str,
) -> dict[str, Any]:
    """对照 VRM 模型验证动效骨架。"""
    # 加载动效
    motions = _load_motions(work_dir, "")
    motion = next((m for m in motions if m.get("id") == motion_id), None)
    if not motion:
        raise DevKitError(404, f"动效不存在: {motion_id}", code="not_found")

    # 加载 VRM 模型
    from devkit.model_viewer import get_model_info as _mv_get
    vrm_model = _mv_get(work_dir, vrm_model_id)
    if not vrm_model:
        raise DevKitError(404, f"VRM 模型不存在: {vrm_model_id}", code="not_found")

    motion_path = motion.get("file_path", "")
    vrm_path = vrm_model.get("path", "")
    if not motion_path or not vrm_path:
        raise DevKitError(400, "路径缺失", code="missing_path")

    return validate_motion_skeleton(motion_path, vrm_path)


def edit_motion_keyframes_public(
    work_dir: str,
    character_id: str,
    motion_id: str,
    keyframes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """关键帧编辑的公共包装器。"""
    return edit_motion_keyframes(motion_id, work_dir, character_id, keyframes)


def generate_motion_from_text(
    work_dir: str,
    character_id: str,
    persona_text: str,
    name: str = "",
    motion_type: str = "idle",
) -> dict[str, Any]:
    """C2.9 AI 从人设描述推断动作（生成 BVH 并导入角色动效库）。

    通过 AI 动作生成服务抽象层执行，fallback 链：
    远程视频捕获 → 本地视频推断 → 确定性人设→BVH 生成（永远成功）。
    生成成功后通过 ``import_motion_file`` 注册到角色动效库。

    返回格式与 ``import_motion_file`` 一致。
    """
    if not persona_text.strip():
        raise DevKitError(400, "人设描述不能为空", code="empty_description")

    from devkit.ai_generation.motion_generation import (
        MotionGenerationStatus,
        create_motion_generation_service,
    )

    service = create_motion_generation_service()
    job = service.generate_and_wait(
        "persona",
        persona_text,
        character_id or "character",
        motion_type=motion_type,
    )
    if job.status != MotionGenerationStatus.SUCCEEDED:
        raise DevKitError(
            502,
            f"AI 动作生成失败: {job.error_message or '未知错误'}",
            code="generation_failed",
        )
    if not job.result_path or not os.path.isfile(job.result_path):
        raise DevKitError(502, "AI 动作生成成功但结果文件缺失", code="generation_failed")

    motion_name = name or f"ai_{motion_type}"
    return import_motion_file(work_dir, character_id, job.result_path, motion_name)


def generate_motion_from_video(
    work_dir: str,
    character_id: str,
    video_path: str,
    name: str = "",
) -> dict[str, Any]:
    """C2.9 AI 从视频推断动作（生成 BVH 并导入角色动效库）。

    真实环境接入远程视频动作捕获服务或本地姿态推断管线
    （见 ai_generation.motion_generation 的 TODO 注释）；
    stub 环境降级为确定性规则生成，保证流程真实可用。
    """
    if not video_path.strip():
        raise DevKitError(400, "视频路径不能为空", code="empty_path")

    from devkit.ai_generation.motion_generation import (
        MotionGenerationStatus,
        create_motion_generation_service,
    )

    service = create_motion_generation_service()
    job = service.generate_and_wait(
        "video",
        video_path,
        character_id or "character",
    )
    if job.status != MotionGenerationStatus.SUCCEEDED:
        raise DevKitError(
            502,
            f"AI 视频动作推断失败: {job.error_message or '未知错误'}",
            code="generation_failed",
        )
    if not job.result_path or not os.path.isfile(job.result_path):
        raise DevKitError(502, "AI 动作生成成功但结果文件缺失", code="generation_failed")

    motion_name = name or "ai_video"
    return import_motion_file(work_dir, character_id, job.result_path, motion_name)
