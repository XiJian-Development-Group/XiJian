"""Runtime environment detection — supporting both dev and PyInstaller packaged modes.

运行时环境检测 — 同时支持开发模式与 PyInstaller 打包模式。

This module provides a unified entry point for determining whether the
current process runs in a PyInstaller-packaged environment, and for
resolving related paths (executable directory, bundled resource directory,
external dependency directory, log directory, etc.).

本模块提供统一的入口来判断当前进程是否运行在 PyInstaller 打包
环境中，并解析相关路径（可执行文件目录、内置资源目录、外部
依赖目录、日志目录等）。

PyInstaller onedir layout::

PyInstaller 的 onedir 模式下，目录结构如下::

    <dist>/xijian-api              # Executable / 可执行文件
    <dist>/_internal/              # PyInstaller runtime (Python + deps) / 运行时（Python + 依赖）
    <dist>/config.toml             # User-editable config / 用户可编辑的配置
    <dist>/logs/                   # Log directory / 日志目录
    <dist>/external_libs/          # Optional external AI deps (mlx/llama_cpp etc.) / 可选的外部 AI 依赖

In dev (unfrozen) mode all paths fall back to the project source root.

在开发模式（未冻结）下，所有路径回退到项目源码根目录。

UI workflow::

UI 程序的工作流程::

    1. Extract xijian-core-<platform>.zip to <app_data>/xijian-core/
       解压 xijian-core-<platform>.zip 到 <app_data>/xijian-core/
    2. Optionally extract AI extension pack into external_libs/
       可选地解压 AI 扩展包到 external_libs/
    3. Launch subprocess: <app_data>/xijian-core/xijian-api --port 18500
       启动子进程
    4. Wait for the server-started log line (e.g. "werkzeug 服务启动") on
       stdout/stderr or poll /v1/health
       等待 stdout/stderr 出现服务启动日志（如 "werkzeug 服务启动"）或轮询 /v1/health
    5. Use the API; send SIGTERM / Ctrl+C on exit
       使用 API；退出时发送 SIGTERM / Ctrl+C
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Module-level constants, resolved once at import time to avoid recomputation.
# 模块级常量，import 时一次性解析，避免重复计算
_FROZEN: bool = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def is_frozen() -> bool:
    """Return whether running in a PyInstaller-packaged environment.

    返回当前是否运行在 PyInstaller 打包环境中。
    """
    return _FROZEN


def executable_dir() -> Path:
    """Return the executable directory.

    返回可执行文件所在目录。

    * Packaged mode: directory containing the executable (xijian-api / xijian-api.exe)
    * Dev mode: parent directory of the ``xijian_api`` package (i.e. ``core/``)

    * 打包模式：可执行文件所在目录
    * 开发模式：``xijian_api`` 包的上级目录（即 ``core/``）

    This directory holds user-editable ``config.toml``, ``logs/``,
    ``external_libs/``, etc.

    该目录用于存放用户可编辑的 ``config.toml``、``logs/`` 目录、
    ``external_libs/`` 目录等。
    """
    if _FROZEN:
        # sys.executable points to the executable itself in packaged mode.
        # sys.executable 在打包模式下指向可执行文件本身
        return Path(sys.executable).resolve().parent
    # Dev mode: xijian_api/runtime.py → xijian_api/ → core/
    # 开发模式：xijian_api/runtime.py → xijian_api/ → core/
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Return the PyInstaller internal resource directory (``_MEIPASS``).

    返回 PyInstaller 内置资源目录（``_MEIPASS``）。

    * Packaged mode: ``_internal/`` directory (where PyInstaller unpacks resources)
    * Dev mode: same as :func:`executable_dir`

    * 打包模式：``_internal/`` 目录（PyInstaller 解压资源的位置）
    * 开发模式：与 :func:`executable_dir` 相同

    Contains all read-only resources packed into the executable (Python
    interpreter, dependency libraries, package data, etc.).
    **Never** write to this directory.

    该目录包含打包进可执行文件的所有只读资源（Python 解释器、
    依赖库、包数据等）。**绝对不要**往这个目录写文件。
    """
    if _FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return executable_dir()
    return executable_dir()


def external_libs_dir() -> Path:
    """Return the external AI dependency directory.

    返回外部 AI 依赖目录。

    The packaged core executable does not bundle large binary
    dependencies like MLX / llama_cpp.  Users can download the AI
    extension pack and extract it here; it is automatically added to
    ``sys.path`` at startup.

    打包后的核心可执行文件不包含 MLX / llama_cpp 等大型二进制
    依赖。用户可按需下载 AI 扩展包并解压到此目录，启动时会自动
    加入 ``sys.path``。

    Path: ``<executable_dir>/external_libs/``

    路径：``<executable_dir>/external_libs/``
    """
    return executable_dir() / "external_libs"


def default_config_path() -> Path:
    """Return the default config file path.

    返回默认配置文件路径。

    * Packaged mode: ``<executable_dir>/config.toml``
    * Dev mode: ``<core_dir>/config.toml``
    """
    return executable_dir() / "config.toml"


def default_log_dir() -> Path:
    """Return the default log directory.

    返回默认日志目录。

    * Packaged mode: ``<executable_dir>/logs/``
    * Dev mode: ``/tmp/xijian-logs/`` (compatible with original behaviour)
    * 开发模式：``/tmp/xijian-logs/``（保持与原行为兼容）
    """
    if _FROZEN:
        return executable_dir() / "logs"
    return Path("/tmp/xijian-logs")


def default_log_file() -> Path:
    """Return the default log file path.

    返回默认日志文件路径。
    """
    return default_log_dir() / "xijian-api.log"


def default_token_dir() -> Path:
    """Return the default token file directory.

    返回默认 token 文件目录。

    * Packaged mode: ``<executable_dir>/run/`` (avoid /tmp being cleaned)
    * Dev mode: ``/tmp/`` (compatible with original behaviour)
    * 打包模式：``<executable_dir>/run/``（避免 /tmp 被清理）
    * 开发模式：``/tmp/``（保持与原行为兼容）
    """
    if _FROZEN:
        return executable_dir() / "run"
    return Path("/tmp")


def default_token_file(pid: int | None = None) -> Path:
    """Return the default token file path.

    返回默认 token 文件路径。
    """
    if pid is None:
        pid = os.getpid()
    return default_token_dir() / f"xijian-{pid}.token"


def default_storage_dir() -> Path:
    """Return the default storage root directory.

    返回默认存储根目录。

    Dev mode and packaged mode both resolve to the unified CORE_ROOT
    (``~/Library/Application Support/XiJian/Core``), overridable via
    the ``XIJIAN_DATA_DIR`` environment variable.

    开发模式与打包模式统一解析到 CORE_ROOT
    (``~/Library/Application Support/XiJian/Core``)，
    可通过 ``XIJIAN_DATA_DIR`` 环境变量覆盖。

    Used to store model weights, user uploads, snapshots, audit logs, etc.

    该目录用于存放模型权重、用户上传文件、快照、审计日志等。
    """
    env_dir = os.environ.get("XIJIAN_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path("~/Library/Application Support/XiJian/Core").expanduser()


def setup_external_libs() -> None:
    """Add the ``external_libs/`` directory to ``sys.path``.

    将 ``external_libs/`` 目录加入 ``sys.path``。

    In packaged mode, if ``external_libs/`` directory exists, add it to
    the end of ``sys.path`` so that user-installed AI extension packages
    (mlx_lm, llama_cpp, etc.) can be imported.

    No-op in dev mode (dependencies are installed via conda/pip).

    在打包模式下，如果 ``external_libs/`` 目录存在，将其加入
    ``sys.path`` 末尾，使得用户按需安装的 AI 扩展包（mlx_lm、
    llama_cpp 等）能被 import 到。

    开发模式下此函数为空操作（依赖通过 conda/pip 安装）。
    """
    if not _FROZEN:
        return
    libs_dir = external_libs_dir()
    if libs_dir.is_dir():
        path_str = str(libs_dir)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def ensure_runtime_dirs() -> None:
    """Ensure runtime directories exist (logs/, run/, storage root).

    确保运行时需要的目录存在（logs/、run/、存储根目录）。

    Always creates the unified storage root (CORE_ROOT) plus its ``logs/``
    and ``run/`` subdirectories so dev and packaged modes share the same
    layout.  In packaged mode additionally creates the executable-side
    ``logs/`` and ``run/`` directories.

    始终创建统一存储根目录 (CORE_ROOT) 及其 ``logs/``、``run/`` 子目录，
    使开发模式与打包模式共享同一布局。打包模式下额外创建可执行文件侧的
    ``logs/`` 与 ``run/`` 目录。
    """
    storage = default_storage_dir()
    targets = [
        default_log_dir(),
        default_token_dir(),
        storage,
        storage / "logs",
        storage / "run",
    ]
    for d in targets:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Failure to create directories does not block startup;
            # errors will surface again on the actual write attempt.
            # 目录创建失败不阻塞启动，后续写入时会再次报错
            pass


def print_environment_info() -> str:
    """Return a runtime environment info string (for startup banner).

    返回运行时环境信息字符串（用于启动 banner）。
    """
    lines = [
        f"运行模式      : {'打包模式 (frozen)' if _FROZEN else '开发模式'}",
        f"可执行目录    : {executable_dir()}",
    ]
    if _FROZEN:
        lines.append(f"内置资源目录  : {bundle_dir()}")
        libs = external_libs_dir()
        lines.append(
            f"外部依赖目录  : {libs} ({'存在' if libs.is_dir() else '未安装'})"
        )
    return "\n".join(lines)


__all__ = [
    "is_frozen",
    "executable_dir",
    "bundle_dir",
    "external_libs_dir",
    "default_config_path",
    "default_log_dir",
    "default_log_file",
    "default_token_dir",
    "default_token_file",
    "default_storage_dir",
    "setup_external_libs",
    "ensure_runtime_dirs",
    "print_environment_info",
]
