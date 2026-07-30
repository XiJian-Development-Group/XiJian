"""Desktop file operation tools — real read/write/list through the A5.2 gate.
桌面文件操作工具 — 经 A5.2 门禁的真实读/写/列目录。

These tools perform **actual** filesystem operations on the user's
machine.  Every call passes through the A5.2 MCP protection gate
(:func:`xijian_api.stubs.mcp.check`) before touching the disk.
这些工具在用户机器上执行**实际**文件系统操作。每次调用在接触磁盘前
均经 A5.2 MCP 保护门禁 (:func:`xijian_api.stubs.mcp.check`)。

Path scoping / 路径范围
============

Per the user's configuration choice, file operations are scoped to
the **user's home directory** (``~``).  System directories are
blocked outright regardless of A5.2 rules:
根据用户配置选择，文件操作限定到**用户主目录** (``~``)。系统目录无论 A5.2 规则如何均被完全阻止：

* ``/etc``, ``/var``, ``/usr``, ``/bin``, ``/sbin``, ``/dev``,
  ``/proc``, ``/sys``, ``/System``, ``/Library``, ``/private/etc``,
  ``/private/var``

Path traversal (``..``) is resolved and checked — a path that
escapes the home directory after resolution is rejected.  Symlinks
are followed but the resolved target must still be within scope.
路径穿越 (``..``) 会被解析和检查 — 解析后逃逸主目录的路径被拒绝。
符号链接被跟随但解析后的目标必须仍在范围内。

Size limits / 大小限制
===========

* ``file_read``: 1 MB max (``MAX_READ_BYTES``)
* ``file_write``: 1 MB max (``MAX_WRITE_BYTES``)
* ``file_list``: 500 entries max / 最多 500 条

Action kinds / 操作类型
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
# Constants / 常量
# ---------------------------------------------------------------------------

#: Root directory that file operations are scoped to.
#: 文件操作限定到的根目录。
HOME_DIR = Path.home()

#: System directories that are always blocked, even if they're inside
#: the home directory (e.g. via symlink).
#: 始终被阻止的系统目录，即使它们在主目录内（例如通过符号链接）。
_BLOCKED_PREFIXES: tuple[str, ...] = (
    "/etc", "/var", "/usr", "/bin", "/sbin", "/dev",
    "/proc", "/sys", "/System", "/Library",
    "/private/etc", "/private/var",
)

#: Maximum bytes for a single read.
#: 单次读取的最大字节数。
MAX_READ_BYTES = 1_048_576  # 1 MB

#: Maximum bytes for a single write.
#: 单次写入的最大字节数。
MAX_WRITE_BYTES = 1_048_576  # 1 MB

#: Maximum entries in a directory listing.
#: 目录列出的最大条目数。
MAX_LIST_ENTRIES = 500


# ---------------------------------------------------------------------------
# Path validation / 路径验证
# ---------------------------------------------------------------------------


def _validate_path(raw_path: str) -> Path:
    """Resolve and validate ``raw_path`` against the scoping rules.
    根据范围规则解析和验证 ``raw_path``。

    * Expands ``~`` to the user's home directory.
      将 ``~`` 展开为用户主目录。
    * Resolves ``..`` and symlinks to a canonical absolute path.
      将 ``..`` 和符号链接解析为规范绝对路径。
    * Rejects paths outside the home directory.
      拒绝主目录外的路径。
    * Rejects paths that land in a blocked system directory.
      拒绝落入被阻止系统目录的路径。

    Returns the resolved :class:`Path`.  Raises :class:`ToolError`
    on violation.
    返回解析后的 :class:`Path`。违规时抛出 :class:`ToolError`。
    """
    if not isinstance(raw_path, str) or not raw_path:
        raise ToolError("path is required")

    expanded = os.path.expanduser(raw_path)
    if not os.path.isabs(expanded):
        expanded = str(HOME_DIR / expanded)

    try:
        resolved = Path(expanded).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ToolError("cannot resolve path %r: %s" % (raw_path, exc)) from exc

    resolved_str = str(resolved)

    for prefix in _BLOCKED_PREFIXES:
        if resolved_str == prefix or resolved_str.startswith(prefix + "/"):
            raise ToolError(
                "access denied: path %r is in a blocked system directory" % raw_path,
                data={"resolved": resolved_str, "blocked_prefix": prefix},
            )

    home_str = str(HOME_DIR)
    if resolved_str != home_str and not resolved_str.startswith(home_str + os.sep):
        raise ToolError(
            "access denied: path %r is outside the user home directory" % raw_path,
            data={"resolved": resolved_str, "home": home_str},
        )

    return resolved


# ---------------------------------------------------------------------------
# Handlers / 处理器
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
# Registration / 注册
# ---------------------------------------------------------------------------

register_tool(
    "file_read",
    "Read the contents of a file. The path must be within the user's home directory. "
    "Binary files are returned as base64. Maximum 1 MB. / "
    "读取文件内容。路径必须在用户主目录内。二进制文件以 base64 返回。最大 1 MB。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to ~ or absolute within home) / 文件路径"},
            "encoding": {"type": "string", "description": "Text encoding (default: utf-8) / 文本编码", "default": "utf-8"},
            "max_bytes": {"type": "integer", "description": "Maximum bytes to read (default: 1048576) / 最大读取字节数", "default": 1048576},
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
    "Maximum 1 MB. / "
    "将内容写入文件。路径必须在用户主目录内。必要时创建父目录。设置 append=true 追加而非覆盖。最大 1 MB。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to ~ or absolute within home) / 文件路径"},
            "content": {"type": "string", "description": "Content to write / 要写入的内容"},
            "encoding": {"type": "string", "description": "Text encoding (default: utf-8) / 文本编码", "default": "utf-8"},
            "append": {"type": "boolean", "description": "Append to file instead of overwriting (default: false) / 追加而非覆盖", "default": False},
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
    "Returns file names, types, sizes, and modification times. Maximum 500 entries. / "
    "列出目录内容。路径必须在用户主目录内。返回文件名、类型、大小和修改时间。最多 500 条。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (relative to ~ or absolute within home) / 目录路径"},
            "pattern": {"type": "string", "description": "Glob pattern to filter (default: *) / 用于筛选的 Glob 模式", "default": "*"},
            "include_hidden": {"type": "boolean", "description": "Include hidden files (default: false) / 包含隐藏文件", "default": False},
            "max_entries": {"type": "integer", "description": "Maximum entries to return (default: 500) / 最大返回条目数", "default": 500},
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
    "Directories require recursive=true. This operation is irreversible. / "
    "删除文件或目录。路径必须在用户主目录内。目录需要 recursive=true。此操作不可逆。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to delete (relative to ~ or absolute within home) / 要删除的路径"},
            "recursive": {"type": "boolean", "description": "Allow deleting directories recursively (default: false) / 允许递归删除目录", "default": False},
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
    "The path must be within the user's home directory. / "
    "获取文件/目录元数据（大小、修改时间、权限）。路径必须在用户主目录内。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to stat (relative to ~ or absolute within home) / 要检查状态的路径"},
        },
        "required": ["path"],
    },
    _file_stat_handler,
    action_kind=rules_stub.KIND_FILE_READ,
    annotations={"readOnlyHint": True, "openWorldHint": True},
)


__all__: list[str] = []