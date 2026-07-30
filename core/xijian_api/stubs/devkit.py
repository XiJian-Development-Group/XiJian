"""DevKit loader — bridge between the standalone DevKit process and the
core API runtime (Plan C preview/test environment).

The DevKit saves its creations to a well-known work directory
(``~/Library/Application Support/XiJian/DevKit/`` by default).  This
module scans that directory, parses the saved JSON/Markdown files, and
loads them into the core's in-memory state so the user can interact
with their work-in-progress characters/worlds through the normal core
API endpoints.

Design
------
* **No DevKit modifications required** — core reads from the DevKit's
  existing save directory directly.
* **Source tagging** — every record loaded via this module gets
  ``_devkit_source=True`` and ``devkit_original_id=<id>`` so the
  ``unload`` / ``reload`` paths can find and replace them.
* **Replace on reload** — when loading an item that was previously
  loaded from the devkit, the old record is replaced (not duplicated).
* **Preview** — full devkit data is returned as-is for the UI to render.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_character_id, gen_world_id
from xijian_api.utils.time import now_ts

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

DEFAULT_DEVKIT_DIR = os.path.expanduser(
    "~/Library/Application Support/XiJian/DevKit"
)
ENV_OVERRIDE = "XIJIAN_DEVKIT_DIR"


def get_devkit_dir() -> str:
    """Return the DevKit work directory path.

    Checks ``XIJIAN_DEVKIT_DIR`` env var first, falls back to the
    hardcoded default.
    """
    return os.environ.get(ENV_OVERRIDE, DEFAULT_DEVKIT_DIR)


def is_available() -> bool:
    """Check whether the DevKit work directory exists and is readable."""
    dk = get_devkit_dir()
    return os.path.isdir(dk)


_LOCK = threading.Lock()

# Constant keys used to tag devkit-loaded records.
_SOURCE_TAG = "_devkit_source"
_ORIGINAL_ID_TAG = "devkit_original_id"

# DevKit subdirectory names — must match devkit/character_editor.py etc.
_CHARACTERS_SUBDIR = "characters"
_WORLDS_SUBDIR = "worlds"
_MEMORIES_SUBDIR = "memories"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _subdir(kind: str) -> str | None:
    mapping = {"character": _CHARACTERS_SUBDIR, "world": _WORLDS_SUBDIR}
    return mapping.get(kind)


def _devkit_json_path(work_dir: str, kind: str, item_id: str) -> str | None:
    sub = _subdir(kind)
    if sub is None:
        return None
    return os.path.join(work_dir, sub, item_id, f"{kind}.json")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return None


def _scan_dir(work_dir: str, sub: str) -> list[dict]:
    """Scan a devkit subdirectory and return parsed JSON records."""
    base = os.path.join(work_dir, sub)
    if not os.path.isdir(base):
        return []
    results: list[dict] = []
    for entry in sorted(os.listdir(base)):
        dirpath = os.path.join(base, entry)
        if not os.path.isdir(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if fname.endswith(".json"):
                fpath = os.path.join(dirpath, fname)
                data = _read_json(fpath)
                if data is not None:
                    results.append(data)
                    break  # only the main JSON per id
    return results


# ---------------------------------------------------------------------------
# Scan — list what the DevKit has saved
# ---------------------------------------------------------------------------


def scan_characters() -> list[dict]:
    """Return metadata for every character the DevKit has saved.

    Each entry is a flat dict drawn from the devkit's ``character.json``:
    ``id``, ``name``, ``display_name``, ``description``, ``updated_at``.
    """
    dk = get_devkit_dir()
    if not os.path.isdir(os.path.join(dk, _CHARACTERS_SUBDIR)):
        return []
    return _scan_dir(dk, _CHARACTERS_SUBDIR)


def scan_worlds() -> list[dict]:
    """Return metadata for every world the DevKit has saved."""
    dk = get_devkit_dir()
    if not os.path.isdir(os.path.join(dk, _WORLDS_SUBDIR)):
        return []
    return _scan_dir(dk, _WORLDS_SUBDIR)


# ---------------------------------------------------------------------------
# Load — pull a single item from devkit into core runtime
# ---------------------------------------------------------------------------


def load_character(devkit_id: str) -> dict | None:
    """Load a character from the DevKit save directory into the core
    runtime.

    Steps
    -----
    1. Read ``{work_dir}/characters/{devkit_id}/character.json``.
    2. Read ``{work_dir}/characters/{devkit_id}/persona.md`` (optional).
    3. If ``{work_dir}/memories/{devkit_id}/entries.json`` exists,
       populate the character's initial memory entries.
    4. Create a full core record in ``state.characters`` with all
       available devkit fields.
    5. Configure ``state.memory_configs`` if the devkit record carries
       ``memory_config``.
    6. Mark the record with ``_devkit_source=True`` and
       ``devkit_original_id=devkit_id``.

    If the character was previously loaded (same ``devkit_original_id``),
    the old record is replaced.

    Returns the created core record, or ``None`` if the devkit data
    could not be read.
    """
    with _LOCK:
        return _load_character_impl(devkit_id)


def _load_character_impl(devkit_id: str) -> dict | None:
    dk = get_devkit_dir()
    char_json_path = _devkit_json_path(dk, "character", devkit_id)
    if not char_json_path or not os.path.isfile(char_json_path):
        _LOGGER.warning("DevKit character %s not found at %s", devkit_id, char_json_path)
        return None

    raw = _read_json(char_json_path)
    if raw is None:
        return None

    # Read optional persona markdown.
    persona_path = os.path.join(
        dk, _CHARACTERS_SUBDIR, devkit_id, "persona.md"
    )
    persona_doc = _read_text(persona_path) or raw.get("persona_doc", "")

    now = now_ts()

    # Build the core character record.  Carry over ALL devkit fields
    # so the preview can render everything the devkit stores, plus
    # add missing fields expected by core stubs.
    record = {
        "id": raw.get("id", devkit_id),
        "object": "character",
        "name": raw.get("name", "Unnamed"),
        "display_name": raw.get("display_name") or raw.get("name", "Unnamed"),
        "description": raw.get("description", ""),
        "persona_doc": persona_doc,
        "voice_profile": raw.get("voice_profile"),
        "default_emotion": raw.get("default_emotion", "neutral"),
        "language_style": raw.get("language_style", ""),
        "tags": list(raw.get("tags", [])),
        "models": list(raw.get("models", [])),
        "assigned_memory_pack": raw.get("assigned_memory_pack", ""),
        "assigned_voice_pack": raw.get("assigned_voice_pack", ""),
        "assigned_model": raw.get("assigned_model", ""),
        "assigned_world": raw.get("assigned_world", ""),
        "character_config": raw.get("character_config", {}),
        "loaded": True,
        "created_at": raw.get("created_at", now),
        "updated_at": now,
        _SOURCE_TAG: True,
        _ORIGINAL_ID_TAG: devkit_id,
    }

    # Remove any previously loaded devkit record with the same origin.
    _remove_devkit_records("character", devkit_id)

    # Store it.
    state.characters[record["id"]] = record

    # Populate memory_config if present.
    mem_cfg = raw.get("memory_config")
    if mem_cfg and isinstance(mem_cfg, dict):
        cfg = dict(mem_cfg)
        cfg["character_id"] = record["id"]
        cfg["updated_at"] = now
        state.memory_configs[record["id"]] = cfg

    # DevKit may have written character_state_config inside character_config.
    state_cfg = raw.get("character_config", {}).get("state_config", {})
    if state_cfg and isinstance(state_cfg, dict):
        sc = dict(state_cfg)
        sc["character_id"] = record["id"]
        sc.setdefault("hunger_decay_per_hour", 2.0)
        sc.setdefault("thirst_decay_per_hour", 3.0)
        sc.setdefault("health_decay_per_hour", 0.1)
        sc.setdefault("mood_decay_per_hour", 1.0)
        sc.setdefault("low_hunger_threshold", 30.0)
        sc.setdefault("low_mood_threshold", 20.0)
        state.character_state_configs[record["id"]] = sc

    # Load initial memory entries if the devkit saved any.
    mem_path = os.path.join(dk, _MEMORIES_SUBDIR, devkit_id, "entries.json")
    mem_entries = _read_json(mem_path) if os.path.isfile(mem_path) else None
    if mem_entries and isinstance(mem_entries, list):
        for entry in mem_entries:
            entry["character_id"] = record["id"]
            eid = entry.get("id", f"mem_{devkit_id}_{len(state.memory)}")
            entry.setdefault("id", eid)
            entry.setdefault("type", "long")
            entry.setdefault("source", "devkit_initial")
            entry.setdefault("importance", 0.6)
            state.memory[eid] = entry

    _LOGGER.info("Loaded DevKit character %s (%s) into runtime", devkit_id, record["name"])
    return record


def load_world(devkit_id: str) -> dict | None:
    """Load a world from the DevKit save directory into the core runtime.

    Similar to :func:`load_character` but for worlds.  Creates a record
    in ``state.worlds`` and initialises ``world_environment`` /
    ``world_compute_config`` if the devkit data contains those fields.
    """
    with _LOCK:
        return _load_world_impl(devkit_id)


def _load_world_impl(devkit_id: str) -> dict | None:
    dk = get_devkit_dir()
    wjson_path = _devkit_json_path(dk, "world", devkit_id)
    if not wjson_path or not os.path.isfile(wjson_path):
        _LOGGER.warning("DevKit world %s not found at %s", devkit_id, wjson_path)
        return None

    raw = _read_json(wjson_path)
    if raw is None:
        return None

    # Read optional world_doc markdown.
    doc_path = os.path.join(dk, _WORLDS_SUBDIR, devkit_id, "world_doc.md")
    world_doc = _read_text(doc_path) or raw.get("world_doc", "")

    # Read optional world_config.json (C1.3).
    wc_path = os.path.join(dk, _WORLDS_SUBDIR, devkit_id, "world_config.json")
    world_config = _read_json(wc_path) if os.path.isfile(wc_path) else raw.get("config", {})

    now = now_ts()

    # Remove any previously loaded devkit world.
    _remove_devkit_records("world", devkit_id)

    wid = raw.get("id", devkit_id)
    record = {
        "id": wid,
        "name": raw.get("name", "Unnamed World"),
        "world_doc": world_doc,
        "world_doc_path": doc_path if os.path.isfile(doc_path) else None,
        "config": world_config,
        "config_path": wc_path if os.path.isfile(wc_path) else None,
        "state_doc_path": None,
        "is_active": False,
        "last_active_at": None,
        "created_at": raw.get("created_at", now),
        "updated_at": now,
        _SOURCE_TAG: True,
        _ORIGINAL_ID_TAG: devkit_id,
    }
    state.worlds[wid] = record

    # Init world_environment with defaults, overlaid from config.
    env = {
        "weather": world_config.get("weather_probabilities", {}).get("morning", {}).get("sunny", "sunny"),
        "time_of_day": 360,  # 06:00 default
        "light_level": 0.8,
        "ambient_audio": None,
        "env_meta": json.dumps(world_config) if world_config else "{}",
    }
    if "sunny" in str(env.get("weather", "")):
        env["weather"] = "sunny"
    state.world_environment[wid] = env

    # Init compute config.
    state.world_compute_config[wid] = {
        "world_id": wid,
        "total_token_budget": 50_000,
        "active_tier": "low_active",
        "max_npcs": 50,
        "max_active_npcs": 3,
        "updated_at": now,
    }

    _LOGGER.info("Loaded DevKit world %s (%s) into runtime", devkit_id, record["name"])
    return record


# ---------------------------------------------------------------------------
# Unload — remove a devkit-loaded item from core runtime
# ---------------------------------------------------------------------------


def unload(kind: str, item_id: str) -> bool:
    """Remove a devkit-loaded item from core runtime state.

    ``kind`` is ``"character"`` or ``"world"``.  ``item_id`` is the
    **core** record id (same as ``devkit_original_id`` in most cases).

    Returns ``True`` if something was removed, ``False`` otherwise.
    """
    with _LOCK:
        return _unload_impl(kind, item_id)


def _unload_impl(kind: str, item_id: str) -> bool:
    found = False
    bucket = state.characters if kind == "character" else state.worlds if kind == "world" else None
    if bucket is None:
        return False
    for rid, r in list(bucket.items()):
        if r.get(_SOURCE_TAG) and r.get(_ORIGINAL_ID_TAG) == item_id:
            del bucket[rid]
            found = True
            # Also clear sidecar buckets.
            state.memory_configs.pop(rid, None)
            state.character_state_configs.pop(rid, None)
            if kind == "world":
                state.world_environment.pop(rid, None)
                state.world_compute_config.pop(rid, None)
            _LOGGER.info("Unloaded DevKit %s %s", kind, item_id)
    if not found:
        _LOGGER.warning("No devkit-loaded %s found with id %s", kind, item_id)
    return found


def _remove_devkit_records(kind: str, original_id: str) -> None:
    """Remove any existing devkit-loaded records sharing the same original id."""
    _unload_impl(kind, original_id)


# ---------------------------------------------------------------------------
# Reload — rescan and refresh one or all items
# ---------------------------------------------------------------------------


def reload_characters() -> list[dict]:
    """Rescan the devkit character directory and reload every character
    that was previously loaded (or all found characters).

    Existing devkit-loaded records are **replaced**.
    """
    with _LOCK:
        loaded: list[dict] = []
        for char in scan_characters():
            cid = char.get("id")
            if not cid:
                continue
            result = _load_character_impl(cid)
            if result:
                loaded.append(result)
        _LOGGER.info("DevKit reload: %d characters loaded", len(loaded))
        return loaded


def reload_worlds() -> list[dict]:
    """Rescan the devkit world directory and reload every world."""
    with _LOCK:
        loaded: list[dict] = []
        for world in scan_worlds():
            wid = world.get("id")
            if not wid:
                continue
            result = _load_world_impl(wid)
            if result:
                loaded.append(result)
        _LOGGER.info("DevKit reload: %d worlds loaded", len(loaded))
        return loaded


# ---------------------------------------------------------------------------
# Preview — return full raw devkit data for UI rendering
# ---------------------------------------------------------------------------


def get_character_preview(devkit_id: str) -> dict | None:
    """Return the full devkit character data for preview rendering.

    Includes the character.json fields plus any available persona.md
    and memory entries count.
    """
    dk = get_devkit_dir()
    char_path = _devkit_json_path(dk, "character", devkit_id)
    if not char_path:
        return None
    raw = _read_json(char_path)
    if raw is None:
        return None

    # Attach extra metadata.
    raw.setdefault("_preview", {})
    raw["_preview"]["persona_exists"] = os.path.isfile(
        os.path.join(dk, _CHARACTERS_SUBDIR, devkit_id, "persona.md")
    )
    raw["_preview"]["memories_count"] = _count_memories(dk, devkit_id)
    raw["_preview"]["is_loaded"] = devkit_id in [
        r.get(_ORIGINAL_ID_TAG) for r in state.characters.values()
        if r.get(_SOURCE_TAG)
    ]
    return raw


def get_world_preview(devkit_id: str) -> dict | None:
    """Return the full devkit world data for preview rendering."""
    dk = get_devkit_dir()
    wpath = _devkit_json_path(dk, "world", devkit_id)
    if not wpath:
        return None
    raw = _read_json(wpath)
    if raw is None:
        return None
    raw.setdefault("_preview", {})
    raw["_preview"]["doc_exists"] = os.path.isfile(
        os.path.join(dk, _WORLDS_SUBDIR, devkit_id, "world_doc.md")
    )
    raw["_preview"]["config_exists"] = os.path.isfile(
        os.path.join(dk, _WORLDS_SUBDIR, devkit_id, "world_config.json")
    )
    raw["_preview"]["is_loaded"] = devkit_id in [
        r.get(_ORIGINAL_ID_TAG) for r in state.worlds.values()
        if r.get(_SOURCE_TAG)
    ]
    return raw


def _count_memories(dk: str, devkit_id: str) -> int:
    mem_path = os.path.join(dk, _MEMORIES_SUBDIR, devkit_id, "entries.json")
    entries = _read_json(mem_path) if os.path.isfile(mem_path) else None
    return len(entries) if isinstance(entries, list) else 0


# ---------------------------------------------------------------------------
# Loaded list
# ---------------------------------------------------------------------------


def list_loaded() -> dict[str, list[dict]]:
    """Return all devkit-loaded items grouped by kind."""
    chars = [
        r for r in state.characters.values()
        if r.get(_SOURCE_TAG)
    ]
    worlds = [
        r for r in state.worlds.values()
        if r.get(_SOURCE_TAG)
    ]
    return {"characters": chars, "worlds": worlds}


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _set_devkit_dir_for_test(path: str) -> None:
    """Override the devkit directory for testing.  Not for production use."""
    os.environ[ENV_OVERRIDE] = path


def _clear_env_override() -> None:
    """Remove the env override, reverting to the default path."""
    os.environ.pop(ENV_OVERRIDE, None)


__all__ = [
    "get_devkit_dir",
    "is_available",
    "scan_characters",
    "scan_worlds",
    "load_character",
    "load_world",
    "unload",
    "reload_characters",
    "reload_worlds",
    "get_character_preview",
    "get_world_preview",
    "list_loaded",
    "DEFAULT_DEVKIT_DIR",
    "ENV_OVERRIDE",
    # Test helpers (exported but not public API)
    "_set_devkit_dir_for_test",
    "_clear_env_override",
]
