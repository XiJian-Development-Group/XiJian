#!/usr/bin/env bash
# build-core.sh — 将 XiJian Core（Python Flask API）用 PyInstaller 打包为
#                 onedir 多文件产物，并嵌入 macapp/Resources/Core/。
#
# 设计目标（用户 2026-08-07 确认）：
#   * 用 PyInstaller 打成【多个二进制文件】（onedir 模式，非单文件）
#   * 避免首次启动时解压/释放资源拖慢启动
#   * 产物直接以文件目录形式嵌入 .app bundle，启动时复制即可运行
#
# 用法
# ----
#     ./build-core.sh            # 正常构建
#     ./build-core.sh --clean    # 先清理 build/dist 缓存
#
# 产物
# ----
#     macapp/Resources/Core/xijian-api      — 可执行文件
#     macapp/Resources/Core/_internal/      — PyInstaller 运行时（Python+依赖）
#     macapp/Resources/Core/config.toml     — 默认配置（用户可编辑）
#     macapp/Resources/Core/README.txt      — 使用说明
#
# 依赖
# ----
#   * conda 环境 xijianBase（/opt/anaconda3/envs/xijianBase）
#   * pyinstaller（缺失时自动安装到 xijianBase）

set -euo pipefail

MACAPP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${MACAPP_DIR}/.." && pwd)"
CORE_DIR="${REPO_ROOT}/core"
# 注意：必须用 macapp/scripts/xijian-api.spec 而非 core/scripts/xijian-api.spec。
# core 侧的 spec 在 excludes 中排除了 sqlite3（旧版），而 store.py 需要 sqlite3，
# 用 core 侧 spec 打出的包会因 `No module named 'sqlite3'` 无法启动。
SPEC_FILE="${MACAPP_DIR}/scripts/xijian-api.spec"

# conda 环境铁律：XiJian 项目必须用 xijianBase
PY="/opt/anaconda3/envs/xijianBase/bin/python"
if [[ ! -x "${PY}" ]]; then
    echo "ERROR: xijianBase 环境不存在: ${PY}" >&2
    echo "请先创建: conda create -n xijianBase python=3.11" >&2
    exit 1
fi

BUILD_DIR="${MACAPP_DIR}/build"
RESOURCES_DIR="${MACAPP_DIR}/Resources/Core"

CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        -h|--help)
            sed -n '3,12p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
    esac
done

echo "==> using Python: $("${PY}" --version 2>&1)"

# --- 确保 pyinstaller 可用 ------------------------------------------------

"${PY}" -m pip install --quiet --upgrade pip
"${PY}" -m pip install --quiet "pyinstaller>=6.0"

# --- 清理 ----------------------------------------------------------------

if [[ "${CLEAN}" == "1" ]]; then
    echo "==> cleaning build artifacts"
    rm -rf "${BUILD_DIR}"
fi

mkdir -p "${BUILD_DIR}"

# --- PyInstaller onedir 打包 ----------------------------------------------
# 直接在 core/scripts/xijian-api.spec 基础上打包（只读引用，不改 core 目录），
# 输出重定向到 macapp/build/，避免污染 core 目录。
# onedir 模式（COLLECT）：产物为多文件目录，启动无需解压。

echo "==> building xijian-core with PyInstaller (onedir)"
# PYTHONPATH: 让 spec 里的 collect_submodules("xijian_api") 能导入 core 包
PYTHONPATH="${CORE_DIR}" "${PY}" -m PyInstaller \
    "${SPEC_FILE}" \
    --noconfirm \
    --clean \
    --distpath "${BUILD_DIR}/dist" \
    --workpath "${BUILD_DIR}/work" 2>&1 | tail -8

# --- 校验产物 ------------------------------------------------------------

DIST_CORE="${BUILD_DIR}/dist/xijian-api"
if [[ ! -x "${DIST_CORE}/xijian-api" ]]; then
    echo "ERROR: PyInstaller 产物未找到: ${DIST_CORE}/xijian-api" >&2
    exit 1
fi

# --- 复制到 Resources/Core（嵌入 app bundle）-----------------------------

echo "==> embedding core into ${RESOURCES_DIR}"
rm -rf "${RESOURCES_DIR}"
mkdir -p "${RESOURCES_DIR}"

# 可执行文件 + _internal 运行时
cp -R "${DIST_CORE}/xijian-api" "${RESOURCES_DIR}/xijian-api"
cp -R "${DIST_CORE}/_internal" "${RESOURCES_DIR}/_internal"

# 配置文件与说明（spec 不负责拷贝，这里手动带）
if [[ -f "${CORE_DIR}/config.toml" ]]; then
    cp "${CORE_DIR}/config.toml" "${RESOURCES_DIR}/config.toml"
fi
if [[ -f "${CORE_DIR}/scripts/dist-readme.txt" ]]; then
    cp "${CORE_DIR}/scripts/dist-readme.txt" "${RESOURCES_DIR}/README.txt"
fi

# 去掉 pycache 等冗余
find "${RESOURCES_DIR}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 清除运行时生成物（token / 日志等），避免被 xcodegen 打进 .app bundle
rm -rf "${RESOURCES_DIR}/run" "${RESOURCES_DIR}/logs"

echo "==> done. embedded core:"
du -sh "${RESOURCES_DIR}"
echo "    ${RESOURCES_DIR}/xijian-api"
echo "    ${RESOURCES_DIR}/_internal/"
echo "    ${RESOURCES_DIR}/config.toml"
