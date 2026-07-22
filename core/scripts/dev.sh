#!/usr/bin/env bash
# =============================================================================
# XiJian Core API — 一条龙开发脚本 (macOS / Linux / bash)
#
# 功能：检测并准备 conda 环境 → 安装/更新依赖 → (可选)编译 AI 后端 →
#       (可选)运行测试 → (可选)启动服务。所有步骤均可单独开关。
#
# 用法示例:
#   # 不传任何参数 → 进入交互式向导（推荐新手）
#   ./core/scripts/dev.sh
#
#   # 显式进入交互式向导
#   ./core/scripts/dev.sh -i
#
#   # 仅安装依赖（非交互，幂等）
#   ./core/scripts/dev.sh --no-install
#
#   # 安装 + 跑测试 + 以 dev 模式启动在 18600 端口
#   ./core/scripts/dev.sh --test --run --dev --port 18600
#
#   # 安装并编译 MLX 后端（macOS），使用本地 mlx-lm 源码路径
#   ./core/scripts/dev.sh --with-mlx --mlx-path ~/code/mlx-lm
#
#   # 安装并编译 GGUF 后端（llama-cpp-python）
#   ./core/scripts/dev.sh --with-gguf
#
#   # 指定 conda 环境名（默认 xijianBase）
#   ./core/scripts/dev.sh --env myenv --run --dev
#
# 任何未被本脚本识别的参数（如 --port / --dev / --log-level / --host …）
# 会在 --run 时原样转发给 `python -m xijian_api`。
# =============================================================================
set -euo pipefail

# -----------------------------------------------------------------------------
# 路径解析（脚本可从任意目录调用）
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 记录原始参数个数，用于自动判断是否进入交互模式
ORIGINAL_ARGC=$#

# ANSI 颜色（非交互终端自动禁用）
if [[ -t 2 ]]; then
    C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'
    C_CYN=$'\033[36m'; C_RST=$'\033[0m'
else
    C_RED=""; C_YEL=""; C_GRN=""; C_CYN=""; C_RST=""
fi

log()  { printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$*" >&2; }
warn() { printf "%s[xijian WARN]%s %s\n" "$C_YEL" "$C_RST" "$*" >&2; }
err()  { printf "%s[xijian ERROR]%s %s\n" "$C_RED" "$C_RST" "$*" >&2; }
ok()   { printf "%s[xijian OK]%s %s\n" "$C_GRN" "$C_RST" "$*" >&2; }

# -----------------------------------------------------------------------------
# 默认值
# -----------------------------------------------------------------------------
ENV_NAME="xijianBase"
PY_VERSION="3.12"
DO_INSTALL=1
WITH_MLX=0
WITH_GGUF=0
MLX_PATH=""
GGUF_PATH=""
DO_TEST=0
DO_RUN=0
INTERACTIVE=0
SERVER_ARGS=()

# -----------------------------------------------------------------------------
# 交互式输入辅助函数
# -----------------------------------------------------------------------------
# 读取 Y/N 回答，回车采用默认值；返回 0=Y，1=N
prompt_yesno() {
    local question="$1" default="${2:-Y}" reply
    local hint
    if [[ "$default" =~ ^[Yy] ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
    while true; do
        printf "%s[xijian Q]%s %s %s " "$C_CYN" "$C_RST" "$question" "$hint" >&2
        read -r reply </dev/tty || reply=""
        reply="${reply:-$default}"
        case "${reply:0:1}" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
        esac
    done
}

# 读取一行文本，回车采用默认值；结果写入全局 $ANSWER
prompt_input() {
    local question="$1" default="${2:-}" reply
    while true; do
        printf "%s[xijian Q]%s %s " "$C_CYN" "$C_RST" "${question}${default:+ (默认: $default)}" >&2
        read -r reply </dev/tty || reply=""
        reply="${reply:-$default}"
        if [[ -n "$reply" ]]; then ANSWER="$reply"; return 0; fi
        if [[ -z "$default" ]]; then continue; fi
        ANSWER="$reply"; return 0
    done
}

# 从多个选项中选一个，回车采用默认索引（1-based）；结果写入全局 $ANSWER
prompt_choice() {
    local question="$1" default_idx="${2:-1}"; shift 2
    local options=("$@") i
    while true; do
        printf "%s[xijian Q]%s %s\n" "$C_CYN" "$C_RST" "$question" >&2
        for i in "${!options[@]}"; do
            local marker=" "
            [[ $((i+1)) -eq $default_idx ]] && marker="*"
            printf "  %s %d) %s\n" "$marker" $((i+1)) "${options[$i]}" >&2
        done
        printf "请输入序号 (默认 %d): " "$default_idx" >&2
        local reply
        read -r reply </dev/tty || reply=""
        reply="${reply:-$default_idx}"
        if [[ "$reply" =~ ^[0-9]+$ ]] && (( reply >= 1 && reply <= ${#options[@]} )); then
            ANSWER="$reply"; return 0
        fi
        warn "无效输入: $reply"
    done
}

# -----------------------------------------------------------------------------
# 参数解析
# -----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)          ENV_NAME="$2"; shift 2 ;;
        --no-install)   DO_INSTALL=0; shift ;;
        --with-mlx)     WITH_MLX=1; shift ;;
        --with-gguf)    WITH_GGUF=1; shift ;;
        --mlx-path)     MLX_PATH="$2"; shift 2 ;;
        --gguf-path)    GGUF_PATH="$2"; shift 2 ;;
        --test)         DO_TEST=1; shift ;;
        --run)          DO_RUN=1; shift ;;
        -i|--interactive) INTERACTIVE=1; shift ;;
        --help|-h)
            grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        --) shift; while [[ $# -gt 0 ]]; do SERVER_ARGS+=("$1"); shift; done ;;
        *) SERVER_ARGS+=("$1"); shift ;;
    esac
done

# 无任何参数时自动进入交互模式（原始参数为 0 且未显式指定 --interactive）
if [[ $ORIGINAL_ARGC -eq 0 && $INTERACTIVE -eq 0 ]]; then
    INTERACTIVE=1
fi

# -----------------------------------------------------------------------------
# 交互式向导（仅当 INTERACTIVE=1 时执行）
# -----------------------------------------------------------------------------
run_interactive() {
    local bar="============================================================"
    printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$bar" >&2
    printf "%s[xijian]%s XiJian Core API 交互式启动向导\n" "$C_CYN" "$C_RST" >&2
    printf "%s[xijian]%s 未提供参数，进入交互模式。可随时用 Ctrl+C 退出。\n" "$C_CYN" "$C_RST" >&2
    printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$bar" >&2

    # 1. conda 环境
    prompt_input "conda 环境名" "$ENV_NAME"
    ENV_NAME="$ANSWER"

    # 2. 安装依赖
    if prompt_yesno "是否安装/更新核心依赖？" "Y"; then DO_INSTALL=1; else DO_INSTALL=0; fi

    # 3. AI 后端
    if prompt_yesno "是否安装 MLX 后端 (macOS Apple Silicon)？" "N"; then
        WITH_MLX=1
        if prompt_yesno "  使用本地源码路径安装？" "N"; then
            prompt_input "  mlx-lm 源码路径" ""
            MLX_PATH="$ANSWER"
        fi
    fi
    if prompt_yesno "是否安装 GGUF 后端 (llama-cpp-python)？" "N"; then
        WITH_GGUF=1
        if prompt_yesno "  使用本地源码路径安装？" "N"; then
            prompt_input "  llama-cpp-python 源码路径" ""
            GGUF_PATH="$ANSWER"
        fi
    fi

    # 4. 测试
    if prompt_yesno "是否运行测试套件 (pytest)？" "N"; then DO_TEST=1; fi

    # 5. 启动服务
    if prompt_yesno "是否启动 API 服务？" "Y"; then
        DO_RUN=1
        # dev 模式
        if prompt_yesno "  开发模式 (自动生成 token、启用测试路由)？" "Y"; then
            SERVER_ARGS+=("--dev")
        fi
        # 端口
        prompt_input "  监听端口" "18500"
        SERVER_ARGS+=("--port" "$ANSWER")
        # 主机
        prompt_input "  监听地址" "0.0.0.0"
        SERVER_ARGS+=("--host" "$ANSWER")
        # 日志级别
        prompt_choice "  日志级别" 1 "DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL"
        local levels=("DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL")
        SERVER_ARGS+=("--log-level" "${levels[$((ANSWER-1))]}")
        # 日志文件
        if prompt_yesno "  写入日志文件？" "N"; then
            prompt_input "  日志文件路径" "/tmp/xijian.log"
            SERVER_ARGS+=("--log-file" "$ANSWER")
        fi
        # 配置文件
        if prompt_yesno "  指定自定义配置文件？" "N"; then
            prompt_input "  config.toml 路径" ""
            SERVER_ARGS+=("--config" "$ANSWER")
        fi
        # 冒烟模式
        if prompt_yesno "  仅冒烟自检 (--no-serve，不真正启动)？" "N"; then
            SERVER_ARGS+=("--no-serve")
        fi
    fi

    # 6. 确认
    printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$bar" >&2
    printf "%s[xijian]%s 即将执行的操作:\n" "$C_CYN" "$C_RST" >&2
    printf "  - conda 环境      : %s\n" "$ENV_NAME" >&2
    [[ $DO_INSTALL -eq 1 ]] && printf "  - 安装依赖        : 是\n" >&2 || printf "  - 安装依赖        : 否\n" >&2
    [[ $WITH_MLX -eq 1 ]]   && printf "  - MLX 后端        : 是%s\n" "${MLX_PATH:+ ($MLX_PATH)}" >&2
    [[ $WITH_GGUF -eq 1 ]]  && printf "  - GGUF 后端       : 是%s\n" "${GGUF_PATH:+ ($GGUF_PATH)}" >&2
    [[ $DO_TEST -eq 1 ]]    && printf "  - 运行测试        : 是\n" >&2
    [[ $DO_RUN -eq 1 ]]     && printf "  - 启动服务        : 是 (%s)\n" "${SERVER_ARGS[*]:-无额外参数}" >&2
    printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$bar" >&2
    if ! prompt_yesno "确认执行？" "Y"; then
        warn "用户取消，退出。"
        exit 0
    fi
}

[[ $INTERACTIVE -eq 1 ]] && run_interactive

# -----------------------------------------------------------------------------
# conda 检测与激活
# -----------------------------------------------------------------------------
CONDA_BASE=""
detect_conda() {
    if command -v conda >/dev/null 2>&1; then
        CONDA_BASE="$(conda info --base 2>/dev/null || true)"
        if [[ -z "$CONDA_BASE" ]]; then
            # 兜底：从 conda 可执行文件推断
            local c; c="$(command -v conda)"
            CONDA_BASE="$(cd "$(dirname "$c")/.." && pwd)"
        fi
        return 0
    fi
    local candidate
    for candidate in /opt/anaconda3 /opt/miniconda3 /opt/miniforge3 \
                     "$HOME/anaconda3" "$HOME/miniconda3" "$HOME/miniforge3" \
                     "$HOME/mambaforge"; do
        if [[ -f "$candidate/etc/profile.d/conda.sh" ]]; then
            CONDA_BASE="$candidate"
            return 0
        fi
    done
    return 1
}

if ! detect_conda; then
    err "未找到 conda，请先安装 Anaconda/Miniconda/Miniforge 并加入 PATH。"
    exit 1
fi
log "检测到 conda: $CONDA_BASE"

# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    log "激活已存在的 conda 环境: $ENV_NAME"
else
    warn "conda 环境 [$ENV_NAME] 不存在，正在创建 (python=$PY_VERSION) ..."
    conda create -n "$ENV_NAME" "python=$PY_VERSION" -y
    ok "已创建 conda 环境: $ENV_NAME"
fi
conda activate "$ENV_NAME"

log "Python: $(python -V)  ($(command -v python))"

# -----------------------------------------------------------------------------
# 安装核心依赖
# -----------------------------------------------------------------------------
cd "$CORE_DIR"

if [[ $DO_INSTALL -eq 1 ]]; then
    log "安装/更新核心依赖 (pip install -e \".[test]\") ..."
    pip install -e ".[test]"
    ok "核心依赖就绪"
else
    log "跳过依赖安装 (--no-install)"
fi

# -----------------------------------------------------------------------------
# 可选：编译 AI 后端
# -----------------------------------------------------------------------------
if [[ $WITH_MLX -eq 1 ]]; then
    log "安装 MLX 后端 ..."
    if [[ -n "$MLX_PATH" ]]; then
        log "使用本地源码路径: $MLX_PATH"
        pip install -e "$MLX_PATH" || { warn "本地 mlx-lm 安装失败，回退到 PyPI 版本"; pip install mlx-lm; }
    else
        pip install mlx-lm
    fi
    ok "MLX 后端安装完成"
fi

if [[ $WITH_GGUF -eq 1 ]]; then
    log "安装 GGUF 后端 (llama-cpp-python) ..."
    if [[ -n "$GGUF_PATH" ]]; then
        log "使用本地源码路径: $GGUF_PATH"
        # 本地源码编译可能需要 CMAKE_ARGS；这里仅做基本安装
        pip install -e "$GGUF_PATH" || { warn "本地 llama-cpp-python 安装失败，回退到 PyPI 版本"; pip install llama-cpp-python; }
    else
        # macOS Metal 加速建议: CMAKE_ARGS="-DGGUF_METAL=on" pip install llama-cpp-python
        pip install llama-cpp-python
    fi
    ok "GGUF 后端安装完成"
fi

# -----------------------------------------------------------------------------
# 可选：运行测试
# -----------------------------------------------------------------------------
if [[ $DO_TEST -eq 1 ]]; then
    log "运行测试套件 (pytest -q) ..."
    pytest -q
    ok "测试通过"
fi

# -----------------------------------------------------------------------------
# 可选：启动服务
# -----------------------------------------------------------------------------
if [[ $DO_RUN -eq 1 ]]; then
    log "启动 XiJian Core API，转发参数: ${SERVER_ARGS[*]:-(无)}"
    exec python -m xijian_api "${SERVER_ARGS[@]}"
else
    ok "环境准备完成。使用 --run 启动服务，例如:"
    log "  ./core/scripts/dev.sh --run --dev --port 18600"
    log "  ./core/scripts/dev.sh --run --dev --log-level DEBUG --log-file /tmp/xijian.log"
fi
