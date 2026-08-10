#!/usr/bin/env bash
# =============================================================================
# XiJian Core API — 一条龙开发脚本 (macOS / Linux / bash)
#
# 功能：检测并准备 conda 环境 → 安装/更新依赖 → (可选)编译 AI 后端 →
#       (可选)运行测试 → (可选)启动服务 → (可选)打包为独立可执行文件。
#       所有步骤均可单独开关，支持交互式向导和纯命令行两种模式。
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
#   # 打包为独立可执行文件（onedir 模式）
#   ./core/scripts/dev.sh --build
#
#   # 打包后自动生成 zip 分发包
#   ./core/scripts/dev.sh --build --zip
#
#   # 指定 conda 环境名（默认 xijianBase）
#   ./core/scripts/dev.sh --env myenv --run --dev
#
#   # 显示当前环境与配置信息
#   ./core/scripts/dev.sh --info
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
PROJECT_DIR="$(cd "$CORE_DIR/.." && pwd)"

# 记录原始参数个数，用于自动判断是否进入交互模式
ORIGINAL_ARGC=$#

# ANSI 颜色（非交互终端自动禁用）
if [[ -t 2 ]]; then
    C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'
    C_CYN=$'\033[36m'; C_MAG=$'\033[35m'; C_RST=$'\033[0m'
    C_BOLD=$'\033[1m'
else
    C_RED=""; C_YEL=""; C_GRN=""; C_CYN=""; C_MAG=""; C_RST=""; C_BOLD=""
fi

log()  { printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$*" >&2; }
warn() { printf "%s[xijian WARN]%s %s\n" "$C_YEL" "$C_RST" "$*" >&2; }
err()  { printf "%s[xijian ERROR]%s %s\n" "$C_RED" "$C_RST" "$*" >&2; }
ok()   { printf "%s[xijian OK]%s %s\n" "$C_GRN" "$C_RST" "$*" >&2; }
step() { printf "%s[xijian STEP]%s %s\n" "$C_MAG" "$C_RST" "$*" >&2; }

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
DO_BUILD=0
DO_ZIP=0
DO_INFO=0
DO_CLEAN=0
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
        --build)        DO_BUILD=1; shift ;;
        --zip)          DO_ZIP=1; DO_BUILD=1; shift ;;
        --clean)        DO_CLEAN=1; shift ;;
        --info)         DO_INFO=1; shift ;;
        -i|--interactive) INTERACTIVE=1; shift ;;
        --help|-h)
            grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        --) shift; while [[ $# -gt 0 ]]; do SERVER_ARGS+=("$1"); shift; done ;;
        *) SERVER_ARGS+=("$1"); shift ;;
    esac
done

# 无任何参数时自动进入交互模式
if [[ $ORIGINAL_ARGC -eq 0 && $INTERACTIVE -eq 0 && $DO_INFO -eq 0 ]]; then
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

    # 5. 操作选择（启动 / 打包 / 两者都做 / 都不做）
    prompt_choice "选择要执行的操作" 1 \
        "仅启动服务" \
        "仅打包为可执行文件" \
        "打包并生成 zip 分发包" \
        "启动 + 打包" \
        "都不执行（仅准备环境）"
    case "$ANSWER" in
        1) DO_RUN=1 ;;
        2) DO_BUILD=1 ;;
        3) DO_BUILD=1; DO_ZIP=1 ;;
        4) DO_RUN=1; DO_BUILD=1 ;;
        5) ;;
    esac

    # 6. 启动参数（仅当需要启动时）
    if [[ $DO_RUN -eq 1 ]]; then
        if prompt_yesno "  开发模式 (自动生成 token、启用测试路由)？" "Y"; then
            SERVER_ARGS+=("--dev")
        fi
        prompt_input "  监听端口" "18500"
        SERVER_ARGS+=("--port" "$ANSWER")
        prompt_input "  监听地址" "0.0.0.0"
        SERVER_ARGS+=("--host" "$ANSWER")
        prompt_choice "  日志级别" 2 "DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL"
        local levels=("DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL")
        SERVER_ARGS+=("--log-level" "${levels[$((ANSWER-1))]}")
        if prompt_yesno "  写入日志文件？" "N"; then
            prompt_input "  日志文件路径" "$HOME/Library/Application Support/XiJian/Core/logs/xijian-api.log"
            SERVER_ARGS+=("--log-file" "$ANSWER")
        fi
        if prompt_yesno "  指定自定义配置文件？" "N"; then
            prompt_input "  config.toml 路径" ""
            SERVER_ARGS+=("--config" "$ANSWER")
        fi
        if prompt_yesno "  仅冒烟自检 (--no-serve，不真正启动)？" "N"; then
            SERVER_ARGS+=("--no-serve")
        fi
    fi

    # 7. 打包选项（仅当需要打包时）
    if [[ $DO_BUILD -eq 1 ]]; then
        if prompt_yesno "  打包后自动生成 zip 分发包？" "Y"; then
            DO_ZIP=1
        fi
        if prompt_yesno "  打包前清理构建缓存 (build/ dist/)？" "Y"; then
            DO_CLEAN=1
        fi
    fi

    # 8. 确认
    printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$bar" >&2
    printf "%s[xijian]%s 即将执行的操作:\n" "$C_CYN" "$C_RST" >&2
    printf "  - conda 环境      : %s\n" "$ENV_NAME" >&2
    [[ $DO_INSTALL -eq 1 ]] && printf "  - 安装依赖        : 是\n" >&2 || printf "  - 安装依赖        : 否\n" >&2
    [[ $WITH_MLX -eq 1 ]]   && printf "  - MLX 后端        : 是%s\n" "${MLX_PATH:+ ($MLX_PATH)}" >&2
    [[ $WITH_GGUF -eq 1 ]]  && printf "  - GGUF 后端       : 是%s\n" "${GGUF_PATH:+ ($GGUF_PATH)}" >&2
    [[ $DO_TEST -eq 1 ]]    && printf "  - 运行测试        : 是\n" >&2
    [[ $DO_RUN -eq 1 ]]     && printf "  - 启动服务        : 是 (%s)\n" "${SERVER_ARGS[*]:-无额外参数}" >&2
    [[ $DO_BUILD -eq 1 ]]   && printf "  - 打包可执行文件  : 是\n" >&2
    [[ $DO_ZIP -eq 1 ]]     && printf "  - 生成 zip 分发包 : 是\n" >&2
    [[ $DO_CLEAN -eq 1 ]]   && printf "  - 清理构建缓存    : 是\n" >&2
    printf "%s[xijian]%s %s\n" "$C_CYN" "$C_RST" "$bar" >&2
    if ! prompt_yesno "确认执行？" "Y"; then
        warn "用户取消，退出。"
        exit 0
    fi
}

[[ $INTERACTIVE -eq 1 ]] && run_interactive

# -----------------------------------------------------------------------------
# --info: 显示环境信息后退出
# -----------------------------------------------------------------------------
if [[ $DO_INFO -eq 1 ]]; then
    log "XiJian Core API 环境信息"
    log "  项目根目录  : $PROJECT_DIR"
    log "  core 目录   : $CORE_DIR"
    log "  scripts 目录: $SCRIPT_DIR"
    log "  conda 环境  : $ENV_NAME"
    log "  Python 版本 : $PY_VERSION"
    if command -v python >/dev/null 2>&1; then
        log "  当前 Python : $(python -V 2>&1) ($(command -v python))"
    fi
    if [[ -f "$CORE_DIR/config.toml" ]]; then
        log "  配置文件    : $CORE_DIR/config.toml"
    fi
    exit 0
fi

# -----------------------------------------------------------------------------
# conda 检测与激活
# -----------------------------------------------------------------------------
CONDA_BASE=""
detect_conda() {
    if command -v conda >/dev/null 2>&1; then
        CONDA_BASE="$(conda info --base 2>/dev/null || true)"
        if [[ -z "$CONDA_BASE" ]]; then
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
    step "安装/更新核心依赖 (pip install -e \".[test]\") ..."
    pip install -e ".[test]"
    ok "核心依赖就绪"
else
    log "跳过依赖安装 (--no-install)"
fi

# -----------------------------------------------------------------------------
# 可选：编译 AI 后端
# -----------------------------------------------------------------------------
if [[ $WITH_MLX -eq 1 ]]; then
    step "安装 MLX 后端 ..."
    if [[ -n "$MLX_PATH" ]]; then
        log "使用本地源码路径: $MLX_PATH"
        pip install -e "$MLX_PATH" || { warn "本地 mlx-lm 安装失败，回退到 PyPI 版本"; pip install mlx-lm; }
    else
        pip install mlx-lm
    fi
    ok "MLX 后端安装完成"
fi

if [[ $WITH_GGUF -eq 1 ]]; then
    step "安装 GGUF 后端 (llama-cpp-python) ..."
    if [[ -n "$GGUF_PATH" ]]; then
        log "使用本地源码路径: $GGUF_PATH"
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
    step "运行测试套件 (pytest -q) ..."
    pytest -q
    ok "测试通过"
fi

# -----------------------------------------------------------------------------
# 可选：打包为独立可执行文件
# -----------------------------------------------------------------------------
build_executable() {
    step "打包为独立可执行文件 (PyInstaller onedir 模式) ..."

    # 1. 确保 pyinstaller 已安装
    if ! python -c "import PyInstaller" 2>/dev/null; then
        log "安装 PyInstaller ..."
        pip install pyinstaller
    fi

    # 2. 清理旧构建（如果指定了 --clean）
    if [[ $DO_CLEAN -eq 1 ]]; then
        log "清理构建缓存 (build/ dist/) ..."
        rm -rf "$CORE_DIR/build" "$CORE_DIR/dist"
    fi

    # 3. 运行 PyInstaller
    log "运行 PyInstaller ..."
    cd "$CORE_DIR"
    pyinstaller scripts/xijian-api.spec --noconfirm --clean

    local dist_dir="$CORE_DIR/dist/xijian-api"
    if [[ ! -d "$dist_dir" ]]; then
        err "打包失败：未找到 $dist_dir"
        exit 1
    fi
    ok "可执行文件已生成: $dist_dir"

    # 4. 拷贝配置文件和说明文档到 dist 目录
    log "拷贝配置文件和说明文档 ..."
    if [[ -f "$CORE_DIR/config.toml" ]]; then
        cp "$CORE_DIR/config.toml" "$dist_dir/config.toml"
        log "  已拷贝 config.toml"
    fi
    if [[ -f "$SCRIPT_DIR/dist-readme.txt" ]]; then
        cp "$SCRIPT_DIR/dist-readme.txt" "$dist_dir/README.txt"
        log "  已拷贝 README.txt"
    fi

    # 5. 创建空目录（运行时需要）
    mkdir -p "$dist_dir/logs" "$dist_dir/tmp" "$dist_dir/data" "$dist_dir/external_libs"
    log "  已创建运行时目录 (logs/ tmp/ data/ external_libs/)"

    # 6. 显示构建产物大小
    local size
    size=$(du -sh "$dist_dir" 2>/dev/null | awk '{print $1}')
    ok "打包完成，产物大小: $size"
    log "  产物位置: $dist_dir"

    # 7. 可选：生成 zip 分发包
    if [[ $DO_ZIP -eq 1 ]]; then
        step "生成 zip 分发包 ..."
        local platform_tag
        local arch_tag
        arch_tag="$(uname -m 2>/dev/null || echo 'x64')"
        case "$(uname -s)" in
            Darwin) platform_tag="macos" ;;
            Linux)  platform_tag="linux" ;;
            MINGW*|MSYS*|CYGWIN*) platform_tag="windows" ;;
            *) platform_tag="$(uname -s | tr '[:upper:]' '[:lower:]')" ;;
        esac
        local zip_name="xijian-core-${platform_tag}-${arch_tag}.zip"
        local zip_path="$CORE_DIR/dist/$zip_name"

        # 先删除旧 zip
        rm -f "$zip_path"

        # 进入 dist 目录打包（确保 zip 内顶层是 xijian-api/）
        cd "$CORE_DIR/dist"
        if command -v zip >/dev/null 2>&1; then
            zip -r "$zip_name" "xijian-api/"
        elif command -v 7z >/dev/null 2>&1; then
            7z a -tzip "$zip_name" "xijian-api/"
        else
            warn "未找到 zip 或 7z 命令，跳过 zip 生成"
            warn "可手动压缩 $dist_dir 目录"
            return 0
        fi

        local zip_size
        zip_size=$(du -sh "$zip_path" 2>/dev/null | awk '{print $1}')
        ok "zip 分发包已生成: $zip_path ($zip_size)"
        log "  UI 程序解压后运行: xijian-api/xijian-api --port 18500"
    fi
}

if [[ $DO_BUILD -eq 1 ]]; then
    build_executable
fi

# -----------------------------------------------------------------------------
# 可选：启动服务
# -----------------------------------------------------------------------------
if [[ $DO_RUN -eq 1 ]]; then
    log "启动 XiJian Core API，转发参数: ${SERVER_ARGS[*]:-(无)}"
    exec python -m xijian_api "${SERVER_ARGS[@]}"
elif [[ $DO_BUILD -eq 0 && $DO_INFO -eq 0 ]]; then
    ok "环境准备完成。常用命令:"
    log "  ./core/scripts/dev.sh --run --dev --port 18600"
    log "  ./core/scripts/dev.sh --run --dev --log-level DEBUG --log-file \"$HOME/Library/Application Support/XiJian/Core/logs/xijian-api.log\""
    log "  ./core/scripts/dev.sh --build --zip          # 打包为可执行文件"
    log "  ./core/scripts/dev.sh --info                 # 显示环境信息"
    log "  ./core/scripts/dev.sh -i                     # 交互式向导"
fi
