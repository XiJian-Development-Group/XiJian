#!/usr/bin/env bash
# build-macapp.sh — 构建 XiJian macOS 原生 App。
#
# 编排流程：
#   1. build-core.sh      — PyInstaller onedir 打包 Core → 嵌入 Resources/Core/
#   2. xcodegen           — 从 project.yml 生成 XiJian.xcodeproj
#   3. xcodebuild         — 编译 .app（DerivedData 用系统默认位置，
#                           绝不放在 XiJian 项目目录内）
#
# 用法
# ----
#     ./build-macapp.sh                 # 完整构建（Debug）
#     ./build-macapp.sh --release       # Release 构建
#     ./build-macapp.sh --skip-core     # 跳过 Core 重建（快速迭代 UI）
#     ./build-macapp.sh --clean         # 先清理构建缓存
#
# 产物
# ----
#     ~/Library/Developer/Xcode/DerivedData/XiJian-*/Build/Products/<config>/XiJian.app

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
            sed -n '3,14p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
    esac
done

# --- 工具检查 --------------------------------------------------------------

if ! command -v xcodegen &>/dev/null; then
    echo "ERROR: xcodegen 未安装，请执行: brew install xcodegen" >&2
    exit 1
fi
if ! command -v xcodebuild &>/dev/null; then
    echo "ERROR: xcodebuild 不可用，请安装 Xcode" >&2
    exit 1
fi

if [[ "${CLEAN}" == "1" ]]; then
    echo "==> cleaning"
    rm -rf "${MACAPP_DIR}/build" "${MACAPP_DIR}/XiJian.xcodeproj"
fi

# --- 1/3: 构建 Core --------------------------------------------------------

if [[ "${SKIP_CORE}" == "0" ]]; then
    echo "==> [1/3] building core server binary"
    "${MACAPP_DIR}/build-core.sh"
else
    echo "==> [1/3] skipping core build (--skip-core)"
fi

# --- 2/3: xcodegen 生成工程 -------------------------------------------------

echo "==> [2/3] generating Xcode project with XcodeGen"
cd "${MACAPP_DIR}"
xcodegen generate --project . --spec project.yml
echo "    -> ${MACAPP_DIR}/XiJian.xcodeproj"

# --- 3/3: xcodebuild 构建 ---------------------------------------------------
# 注意：不传 -derivedDataPath，使用 Xcode 默认 DerivedData 位置，
#       避免在 XiJian 项目目录内产生构建产物。

echo "==> [3/3] building XiJian (${CONFIGURATION})"
xcodebuild -project "${MACAPP_DIR}/XiJian.xcodeproj" \
           -scheme XiJian \
           -configuration "${CONFIGURATION}" \
           build

# --- 定位产物 ---------------------------------------------------------------

echo ""
echo "==> build complete. 产物位于 DerivedData 默认位置:"
echo "    ~/Library/Developer/Xcode/DerivedData/XiJian-*/Build/Products/${CONFIGURATION}/XiJian.app"
