#!/usr/bin/env bash
# build-core.sh — Build xijian-api into a standalone PyInstaller binary,
#                 pack it into a 7z solid archive, and bundle the py7zr
#                 extraction helper alongside.
#
# The resulting artifacts are placed under macapp/Resources/ so they are
# included in the .app bundle at build time.
#
# Usage
# -----
#     ./build-core.sh                    # normal build
#     ./build-core.sh --clean            # wipe build/dist cache first
#     VENV=/path/to/venv ./build-core.sh  # use a specific venv
#
# Output
# ------
#     macapp/XiJian/Resources/xijian-core.7z      — frozen server binary (7z)
#     macapp/XiJian/Resources/py7zr_bundle/        — vendored py7zr + deps
#
# Requirements
# ------------
#   * Python >= 3.11  (for building; the frozen binary targets 3.12)
#   * pyinstaller  (installed automatically if missing)
#   * py7zr        (installed automatically if missing)

set -euo pipefail

MACAPP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${MACAPP_DIR}/.." && pwd)"
CORE_DIR="${REPO_ROOT}/core"
CORE_VENV="${CORE_DIR}/.venv"

BUILD_DIR="${MACAPP_DIR}/build"
RESOURCES_DIR="${MACAPP_DIR}/XiJian/Resources"

CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        -h|--help)
            sed -n '3,10p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
    esac
done

# --- locate Python ----------------------------------------------------------

PY="python3"
if [[ -f "${CORE_VENV}/bin/python" ]]; then
    PY="${CORE_VENV}/bin/python"
elif [[ -n "${VENV:-}" && -f "${VENV}/bin/python" ]]; then
    PY="${VENV}/bin/python"
fi

echo "==> using Python: $(${PY} --version 2>&1)"

# --- ensure build tools are available ---------------------------------------

"${PY}" -m pip install --quiet --upgrade pip
"${PY}" -m pip install --quiet "pyinstaller>=6.0" py7zr

# --- install core + its deps into the build venv ---------------------------

echo "==> installing core dependencies"
"${PY}" -m pip install --quiet -e "${CORE_DIR}" 2>/dev/null || \
    "${PY}" -m pip install --quiet -r "${CORE_DIR}/pyproject.toml" 2>/dev/null || \
    echo "    (core already installed or deps resolved)"

# --- prepare directories ----------------------------------------------------

if [[ "${CLEAN}" == "1" ]]; then
    echo "==> cleaning build artifacts"
    rm -rf "${CORE_DIR}/build" "${CORE_DIR}/dist" "${BUILD_DIR}"
fi

mkdir -p "${BUILD_DIR}" "${RESOURCES_DIR}"

# --- PyInstaller ------------------------------------------------------------
# Build the xijian-api Flask server as a standalone onedir.

echo "==> building xijian-core with PyInstaller"

# We need to make sure the core is importable by pyinstaller from the
# repo root context.  The entry point is xijian_api/__main__.py.
cd "${CORE_DIR}"

"${PY}" -m PyInstaller \
    --clean --noconfirm \
    --name xijian-core \
    --onedir \
    --paths "${CORE_DIR}" \
    --add-data "config.toml:." \
    --exclude-module tkinter \
    --exclude-module pytest \
    --exclude-module _pytest \
    --hidden-import flask \
    --hidden-import flask_sock \
    --hidden-import waitress \
    --hidden-import psutil \
    --hidden-import simple_websocket \
    xijian_api/__main__.py 2>&1 | tail -5

echo "==> PyInstaller done, verifying binary"
if [[ ! -d "${CORE_DIR}/dist/xijian-core" ]]; then
    echo "ERROR: PyInstaller output not found at ${CORE_DIR}/dist/xijian-core" >&2
    exit 1
fi

# --- archive the frozen core as a 7z solid archive --------------------------

echo "==> packing xijian-core.7z"

# First copy into our build dir so the archive paths are clean
rm -rf "${BUILD_DIR}/xijian-core"
cp -R "${CORE_DIR}/dist/xijian-core" "${BUILD_DIR}/xijian-core"

"${PY}" -c "
import py7zr, shutil, os

src = '${BUILD_DIR}/xijian-core'
archive = '${BUILD_DIR}/xijian-core.7z'

if os.path.exists(archive):
    os.remove(archive)

with py7zr.SevenZipFile(archive, mode='w', filters=[{'id': py7zr.FILTER_LZMA2}]) as z:
    z.writeall(src, arcname='xijian-core')

print(f'    created: {archive} ({os.path.getsize(archive) >> 20} MiB)')
"

# --- vendor py7zr for the Swift extraction helper ---------------------------
# The app needs to extract the 7z before the core is available, so we bundle
# py7zr + its pure-Python deps directly inside the .app bundle.

echo "==> vendoring py7zr extraction helper"

"${PY}" -m pip install py7zr texttable --quiet --target "${BUILD_DIR}/py7zr_bundle" 2>&1 | tail -1

# Remove a few unnecessary items to keep the bundle lean
rm -rf "${BUILD_DIR}/py7zr_bundle/bin" "${BUILD_DIR}/py7zr_bundle/__pycache__" 2>/dev/null || true

# --- copy into app resources ------------------------------------------------

cp "${BUILD_DIR}/xijian-core.7z" "${RESOURCES_DIR}/xijian-core.7z"

rm -rf "${RESOURCES_DIR}/py7zr_bundle"
cp -R "${BUILD_DIR}/py7zr_bundle" "${RESOURCES_DIR}/py7zr_bundle"

echo "==> done. artifacts:"
echo "    ${RESOURCES_DIR}/xijian-core.7z"
echo "    ${RESOURCES_DIR}/py7zr_bundle/"
