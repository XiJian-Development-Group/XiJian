#!/usr/bin/env bash
# build-macapp.sh — Build the XiJian macOS native app.
#
# Orchestrates:
#   1. build-core.sh         — freeze + 7z the Flask server binary
#   2. xcodegen              — generate .xcodeproj from Project.yml
#   3. xcodebuild            — compile + sign the .app bundle
#
# Usage
# -----
#     ./build-macapp.sh                  # full build (Debug)
#     ./build-macapp.sh --release        # Release build
#     ./build-macapp.sh --skip-core      # skip core rebuild (faster iteration)
#     ./build-macapp.sh --clean          # wipe build artifacts first
#

set -euo pipefail

MACAPP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIGURATION="${CONFIGURATION:-Debug}"

SKIP_CORE=0
CLEAN=0

for arg in "$@"; do
    case "$arg" in
        --release) CONFIGURATION="Release" ;;
        --skip-core) SKIP_CORE=1 ;;
        --clean) CLEAN=1 ;;
        -h|--help)
            sed -n '3,12p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
    esac
done

if [[ "${CLEAN}" == "1" ]]; then
    echo "==> cleaning"
    rm -rf "${MACAPP_DIR}/build" "${MACAPP_DIR}/XiJian.xcodeproj"
fi

# Step 1: Build the core server binary
if [[ "${SKIP_CORE}" == "0" ]]; then
    echo "==> [1/3] building core server binary"
    "${MACAPP_DIR}/build-core.sh"
else
    echo "==> [1/3] skipping core build (--skip-core)"
fi

# Step 2: Generate Xcode project
echo "==> [2/3] generating Xcode project with XcodeGen"
if ! command -v xcodegen &>/dev/null; then
    echo "ERROR: xcodegen not found. Install with: brew install xcodegen" >&2
    exit 1
fi

cd "${MACAPP_DIR}"
xcodegen generate --project . --spec Project.yml
echo "    -> ${MACAPP_DIR}/XiJian.xcodeproj"

# Step 3: Build with xcodebuild
echo "==> [3/3] building XiJian (${CONFIGURATION})"
xcodebuild -project "${MACAPP_DIR}/XiJian.xcodeproj" \
           -scheme XiJian \
           -configuration "${CONFIGURATION}" \
           -derivedDataPath "${MACAPP_DIR}/build/DerivedData" \
           build

# Locate the built app
APP_PATH="${MACAPP_DIR}/build/DerivedData/Build/Products/${CONFIGURATION}/隙间.app"
echo ""
echo "==> build complete: ${APP_PATH}"
