#!/usr/bin/env bash
# build-devkit.sh — build the standalone 隙间 Developer Kit binary.
#
# Why a wrapper script (instead of just running pyinstaller)
# -----------------------------------------------------------
# * Creates a project-local venv (`devkit/.venv`) so the DevKit build
#   environment stays clean and reproducible — it never touches the
#   repo-wide venv used by the API.
# * Installs the runtime deps from `devkit/requirements.txt` plus the
#   `pyinstaller` build tool.
# * Forces `--clean --noconfirm` so a stale `build/` cache never gets
#   picked up by accident.
# * Reads its inputs (output dir, target, venv path) from env vars so
#   CI can override without touching the script.
#
# Usage
# -----
#     ./build-devkit.sh                       # build everything (onedir + .app on macOS)
#     ./build-devkit.sh --target=dir          # onedir only
#     ./build-devkit.sh --target=app          # macOS .app only (no-op on Linux/Win)
#     ./build-devkit.sh --clean-venv          # wipe .venv before installing
#     PYI=/path/to/pyinstaller ./build-devkit.sh
#
# Output
# ------
#     dist/xijian-devkit/                     # onedir binary
#     dist/隙间开发者工具.app                  # macOS .app bundle (double-clickable)
#
# Design contract (do not violate without updating xijian-devkit.spec)
# -------------------------------------------------------------------
# * The DevKit must NOT depend on `xijian_api` or `flask`.  The spec
#   excludes both packages — this script never installs them into the
#   build venv either.
# * Output is onedir, not onefile — the .app bundle on macOS needs the
#   companion files for code signing & notarization.

set -euo pipefail

# --- locate ourselves & the repo root --------------------------------------

DEVKIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${DEVKIT_DIR}/.." && pwd)"
SPEC="${DEVKIT_DIR}/xijian-devkit.spec"
VENV="${DEVKIT_DIR}/.venv"

# --- defaults overridable via env ------------------------------------------

TARGET="${DEVKIT_TARGET:-all}"        # all | dir | app
PYI_BIN="${PYI:-}"                    # explicit pyinstaller path
SKIP_INSTALL=0
WIPE_VENV=0

for arg in "$@"; do
    case "$arg" in
        --target=dir)  TARGET="dir" ;;
        --target=app)  TARGET="app" ;;
        --target=all)  TARGET="all" ;;
        --clean-venv)  WIPE_VENV=1 ;;
        --no-install)  SKIP_INSTALL=1 ;;
        -h|--help)
            sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown arg: $arg" >&2
            exit 2
            ;;
    esac
done

# --- preflight -------------------------------------------------------------

if [[ ! -f "${SPEC}" ]]; then
    echo "ERROR: ${SPEC} not found" >&2
    exit 1
fi

# --- venv management -------------------------------------------------------

if [[ "${WIPE_VENV}" == "1" && -d "${VENV}" ]]; then
    echo "==> wiping ${VENV}"
    rm -rf "${VENV}"
fi

if [[ ! -d "${VENV}" ]]; then
    if [[ "${SKIP_INSTALL}" == "1" ]]; then
        echo "ERROR: ${VENV} missing and --no-install set" >&2
        exit 1
    fi
    echo "==> creating venv at ${VENV}"
    python3 -m venv "${VENV}"
    NEED_INSTALL=1
else
    NEED_INSTALL=0
fi

PIP="${VENV}/bin/pip"
PY="${VENV}/bin/python"

# Windows venv uses Scripts/ instead of bin/
if [[ ! -f "${PIP}" ]]; then
    PIP="${VENV}/Scripts/pip"
    PY="${VENV}/Scripts/python"
fi
if [[ ! -f "${PIP}" && -f "${VENV}/Scripts/pip.exe" ]]; then
    PIP="${VENV}/Scripts/pip.exe"
    PY="${VENV}/Scripts/python.exe"
fi

if [[ "${SKIP_INSTALL}" != "1" && ( "${NEED_INSTALL}" == "1" || "${PYI_BIN}" == "" ) ]]; then
    echo "==> installing runtime deps + pyinstaller"
    "${PIP}" install --quiet --upgrade pip
    "${PIP}" install --quiet -r "${DEVKIT_DIR}/requirements.txt"
    "${PIP}" install --quiet "pyinstaller>=6.0"
fi

# --- locate pyinstaller ---------------------------------------------------

if [[ -z "${PYI_BIN}" ]]; then
    PYI_BIN="${VENV}/bin/pyinstaller"
    if [[ ! -f "${PYI_BIN}" ]]; then
        PYI_BIN="${VENV}/Scripts/pyinstaller"
    fi
    if [[ ! -f "${PYI_BIN}" && -f "${VENV}/Scripts/pyinstaller.exe" ]]; then
        PYI_BIN="${VENV}/Scripts/pyinstaller.exe"
    fi
fi
if [[ ! -x "${PYI_BIN}" ]]; then
    echo "ERROR: pyinstaller not found at ${PYI_BIN}" >&2
    exit 1
fi

# --- run the build --------------------------------------------------------

cd "${DEVKIT_DIR}"
echo "==> pyinstaller ${SPEC} (target=${TARGET})"
"${PYI_BIN}" --clean --noconfirm "${SPEC}"

# --- emit a tiny summary so CI logs are greppable -------------------------

echo
echo "==> build done. artifacts:"
shopt -s nullglob
for f in "${DEVKIT_DIR}/dist/xijian-devkit" "${DEVKIT_DIR}/dist/xijian-devkit.exe" \
         "${DEVKIT_DIR}/dist/隙间开发者工具" "${DEVKIT_DIR}/dist/隙间开发者工具.app"; do
    [[ -e "$f" ]] && echo "    $f"
done