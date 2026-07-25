# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec 文件 — 将 XiJian Core API 打包为独立可执行文件。

构建产物（onedir 模式）::

    dist/xijian-api/
    ├── xijian-api              # 可执行文件（macOS/Linux）
    │                           # 或 xijian-api.exe（Windows）
    ├── _internal/              # PyInstaller 运行时（Python + 依赖）
    ├── config.toml             # 默认配置（用户可编辑，由构建脚本拷贝）
    └── README.txt              # 使用说明（由构建脚本拷贝）

使用方法::

    # 通过 dev 脚本调用（推荐）
    ./core/scripts/dev.sh --build
    .\core\scripts\dev.ps1 -Build

    # 直接调用 PyInstaller
    cd core
    pyinstaller scripts/xijian-api.spec --noconfirm --clean

构建脚本会在 PyInstaller 完成后将 ``config.toml`` 和 ``README.txt``
拷贝到 ``dist/xijian-api/`` 目录，然后打包为 zip。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
# spec 文件位于 core/scripts/，项目根目录是其上级的上级
SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 — PyInstaller 注入
CORE_DIR = SPEC_DIR.parent

# ---------------------------------------------------------------------------
# 收集 hidden imports
# ---------------------------------------------------------------------------
# Flask 应用通常需要显式声明子模块作为 hidden imports，
# 因为 PyInstaller 的静态分析无法发现通过字符串导入的模块。
hiddenimports = []

# 1. xijian_api 自身的所有子模块（routes、stubs、ai、mcp 等）
hiddenimports += collect_submodules("xijian_api")

# 2. Flask 生态
hiddenimports += collect_submodules("flask")
hiddenimports += collect_submodules("waitress")
hiddenimports += collect_submodules("jinja2")
hiddenimports += collect_submodules("werkzeug")

# 3. 配置/数据格式
hiddenimports += ["tomllib"]  # Python 3.11+ 标准库（需要确保打包）

# 4. 可选 AI 后端依赖（如果安装了则打包，未安装则跳过）
_optional_ai_imports = [
    # MLX 生态（macOS Apple Silicon）
    "mlx",
    "mlx_lm",
    "mlx_vlm",
    "mlx_embeddings",
    "mlx_audio",
    "mlx_whisper",
    # GGUF 生态
    "llama_cpp",
    "pywhispercpp",
    # 图像生成
    "diffusers",
    "torch",
    "PIL",
    # OpenAI SDK（可选传输）
    "openai",
    # HTTP 客户端
    "httpx",
    "requests",
]
for mod in _optional_ai_imports:
    try:
        __import__(mod)
        hiddenimports.append(mod)
        # 同时收集该包的子模块
        try:
            subs = collect_submodules(mod)
            hiddenimports += subs
        except Exception:
            pass
    except ImportError:
        # 未安装的依赖跳过（不影响核心打包）
        pass

# 去重
hiddenimports = sorted(set(hiddenimports))

# ---------------------------------------------------------------------------
# 数据文件
# ---------------------------------------------------------------------------
datas = []

# DevKit UI 资源（HTML/JS/CSS）
devkit_ui = CORE_DIR / "xijian_api" / "devkit" / "ui"
if devkit_ui.is_dir():
    datas.append((str(devkit_ui), "xijian_api/devkit/ui"))

# ---------------------------------------------------------------------------
# 二进制文件
# ---------------------------------------------------------------------------
binaries = []

# ---------------------------------------------------------------------------
# 排除项（减小体积）
# ---------------------------------------------------------------------------
excludes = [
    # 测试框架（打包后不需要）
    "pytest",
    "pytest_cov",
    "_pytest",
    # IDE 相关
    "IPython",
    "jupyter",
    "notebook",
    # tkinter（Flask 应用不需要）
    "tkinter",
    # 未使用的数据库驱动
    "MySQLdb",
    "psycopg2",
    "sqlite3",  # 如果业务代码用到请删除此项
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(CORE_DIR / "xijian_api" / "launch.py")],
    pathex=[str(CORE_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------------------
# PYZ（Python 字节码归档）
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# EXE
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="xijian-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # macOS: 不签名（用户可自行 codesign）
    # Windows: console 模式（API 服务器需要控制台输出）
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 图标（如果存在）
    # icon=str(CORE_DIR / "scripts" / "xijian-icon.ico"),
)

# ---------------------------------------------------------------------------
# COLLECT（onedir 模式）
# ---------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="xijian-api",
)
