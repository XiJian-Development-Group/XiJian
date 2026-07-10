# -*- mode: python ; coding: utf-8 -*-
# xijian-devkit.spec — PyInstaller build recipe for the standalone DevKit.
#
# Build (from the devkit/ directory)::
#
#     pyinstaller --clean --noconfirm xijian-devkit.spec
#
# or via the helper::
#
#     ./build-devkit.sh
#
# Output:
#   * dist/xijian-devkit/       — onedir binary (macOS, used for .app bundle)
#   * dist/XiJianDevKit.app     — macOS .app bundle (double-clickable)
#   * dist/XiJianDevKit.exe     — Windows single-file executable
#
# Design notes
# ------------
# * The DevKit is its own top-level ``devkit`` package (moved out of
#   ``xijian_api`` in v2.3, C5 packaging split).  ``pathex`` points at the
#   repo root so ``import devkit`` resolves during freezing.
# * ``xijian_api`` and ``flask`` are explicitly EXCLUDED — the DevKit
#   vendors the three helpers it needs (see ``devkit/_vendor.py``), so the
#   binary must never drag in the API package or Flask.
# * The ``ui/`` assets are bundled under the relative path ``devkit/ui`` so
#   ``devkit.ui_dir()`` finds them via ``sys._MEIPASS / "devkit" / "ui"``
#   when frozen.
# * ``webview`` (pywebview) + ``py7zr`` are collected wholesale because
#   both load platform backends / codecs dynamically that a static import
#   scan would miss (WKWebView/WebView2/webkitgtk; LZMA/AES codecs).
# * macOS gets an onedir collection (for .app bundle) + .app bundle
# * Windows gets a single-file executable (onefile)

import os
import sys

from PyInstaller.utils.hooks import collect_all

DEVKIT_DIR = SPECPATH                       # .../XiJian/devkit
REPO_ROOT = os.path.dirname(DEVKIT_DIR)     # .../XiJian

# --- collect dynamic deps --------------------------------------------------
# ``ui/`` assets → devkit/ui ; the project ``Config/Config.json`` is bundled
# under ``Config/`` so :func:`devkit.version.config_json_path` can read the
# app version + update source at runtime from ``sys._MEIPASS/Config``.
datas = [
    (os.path.join(DEVKIT_DIR, "ui"), "devkit/ui"),
    (os.path.join(REPO_ROOT, "Config", "Config.json"), "Config"),
]
binaries = []
hiddenimports = []

for _pkg in ("webview", "py7zr"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

block_cipher = None

a = Analysis(
    [os.path.join(DEVKIT_DIR, "app.py")],
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Keep the binary lean and enforce the decoupling contract: the
    # DevKit must not bundle the API package or Flask.
    excludes=[
        "xijian_api",
        "flask",
        "flask_sock",
        "waitress",
        "tkinter",
        "pytest",
        "_pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --- onedir executable (used for macOS .app bundle on all platforms) ------
exe_onedir = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="xijian-devkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app — no terminal window
    disable_windowed_traceback=False,
    argv_emulation=True,    # macOS: forward file-open / dock args
    target_arch=None,       # build for the host arch (arm64 / x86_64)
    codesign_identity=None,
    entitlements_file=None,
)

# --- onedir collection (for macOS .app bundle) -----------------------------
coll = COLLECT(
    exe_onedir,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="xijian-devkit",
)

# --- Windows single-file executable ----------------------------------------
if sys.platform == "win32":
    exe_win = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name="XiJianDevKit",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,          # GUI app — no terminal window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        onefile=True,           # single-file executable
    )
else:
    exe_win = None

# --- macOS .app bundle -----------------------------------------------------
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="XiJianDevKit.app",
        icon=None,
        bundle_identifier="com.xijian.devkit",
        info_plist={
            "CFBundleName": "XiJianDevKit",
            "CFBundleDisplayName": "隙间 · 开发者工具",
            "CFBundleShortVersionString": "1.5.0",
            "CFBundleVersion": "1.5.0",
            "NSHighResolutionCapable": True,
            # No network server is ever opened; the app talks SMTP outbound
            # only.  Declared here for App Transport Security clarity.
            "LSMinimumSystemVersion": "11.0",
        },
    )