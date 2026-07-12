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

    # C2.9 AC-3 — capture the imported motion's skeleton joint names so the
    # UI / VRM runtime can verify bone-name matching before playback.
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
    """Parse the joint names from a BVH file's HIERARCHY section.

    Returns the ordered list of bone names, or ``None`` if the file is not a
    parseable BVH.  Used to surface the skeleton so the VRM runtime can check
    bone-name compatibility (C2.9 AC-3).
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
# C2.9: BVH→VRM conversion and keyframe editing
# ---------------------------------------------------------------------------


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


def validate_motion_skeleton(
    motion_path: str,
    vrm_model_path: str,
) -> dict[str, Any]:
    """Validate that a motion's skeleton matches a VRM model (C2.9 AC-3).

    Args:
        motion_path: Path to .bvh / .fbx / .glb motion file
        vrm_model_path: Path to the VRM model to check against

    Returns a dict with:
        - ok: bool — whether the skeleton matches
        - motion_joints: list[str] — bone names from motion
        - vrm_joints: list[str] — bone names from VRM
        - missing_in_vrm: list[str] — bones in motion but not VRM
        - extra_in_vrm: list[str] — bones in VRM but not motion
        - errors: list[str] — validation errors
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

    # Extract joints from motion file
    ext = os.path.splitext(motion_path)[1].lower()
    motion_joints = []

    if ext == ".bvh":
        motion_joints = _extract_bvh_joints(motion_path) or []
    elif ext in (".fbx", ".glb", ".gltf"):
        # For FBX/GLB, try to extract from glTF JSON
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

    # Extract joints from VRM model
    try:
        gltf = _read_gltf_json(vrm_model_path)
        if gltf:
            nodes = gltf.get("nodes", [])
            vrm_joints = [n.get("name", "") for n in nodes if n.get("name")]
            result["vrm_joints"] = vrm_joints
    except Exception as e:
        result["errors"].append(f"无法读取 VRM 模型: {e}")
        return result

    # Compare
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
    """Validate keyframe data structure.

    Returns (ok, errors).
    """
    errors: list[str] = []
    if not isinstance(keyframes, list):
        return False, ["keyframes 必须是列表"]

    seen_frames: dict[str, set[int]] = {}  # bone -> set of frames

    for i, kf in enumerate(keyframes):
        if not isinstance(kf, dict):
            errors.append(f"关键帧 #{i}: 必须是对象")
            continue

        # Required: frame (int >= 0)
        frame = kf.get("frame")
        if not isinstance(frame, int) or frame < 0:
            errors.append(f"关键帧 #{i}: frame 必须是非负整数")
        else:
            # Check for duplicate frame on same bone
            bone = kf.get("bone")
            if bone:
                if bone not in seen_frames:
                    seen_frames[bone] = set()
                if frame in seen_frames[bone]:
                    errors.append(f"关键帧 #{i}: 骨骼 {bone} 在帧 {frame} 有重复关键帧")
                seen_frames[bone].add(frame)

        # Required: bone (non-empty string)
        bone = kf.get("bone")
        if not isinstance(bone, str) or not bone.strip():
            errors.append(f"关键帧 #{i}: bone 必须是非空字符串")

        # Optional: position [x, y, z] (list of 3 floats)
        pos = kf.get("position")
        if pos is not None:
            if not (isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, (int, float)) for v in pos)):
                errors.append(f"关键帧 #{i}: position 必须是 [x, y, z] 格式的三个数字")

        # Optional: rotation [x, y, z, w] (quaternion, list of 4 floats)
        rot = kf.get("rotation")
        if rot is not None:
            if not (isinstance(rot, list) and len(rot) == 4 and all(isinstance(v, (int, float)) for v in rot)):
                errors.append(f"关键帧 #{i}: rotation 必须是 [x, y, z, w] 格式的四元数")
            else:
                # Check quaternion is normalized (approximately)
                import math
                norm = math.sqrt(sum(v * v for v in rot))
                if abs(norm - 1.0) > 0.01:
                    errors.append(f"关键帧 #{i}: rotation 四元数未归一化 (模长={norm:.4f})")

        # Optional: scale [x, y, z] (list of 3 floats)
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
    """Edit keyframe parameters for a motion (C2.9 AC-1).

    Args:
        motion_id: ID of the motion to edit
        work_dir: Work directory
        character_id: Character ID
        keyframes: List of keyframe dicts, each with:
            - frame: int — frame number (>= 0)
            - bone: str — bone name
            - position: [x, y, z] — optional
            - rotation: [x, y, z, w] — optional (quaternion)
            - scale: [x, y, z] — optional

    Returns the updated motion record.
    """
    # Validate keyframes
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
    """Get keyframes for a motion (for playback in UI)."""
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
    """Apply keyframes to a VRM model, generating a VRM with animation (VRMC_vrm_animation).

    This creates a new VRM file with the keyframe animation baked in as a
    VRMC_vrm_animation extension, which can be played back in three.js/VRM viewers.

    Args:
        work_dir: Work directory
        character_id: Character ID
        motion_id: Motion ID with keyframes
        vrm_model_id: Target VRM model ID (must be registered)
        output_path: Output path (optional, auto-generated if not provided)

    Returns the path to the generated VRM file.
    """
    # Load motion with keyframes
    motions = _load_motions(work_dir, character_id)
    motion = next((m for m in motions if m.get("id") == motion_id), None)
    if not motion:
        raise DevKitError(404, f"动效不存在: {motion_id}", code="not_found")

    keyframes = motion.get("keyframes", [])
    if not keyframes:
        raise DevKitError(400, "该动效没有关键帧数据", code="no_keyframes")

    # Load VRM model
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

    # Read VRM as glTF JSON
    gltf = _read_gltf_json(vrm_path)
    if gltf is None:
        raise DevKitError(400, "无法解析 VRM 文件", code="parse_failed")

    # Ensure extensions structure
    if "extensions" not in gltf:
        gltf["extensions"] = {}
    if "extensionsUsed" not in gltf:
        gltf["extensionsUsed"] = []

    # Build VRMC_vrm_animation from keyframes
    # Group keyframes by bone
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

    # Sort each bone's tracks by frame
    for bone in bone_tracks:
        bone_tracks[bone].sort(key=lambda t: t["frame"])

    # Create animation clips for VRMC_vrm_animation
    # This is a simplified version - real implementation would create proper
    # glTF animation samplers/channels and reference them in VRMC_vrm_animation
    animation = {
        "name": motion.get("name", "custom_animation"),
        "tracks": [
            {
                "bone": bone,
                "keyframes": tracks,
            }
            for bone, tracks in bone_tracks.items()
        ],
        "frame_rate": 30,  # default
        "duration": max((kf.get("frame", 0) for kf in keyframes), default=0) / 30.0,
    }

    if "VRMC_vrm_animation" not in gltf["extensions"]:
        gltf["extensions"]["VRMC_vrm_animation"] = {}
    gltf["extensions"]["VRMC_vrm_animation"]["animations"] = [animation]
    if "VRMC_vrm_animation" not in gltf["extensionsUsed"]:
        gltf["extensionsUsed"].append("VRMC_vrm_animation")

    # Write output VRM
    if output_path is None:
        import tempfile
        output_path = os.path.join(
            tempfile.gettempdir(),
            f"xijian_motion_{motion_id}_{os.path.basename(vrm_path)}"
        )

    # For GLB/VRM binary, we need to rebuild the binary. This is complex.
    # For now, write as .gltf (JSON) which can be loaded by three.js.
    # A full implementation would use pygltflib or similar to write GLB.
    out_ext = os.path.splitext(output_path)[1].lower()
    if out_ext == ".gltf":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(gltf, f, ensure_ascii=False, separators=(",", ":"))
    else:
        # Write as JSON for now (user can convert to GLB externally)
        json_path = os.path.splitext(output_path)[0] + ".gltf"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(gltf, f, ensure_ascii=False, separators=(",", ":"))
        # Also save the original binary path for reference
        # In a full implementation, we'd embed the binary buffer here

    return output_path if out_ext == ".gltf" else json_path


def convert_bvh_to_vrm_public(
    work_dir: str,
    bvh_path: str,
    vrm_template: str,
    output_path: str | None = None,
) -> str:
    """Public wrapper for BVH→VRM conversion."""
    return convert_bvh_to_vrm(bvh_path, vrm_template, output_path)


def validate_motion_skeleton_public(
    work_dir: str,
    motion_id: str,
    vrm_model_id: str,
) -> dict[str, Any]:
    """Validate motion skeleton against a VRM model."""
    # Load motion
    motions = _load_motions(work_dir, "")
    motion = next((m for m in motions if m.get("id") == motion_id), None)
    if not motion:
        raise DevKitError(404, f"动效不存在: {motion_id}", code="not_found")

    # Load VRM model
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
    """Public wrapper for keyframe editing."""
    return edit_motion_keyframes(motion_id, work_dir, character_id, keyframes)
