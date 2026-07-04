#!/usr/bin/env bash
# build.sh — 隙间(XiJian) 统一构建脚本
#
# 默认行为 (./build.sh):
#   1. 检查 Python ≥ 3.11
#   2. 准备 venv (默认 core/.venv, 已存在则复用)
#   3. 升级 pip, 装 build / wheel
#   4. 装核心依赖 + 测试依赖 (pip install -e ".[test]")
#   5. 跑全套测试 (pytest)
#   6. 打包 wheel + sdist 到 ./dist/
#
# 常用选项:
#   --with-devkit    装 devkit 依赖 (py7zr + pywebview), 首次在 macOS 上跑 devkit 前用
#   --with-all       装所有可选依赖 (test + devkit)
#   --skip-test      跳过测试 (调试打包时用)
#   --skip-package   跳过打包 (只跑测试时用)
#   --clean          先删 ./dist/ ./build/ 以及所有 *.egg-info 再构建
#   --venv PATH      指定 venv 路径 (默认 core/.venv)
#   --python BIN     指定 Python 解释器 (默认 python3)
#   --help           显示帮助
#
# 退出码:
#   0 = 成功, 1 = 环境问题, 2 = 测试失败, 3 = 打包失败

set -euo pipefail

# ---- 路径与默认值 -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="${SCRIPT_DIR}/core"
VENV_PATH="${CORE_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DIST_DIR="${SCRIPT_DIR}/dist"
BUILD_DIR="${SCRIPT_DIR}/build"

WITH_DEVKIT=0
WITH_ALL=0
SKIP_TEST=0
SKIP_PACKAGE=0
DO_CLEAN=0

# ---- 颜色 (没有 tty 时降级) -------------------------------------------
if [[ -t 1 ]]; then
    C_RED=$'\033[0;31m'
    C_GRN=$'\033[0;32m'
    C_YEL=$'\033[0;33m'
    C_BLU=$'\033[0;34m'
    C_DIM=$'\033[2m'
    C_OFF=$'\033[0m'
else
    C_RED="" C_GRN="" C_YEL="" C_BLU="" C_DIM="" C_OFF=""
fi

log()  { printf "%s[build]%s %s\n" "${C_BLU}" "${C_OFF}" "$*"; }
ok()   { printf "%s[ ok ]%s %s\n" "${C_GRN}" "${C_OFF}" "$*"; }
warn() { printf "%s[warn]%s %s\n" "${C_YEL}" "${C_OFF}" "$*"; }
err()  { printf "%s[fail]%s %s\n" "${C_RED}" "${C_OFF}" "$*" >&2; }

# ---- 解析参数 ---------------------------------------------------------
usage() {
    sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-devkit)    WITH_DEVKIT=1 ;;
        --with-all)       WITH_ALL=1 ;;
        --skip-test)      SKIP_TEST=1 ;;
        --skip-package)   SKIP_PACKAGE=1 ;;
        --clean)          DO_CLEAN=1 ;;
        --venv)           VENV_PATH="$2"; shift ;;
        --python)         PYTHON_BIN="$2"; shift ;;
        -h|--help)        usage ;;
        *) err "未知参数: $1"; usage ;;
    esac
    shift
done

# ---- 1. 环境检查 ------------------------------------------------------
log "检查 Python 解释器: ${PYTHON_BIN}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    err "找不到 ${PYTHON_BIN}; 装 Python 3.11+ 或用 --python 指定"
    exit 1
fi

PY_VERSION=$("${PYTHON_BIN}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$("${PYTHON_BIN}" -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$("${PYTHON_BIN}" -c 'import sys; print(sys.version_info[1])')

if [[ "${PY_MAJOR}" -lt 3 || ( "${PY_MAJOR}" -eq 3 && "${PY_MINOR}" -lt 11 ) ]]; then
    err "需要 Python ≥ 3.11, 当前 ${PY_VERSION}"
    err "  (pyproject.toml: requires-python = \">=3.11\")"
    exit 1
fi
ok "Python ${PY_VERSION} ✓"

# ---- 2. 准备 venv -----------------------------------------------------
if [[ ! -d "${VENV_PATH}" ]]; then
    log "创建 venv: ${VENV_PATH}"
    "${PYTHON_BIN}" -m venv "${VENV_PATH}"
fi

# shellcheck disable=SC1091
source "${VENV_PATH}/bin/activate"

VENV_PY="${VENV_PATH}/bin/python"
VENV_PIP="${VENV_PATH}/bin/pip"

log "升级 pip + setuptools + wheel"
"${VENV_PIP}" install --quiet --upgrade pip setuptools wheel

# ---- 3. 清理旧产物 (可选) --------------------------------------------
if [[ "${DO_CLEAN}" -eq 1 ]]; then
    log "清理 ./dist/ ./build/ 和 *.egg-info"
    rm -rf "${DIST_DIR}" "${BUILD_DIR}"
    find "${SCRIPT_DIR}" -type d -name "*.egg-info" -prune -exec rm -rf {} +
    find "${CORE_DIR}"  -type d -name "*.egg-info" -prune -exec rm -rf {} +
    find "${CORE_DIR}"  -type d -name "__pycache__" -prune -exec rm -rf {} +
    ok "清理完成"
fi

# ---- 4. 装依赖 -------------------------------------------------------
EXTRA=""
if [[ "${WITH_ALL}" -eq 1 ]]; then
    EXTRA="[all]"
elif [[ "${WITH_DEVKIT}" -eq 1 ]]; then
    EXTRA="[test,devkit]"
else
    EXTRA="[test]"
fi

log "以 editable 模式安装 xijian-api${EXTRA}"
"${VENV_PIP}" install --quiet -e "${CORE_DIR}${EXTRA}"
ok "依赖装好"

# ---- 5. 测试 ---------------------------------------------------------
if [[ "${SKIP_TEST}" -eq 0 ]]; then
    log "跑测试 (pytest -q)"
    if ! ( cd "${CORE_DIR}" && "${VENV_PY}" -m pytest --tb=short -q ); then
        err "测试失败"
        exit 2
    fi
    ok "测试全过"
else
    warn "跳过测试 (--skip-test)"
fi

# ---- 6. 打包 ---------------------------------------------------------
if [[ "${SKIP_PACKAGE}" -eq 0 ]]; then
    log "准备打包 (wheel + sdist) → ${DIST_DIR}/"
    mkdir -p "${DIST_DIR}"

    # PEP 517 标准做法: 用 python -m build
    # 优先用 build 包的 wheel 子命令 (隔离构建, 不污染源码树)
    if "${VENV_PY}" -c "import build" 2>/dev/null; then
        ( cd "${CORE_DIR}" && "${VENV_PY}" -m build \
            --wheel \
            --sdist \
            --outdir "${DIST_DIR}" )
    else
        # 备选: pip wheel (没有 build 包时)
        warn "未装 'build' 包, 退回到 pip wheel (效果一样, 但少了隔离)"
        "${VENV_PIP}" install --quiet build
        ( cd "${CORE_DIR}" && "${VENV_PY}" -m build \
            --wheel \
            --sdist \
            --outdir "${DIST_DIR}" )
    fi

    if [[ ! -d "${DIST_DIR}" ]] || [[ -z "$(ls -A "${DIST_DIR}" 2>/dev/null)" ]]; then
        err "打包后 dist/ 为空"
        exit 3
    fi

    ok "打包完成"
    echo
    log "产物:"
    ls -lh "${DIST_DIR}/" | tail -n +2 | awk '{printf "  %-8s %s\n", $5, $9}'
else
    warn "跳过打包 (--skip-package)"
fi

echo
ok "全部完成 ✨"