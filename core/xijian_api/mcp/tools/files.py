"""Desktop file operation tools — real read/write/list through the A5.2 gate.

These tools perform **actual** filesystem operations on the user's
machine.  Every call passes through the A5.2 MCP protection gate
(:func:`xijian_api.stubs.mcp.check`) before touching the disk.

Path scoping
============

Per the user's configuration choice, file operations are scoped to
the **user's home directory** (``~``).  System directories are
blocked outright regardless of A5.2 rules:

* ``/etc``, ``/var``, ``/usr``, ``/bin``, ``/sbin``, ``/dev``,
  ``/proc``, ``/sys``, ``/System``, ``/Library``, ``/private/etc``,
  ``/private/var``

Path traversal (``..``) is resolved and checked — a path that
escapes the home directory after resolution is rejected.  Symlinks
are followed but the resolved target must still be within scope.

Size limits
===========

* ``file_read``: 1 MB max (``MAX_READ_BYTES``)
* ``file_write``: 1 MB max (``MAX_WRITE_BYTES``)
* ``file_list``: 500 entries max

Action kinds
============

* ``file_read``   → :data:`rules_stub.KIND_FILE_READ`
* ``file_write``  → :data:`rules_stub.KIND_FILE_WRITE`
* ``file_delete`` → :data:`rules_stub.KIND_FILE_DELETE`
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import mcp_rules as rules_stub


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Root directory that file operations are scoped to.
HOME_DIR = Path.home()

#: System directories that are always blocked, even if they're inside
#: the home directory (e.g. via symlink).
_BLOCKED_PREFIXES: tuple[str, ...] = (
    "/etc", "/var", "/usr", "/bin", "/sbin", "/dev",
    "/proc", "/sys", "/System", "/Library",
    "/private/etc", "/private/var",
)

#: Maximum bytes for a single read.
MAX_READ_BYTES = 1_048_576  # 1 MB

#: Maximum bytes for a single write.
MAX_WRITE_BYTES = 1_048_576  # 1 MB

#: Maximum entries in a directory listing.
MAX_LIST_ENTRIES = 500


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _validate_path(raw_path: str) -> Path:
    """Resolve and validate ``raw_path`` against the scoping rules.

    * Expands ``~`` to the user's home directory.
    * Resolves ``..`` and symlinks to a canonical absolute path.
    * Rejects paths outside the home directory.
    * Rejects paths that land in a blocked system directory.

    Returns the resolved :class:`Path`.  Raises :class:`ToolError`
    on violation.
    """
    if not isinstance(raw_path, str) or not raw_path:
        raise ToolError("path is required")

    # Expand ~ and make absolute.
    expanded = os.path.expanduser(raw_path)
    if not os.path.isabs(expanded):
        expanded = str(HOME_DIR / expanded)

    # Resolve to canonical path (follows symlinks, resolves ..).
    try:
        resolved = Path(expanded).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ToolError("cannot resolve path %r: %s" % (raw_path, exc)) from exc

    resolved_str = str(resolved)

    # Block system directories.
    for prefix in _BLOCKED_PREFIXES:
        if resolved_str == prefix or resolved_str.startswith(prefix + "/"):
            raise ToolError(
                "access denied: path %r is in a blocked system directory" % raw_path,
                data={"resolved": resolved_str, "blocked_prefix": prefix},
            )

    # Must be within home directory.
    home_str = str(HOME_DIR)
    if resolved_str != home_str and not resolved_str.startswith(home_str + os.sep):
        raise ToolError(
            "access denied: path %r is outside the user home directory" % raw_path,
            data={"resolved": resolved_str, "home": home_str},
        )

    return resolved


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _file_read_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    path = _validate_path(args.get("path", ""))
    encoding = args.get("encoding", "utf-8")
    max_bytes = int(args.get("max_bytes", MAX_READ_BYTES))
    if max_bytes > MAX_READ_BYTES:
        max_bytes = MAX_READ_BYTES

    if not path.exists():
        raise ToolError("file not found: %s" % path)
    if not path.is_file():
        raise ToolError("not a regular file: %s" % path)

    file_size = path.stat().st_size
    if file_size > max_bytes:
        raise ToolError(
            "file too large: %d bytes (max %d)" % (file_size, max_bytes),
            data={"size": file_size, "max": max_bytes},
        )

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError("read failed: %s" % exc) from exc

    # Try to decode as text; fall back to base64 for binary.
    try:
        text = data.decode(encoding)
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
            "_meta": {
                "path": str(path),
                "size": file_size,
                "encoding": encoding,
            },
        }
    except (UnicodeDecodeError, LookupError):
        import base64
        b64 = base64.b64encode(data).decode("ascii")
        return {
            "content": [{"type": "text", "text": b64}],
            "isError": False,
            "_meta": {
                "path": str(path),
                "size": file_size,
                "encoding": "base64",
            },
        }


def _file_write_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    path = _validate_path(args.get("path", ""))
    content = args.get("content", "")
    encoding = args.get("encoding", "utf-8")
    append = bool(args.get("append", False))

    if not isinstance(content, str):
        raise ToolError("content must be a string")

    data = content.encode(encoding)
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError(
            "content too large: %d bytes (max %d)" % (len(data), MAX_WRITE_BYTES),
            data={"size": len(data), "max": MAX_WRITE_BYTES},
        )

    # Ensure parent directory exists.
    path.parent.mkdir(parents=True, exist_ok=True)

    mode = "a" if append else "w"
    try:
        with open(path, mode, encoding=encoding) as f:
            f.write(content)
    except OSError as exc:
        raise ToolError("write failed: %s" % exc) from exc

    return {
        "content": [{"type": "text", "text": "wrote %d bytes to %s" % (len(data), path)}],
        "isError": False,
        "_meta": {"path": str(path), "bytes": len(data), "append": append},
    }


def _file_list_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    path = _validate_path(args.get("path", ""))
    pattern = args.get("pattern", "*")
    include_hidden = bool(args.get("include_hidden", False))
    max_entries = int(args.get("max_entries", MAX_LIST_ENTRIES))
    if max_entries > MAX_LIST_ENTRIES:
        max_entries = MAX_LIST_ENTRIES

    if not path.exists():
        raise ToolError("directory not found: %s" % path)
    if not path.is_dir():
        raise ToolError("not a directory: %s" % path)

    entries: list[dict[str, Any]] = []
    try:
        for item in sorted(path.iterdir(), key=lambda p: p.name):
            name = item.name
            if not include_hidden and name.startswith("."):
                continue
            # Simple glob match.
            import fnmatch
            if not fnmatch.fnmatch(name, pattern):
                continue
            try:
                stat = item.stat()
                entries.append({
                    "name": name,
                    "path": str(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size": stat.st_size if item.is_file() else None,
                    "modified": stat.st_mtime,
                })
            except OSError:
                entries.append({"name": name, "path": str(item), "type": "unknown"})
            if len(entries) >= max_entries:
                break
    except OSError as exc:
        raise ToolError("list failed: %s" % exc) from exc

    import json
    return {
        "content": [{"type": "text", "text": json.dumps(entries, ensure_ascii=False, indent=2)}],
        "isError": False,
        "_meta": {"path": str(path), "count": len(entries), "truncated": len(entries) >= max_entries},
    }


def _file_delete_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    path = _validate_path(args.get("path", ""))
    recursive = bool(args.get("recursive", False))

    if not path.exists():
        raise ToolError("path not found: %s" % path)

    try:
        if path.is_dir():
            if not recursive:
                raise ToolError("cannot delete directory without recursive=true")
            import shutil
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        raise ToolError("delete failed: %s" % exc) from exc

    return {
        "content": [{"type": "text", "text": "deleted %s" % path}],
        "isError": False,
        "_meta": {"path": str(path), "recursive": recursive},
    }


def _file_stat_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    path = _validate_path(args.get("path", ""))

    if not path.exists():
        raise ToolError("path not found: %s" % path)

    try:
        stat = path.stat()
    except OSError as exc:
        raise ToolError("stat failed: %s" % exc) from exc

    import json
    info = {
        "path": str(path),
        "name": path.name,
        "type": "directory" if path.is_dir() else "file",
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "created": stat.st_ctime,
        "permissions": oct(stat.st_mode & 0o777),
    }
    return {
        "content": [{"type": "text", "text": json.dumps(info, ensure_ascii=False, indent=2)}],
        "isError": False,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register_tool(
    "file_read",
    "Read the contents of a file. The path must be within the user's home directory. "
    "Binary files are returned as base64. Maximum 1 MB.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to ~ or absolute within home)"},
            "encoding": {"type": "string", "description": "Text encoding (default: utf-8)", "default": "utf-8"},
            "max_bytes": {"type": "integer", "description": "Maximum bytes to read (default: 1048576)", "default": 1048576},
        },
        "required": ["path"],
    },
    _file_read_handler,
    action_kind=rules_stub.KIND_FILE_READ,
    annotations={"readOnlyHint": True, "openWorldHint": True},
)

register_tool(
    "file_write",
    "Write content to a file. The path must be within the user's home directory. "
    "Creates parent directories if needed. Set append=true to append instead of overwrite. "
    "Maximum 1 MB.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to ~ or absolute within home)"},
            "content": {"type": "string", "description": "Content to write"},
            "encoding": {"type": "string", "description": "Text encoding (default: utf-8)", "default": "utf-8"},
            "append": {"type": "boolean", "description": "Append to file instead of overwriting (default: false)", "default": False},
        },
        "required": ["path", "content"],
    },
    _file_write_handler,
    action_kind=rules_stub.KIND_FILE_WRITE,
    annotations={"destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)

register_tool(
    "file_list",
    "List the contents of a directory. The path must be within the user's home directory. "
    "Returns file names, types, sizes, and modification times. Maximum 500 entries.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (relative to ~ or absolute within home)"},
            "pattern": {"type": "string", "description": "Glob pattern to filter (default: *)", "default": "*"},
            "include_hidden": {"type": "boolean", "description": "Include hidden files (default: false)", "default": False},
            "max_entries": {"type": "integer", "description": "Maximum entries to return (default: 500)", "default": 500},
        },
        "required": ["path"],
    },
    _file_list_handler,
    action_kind=rules_stub.KIND_FILE_READ,
    annotations={"readOnlyHint": True, "openWorldHint": True},
)

register_tool(
    "file_delete",
    "Delete a file or directory. The path must be within the user's home directory. "
    "Directories require recursive=true. This operation is irreversible.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to delete (relative to ~ or absolute within home)"},
            "recursive": {"type": "boolean", "description": "Allow deleting directories recursively (default: false)", "default": False},
        },
        "required": ["path"],
    },
    _file_delete_handler,
    action_kind=rules_stub.KIND_FILE_DELETE,
    annotations={"destructiveHint": True, "openWorldHint": True},
)

register_tool(
    "file_stat",
    "Get file/directory metadata (size, modification time, permissions). "
    "The path must be within the user's home directory.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to stat (relative to ~ or absolute within home)"},
        },
        "required": ["path"],
    },
    _file_stat_handler,
    action_kind=rules_stub.KIND_FILE_READ,
    annotations={"readOnlyHint": True, "openWorldHint": True},
)


__all__: list[str] = []
