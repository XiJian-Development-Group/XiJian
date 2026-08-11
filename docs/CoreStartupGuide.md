# XiJian Core 启动指南

> 文档版本：v1.1  
> 适用版本：XiJian Core API v1.2.0-Alpha（PEP 440：`1.2.0a0`）及以上  
> 维护者：隙间开发组

---

## 1. 概览

XiJian Core 是一个基于 Flask 的 OpenAI 兼容 API 服务端，提供：
- OpenAI 兼容的聊天补全、Embeddings、音频、图像、视频生成接口
- 角色/世界/记忆/状态等隙间特有业务接口
- WebSocket 实时事件流
- 模型管理、过载防护、安全审查等运维能力

**默认监听地址**：`127.0.0.1:18500`（**无需任何环境变量即可启动**）  
端口、主机、开发模式、日志级别等均支持 **命令行参数 > 环境变量 > config.toml > 内置默认值** 的优先级，零配置即可运行。

---

## 2. 环境准备

### 2.1 系统要求
| 平台 | 最低版本 | 推荐配置 |
|------|----------|----------|
| macOS | 26.0 | M 系列芯片必需；16 GB 内存（可用 ≥ 6 GB）；可用磁盘 ≥ 24 GB |
| Windows | Windows >= 24H2 | 最低 20 GB 可用显存；建议固态硬盘；可用磁盘 ≥ 26 GB |
| iOS | iOS >= 26.0 | 需连接运行隙间最新版本的计算机，远程使用全部功能 |
| Android | Android >= 12.0 | 需连接运行隙间最新版本的计算机，远程使用全部功能 |

### 2.2 Python 环境

XiJian Core 要求 Python ≥ 3.11（推荐 3.12）。官方推荐使用 conda 环境 `xijianBase`；下文的一条龙脚本会自动创建并复用该环境。

```bash
# 方式 A（推荐）：使用 conda
conda create -n xijianBase python=3.12 -y
conda activate xijianBase

# 方式 B：使用 venv
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 升级基础工具
pip install -U pip setuptools wheel
```

### 2.3 系统级依赖（编译 llama.cpp / MLX 所需）
| 平台 | 安装命令 |
|------|----------|
| macOS | `xcode-select --install && brew install cmake rust` |
| Windows | `winget install Kitware.CMake Rustlang.Rust.GNU` + Visual Studio 2022 "C++ 桌面开发" 负载 |
| Linux (Debian/Ubuntu) | `apt update && apt install -y build-essential cmake rustc cargo` |
| Linux (Arch) | `pacman -S base-devel cmake rust` |

---

## 3. 安装 XiJian Core

### 3.1 一条龙脚本（推荐，构建/安装/运行一体化）

仓库提供跨平台一条龙脚本，自动完成「检测/创建 conda 环境 → 安装依赖 → (可选)编译 AI 后端 → (可选)测试 → (可选)启动」全流程，自由度高、所有步骤可单独开关。

| 平台 | 脚本 |
|------|------|
| macOS / Linux | `core/scripts/dev.sh` |
| Windows | `core/scripts/dev.ps1` |

```bash
# macOS / Linux —— 不传任何参数 → 进入交互式向导（推荐新手）
./core/scripts/dev.sh

# 显式进入交互式向导
./core/scripts/dev.sh -i

# 仅安装依赖（非交互，幂等）
./core/scripts/dev.sh --no-install

# 安装 + 跑测试 + 以 dev 模式启动在 18600 端口
./core/scripts/dev.sh --test --run --dev --port 18600

# 安装并编译 MLX 后端（macOS），使用本地 mlx-lm 源码路径
./core/scripts/dev.sh --with-mlx --mlx-path ~/code/mlx-lm

# 安装并编译 GGUF 后端
./core/scripts/dev.sh --with-gguf

# 指定 conda 环境名（默认 xijianBase）
./core/scripts/dev.sh --env myenv --run --dev
```

```powershell
# Windows PowerShell
.\core\scripts\dev.ps1                         # 无参 → 交互式向导
.\core\scripts\dev.ps1 -Interactive            # 显式交互式向导
.\core\scripts\dev.ps1 -NoInstall              # 仅安装依赖（非交互）
.\core\scripts\dev.ps1 -Test -Run -Dev -Port 18600
.\core\scripts\dev.ps1 -WithGguf -GgufPath D:\code\llama-cpp-python
```

> **交互式向导**：不传任何参数（或显式 `-i` / `-Interactive`）时，脚本会引导你逐步选择 conda 环境、是否安装依赖、是否编译 AI 后端（含本地源码路径）、是否跑测试、是否启动服务及端口/主机/日志级别/日志文件/配置文件/冒烟模式等，最终给出操作摘要并要求确认后才执行。适合不熟悉参数的新手或一次性配置场景。
>
> 任何未被脚本识别的参数（如 `--port` / `--dev` / `--log-level` / `--host`）会在 `--run` / `-Run` 时原样转发给 `python -m xijian_api`。

### 3.2 手动从源码安装
```bash
cd /path/to/XiJian/core

# 开发模式安装（含测试依赖）
pip install -e ".[test]"

# 仅运行时依赖
pip install -e .
```

### 3.3 验证安装
```bash
python -c "from xijian_api import create_app; print('OK')"
# 输出 OK 即成功
```

---

## 4. 配置说明

### 4.1 配置文件加载优先级
1. 环境变量 `XIJIAN_CONFIG` 指定的 TOML 文件
2. 当前工作目录下的 `config.toml`
3. 仓库根目录下的 `config.toml`（即 `core/config.toml`）

### 4.2 环境变量（全部可选）

XiJian Core **没有任何必须设置的环境变量**——所有配置都有内置默认值。
下列环境变量均为可选覆盖项，优先级低于命令行参数、高于 config.toml。

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `XIJIAN_API_PORT` | `18500` | 监听端口 (1-65535) |
| `XIJIAN_HOST` | `127.0.0.1` | 监听地址（默认仅本地环回；如需外部访问请显式设为 `0.0.0.0`） |
| `XIJIAN_DEV` | `false` | 开发模式：启用测试路由等开发能力 |
| `XIJIAN_DEV_TOKEN_FILE` | `false` | dev token 保留开关（`keep_token_file`）：为真时开发模式下不删除 token 文件 |
| `XIJIAN_LOG_LEVEL` | `INFO` | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `XIJIAN_LOG_FILE` | *(无)* | 日志文件路径；设置后日志同时写入文件与 stderr |
| `XIJIAN_CONFIG` | 自动搜索 | 自定义配置文件绝对路径 |
| `XIJIAN_OVERLOAD_MONITOR` | `true` | 过载防护监控开关（Windows 建议 `false`） |
| `XIJIAN_DATA_DIR` | `~/Library/Application Support/XiJian/Core` | 存储根整体覆盖（优先于 config.toml `[storage] base_dir`，测试与高级用户用它整体搬迁数据） |
| `XIJIAN_DB_PATH` | `$(XIJIAN_DATA_DIR)/xijian.db` | SQLite 数据库路径（优先级高于 `XIJIAN_DATA_DIR` 推导的默认位置） |

### 4.3 命令行参数（最高优先级）

`python -m xijian_api --help` 查看完整列表。常用参数：

| 参数 | 说明 |
|------|------|
| `--port <n>` | 监听端口（覆盖 `$XIJIAN_API_PORT` / config.toml） |
| `--host <h>` | 监听地址（覆盖 `$XIJIAN_HOST` / config.toml） |
| `--dev` / `--no-dev` | 强制开启/关闭开发模式（覆盖 `$XIJIAN_DEV`） |
| `--config <path>` | 指定配置文件路径（覆盖 `$XIJIAN_CONFIG`） |
| `--log-level <L>` | 日志级别（覆盖 `$XIJIAN_LOG_LEVEL`） |
| `--log-file <path>` | 日志文件路径（覆盖 `$XIJIAN_LOG_FILE`） |
| `--no-serve` | 完成初始化与自检后不启动 WSGI 服务（冒烟测试用） |
| `--version` | 打印版本信息并退出 |

### 4.4 config.toml 关键段落（生产环境建议修改）

```toml
[server]
host = "127.0.0.1"    # 仅监听本地环回，避免未授权外部访问
port = 18500          # 文档默认，实际以 XIJIAN_API_PORT 为准
dev = false           # 生产环境必须 false
keep_token_file = false

# token 路径由 runtime 统一管理，不在此配置；
# 实际写入 ~/Library/Application Support/XiJian/tmp/xijian-{pid}.token

[storage]
base_dir = "/var/lib/xijian"  # 数据根目录（模型权重、上传、快照、审计）

[backends.chat]
default = "mlx"          # macOS Apple Silicon 首选
fallbacks = ["gguf"]

[backends.embeddings]
default = "mlx"
fallbacks = ["gguf"]

# tts/stt/image/video 同理...

[overload]
monitor = true           # Windows 请设为 false
tier = "medium"          # strict / medium（MacBook Air 建议 strict）

[features]
protection_module = true
rate_limit = false       # 生产可开启
seed_default_data = false
dev_test_emit = false
```

> **Windows 部署特别注意**：将所有 `[backends.*].default` 改为 `"gguf"`，`[overload] monitor = false`。

---

## 5. 启动方式

### 5.1 零配置启动（最简）

无需任何环境变量即可启动，默认监听 `127.0.0.1:18500`：

```bash
python -m xijian_api
```

> 生产模式下若未预置 token 文件，服务会**自动降级为开发模式**并生成临时 token（同时写 WARNING 日志），以保证可用性。正式生产部署建议预置 token 文件或显式 `--no-dev`。

### 5.2 常用启动参数
```bash
# 开发模式 + 指定端口 + DEBUG 日志
python -m xijian_api --dev --port 18600 --log-level DEBUG

# 指定监听地址与日志文件
python -m xijian_api --host 127.0.0.1 --port 18500 --log-file /var/log/xijian/api.log

# 指定配置文件
python -m xijian_api --config /etc/xijian/config.toml

# 冒烟自检（初始化后不启动 WSGI，适合 CI）
python -m xijian_api --no-serve --dev

# 查看版本
python -m xijian_api --version
```

**启动成功标志**（启动横幅示例）：
```
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] ================================================================
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] XiJian Core API 启动
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] 监听地址      : 127.0.0.1:18600
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] 服务器驱动    : werkzeug (WebSocket 可用)
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] 开发模式      : True
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] 配置文件      : /path/to/config.toml
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] 存储根目录    : ~/Library/Application Support/XiJian/Core
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] 已注册模型    : 3 个
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] 日志级别      : DEBUG
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] ================================================================
[xijian-api] 2026-07-22 21:54:56 INFO  [xijian_api] werkzeug 服务启动: 127.0.0.1:18600 (WebSocket 可用)
```

### 5.3 开发/调试模式

`--dev`（或 `XIJIAN_DEV=1`）启用开发模式：
- Token 写入统一临时目录 `~/Library/Application Support/XiJian/tmp/xijian-{pid}.token` 并在日志/终端打印明文
- 启用 `/v1/xijian/_test/emit` 等测试路由
- 默认服务器驱动为 `werkzeug`（多线程，WebSocket 可用）；也可用 `--server waitress` 显式切换，但 waitress 不支持 WebSocket（/v1/ws 不可用）

### 5.4 启动韧性（自动错误修正）

启动流程对常见错误具备自动修正能力，**每次修正都会写日志**（不会因修正成功而静默）：

| 故障 | 自动修正行为 | 日志级别 |
|------|--------------|----------|
| 配置文件缺失/解析失败 | 回退到内置默认配置 | WARNING |
| 存储目录不存在 | 自动创建 base/files/models/snapshots/audit | DEBUG（失败 WARNING） |
| 生产模式缺 token 文件 | 降级为开发模式、自动生成 token | WARNING（失败+恢复各一条） |
| `waitress` 未安装 | 回退到 `werkzeug`（多线程，WebSocket 可用） | WARNING |
| 端口越界 (1-65535) | 回退到默认端口 18500 | ERROR |
| 端口被占用 | 报告占用进程并自动向上探测空闲端口（最多 100 个），用新端口启动；实际端口写入 `tmp/xijian-<pid>.port`（`--port-strict` 可恢复占用即报错退出） | WARNING |
| 路由模块导入失败 | 跳过该模块、其余正常注册 | WARNING |
| 未捕获的启动异常 | 记录 CRITICAL 堆栈并退出码 1 | CRITICAL |

### 5.5 后台守护进程部署（systemd 示例）

**/etc/systemd/system/xijian-api.service**
```ini
[Unit]
Description=XiJian Core API Server
After=network.target

[Service]
Type=simple
User=xijian
Group=xijian
WorkingDirectory=/opt/xijian/core
Environment=XIJIAN_API_PORT=18500
Environment=XIJIAN_LOG_LEVEL=INFO
Environment=XIJIAN_CONFIG=/etc/xijian/config.toml
ExecStart=/opt/xijian/core/.venv/bin/python -m xijian_api
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

启用并启动：
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xijian-api
sudo journalctl -u xijian-api -f  # 查看日志
```

### 5.6 Docker 部署（参考）

**Dockerfile**
```dockerfile
FROM python:3.12-slim

# 系统依赖（llama.cpp 运行时需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libstdc++6 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY core/ ./core/
RUN pip install --no-cache-dir -e ./core

ENV XIJIAN_API_PORT=18500 \
    XIJIAN_LOG_LEVEL=INFO \
    XIJIAN_CONFIG=/app/config.toml

EXPOSE 18500
CMD ["python", "-m", "xijian_api"]
```

**构建与运行**
```bash
docker build -t xijian-core:latest -f core/Dockerfile .
docker run -d --name xijian-api \
  -p 18500:18500 \
  -v /host/data:/var/lib/xijian \
  -v /host/config.toml:/app/config.toml \
  xijian-core:latest
```

---

## 6. 验证服务可用性

### 6.1 健康检查
```bash
curl -s http://localhost:18500/healthz
# XIJIAN_OK_v1（text/plain；前缀 `XIJIAN_OK_<version>`，见 docs/Dev.md 握手约定）
```

### 6.2 根路径与能力发现
```bash
curl -s http://localhost:18500/
# {"name":"xijian-api","server_version":"1.2.0a0","api_version":"1.0.0","status":"ok"}

curl -s http://localhost:18500/v1
# {"api_version":"1.0.0","server_version":"1.2.0a0","capabilities":[...]}
```

### 6.3 认证 Token 获取
```bash
# token 固定写入统一临时目录（开发/打包一致）：
#   ~/Library/Application Support/XiJian/tmp/xijian-<pid>.token
TOKEN=$(cat ~/Library/Application\ Support/XiJian/tmp/xijian-*.token)

# 开发模式：日志中直接打印
# [xijian-api] dev token: a3b5f1ef0343204f467091d5e275b74a4f438f050f33053e963c5af638a112de
TOKEN="a3b5f1ef0343204f467091d5e275b74a4f438f050f33053e963c5af638a112de"
```

### 6.4 简单聊天测试
```bash
curl -s -X POST http://localhost:18500/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stub-model",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

> 出厂仅注册 mock 模型（`stub-model`，测试/开发用）。正式使用前需先按 `docs/AIBackend.md` 配置模型后端（mlx / gguf / openai）并注册模型。

### 6.5 外部访问（需显式开启）

默认仅监听 `127.0.0.1`，外部无法直接访问。如确需局域网访问，请显式配置：

```bash
# 通过环境变量显式开放外部访问（请确保网络环境可信）
XIJIAN_HOST=0.0.0.0 python -m xijian_api

# 或在 config.toml 中设置 [server] host = "0.0.0.0"
# 随后在局域网另一台机器上执行
curl -s http://<SERVER_IP>:18500/healthz
# 应返回 XIJIAN_OK_v1
```

> **安全提示**：开放外部访问前请确认已配置 Token 鉴权、防火墙规则及网络环境可信。

---

## 7. 常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 日志提示端口被占用 | 端口已被其他进程占用 | 已自动向上探测空闲端口（最多 100 个）并启动；实际端口见 `tmp/xijian-<pid>.port`。需要固定端口时可加 `--port-strict`（占用即报错退出），或释放被占用的端口 |
| 日志出现「已降级为开发模式启动」 | 生产模式未预置 token 文件 | 已自动修正；正式部署请预置 token 文件或检查 `XIJIAN_DEV` |
| 日志出现「waitress 不支持 WebSocket」 | 显式使用了 `--server waitress` | 如需 WebSocket（/v1/ws）请改回默认 werkzeug（去掉 `--server` 或 `driver = "auto"`）；纯 HTTP 场景可继续用 waitress |
| 日志出现「waitress 未安装」 | 未安装 waitress | `pip install waitress`（已自动回退 werkzeug，WebSocket 可用） |
| 日志出现「配置加载失败」 | config.toml 缺失或语法错误 | 已自动回退默认配置；检查 TOML 语法 |
| 外部无法访问 | 默认仅监听 `127.0.0.1`（安全基线） | 如确需外部访问，显式设置 `XIJIAN_HOST=0.0.0.0` 并放行端口与防火墙 |
| `ImportError: No module named 'mlx'` | macOS 非 Apple Silicon / Windows/Linux | 配置 `backends.*.default = "gguf"` |
| Token 认证失败 401 | Token 过期/错误 | 重读 token_file 或重启服务获取新 token |
| 模型加载 OOM | 显存/内存不足 | 减小 `gguf_n_ctx`、量化等级、或换更小模型 |

---

## 8. 运维操作

### 8.1 优雅重启
```bash
# systemd
sudo systemctl reload xijian-api  # 仅重载配置
sudo systemctl restart xijian-api # 完全重启

# 手动发送 SIGTERM 触发优雅关闭
kill -TERM <pid>
```

### 8.2 备份关键数据
```bash
# 备份目录（含模型、上传、快照、审计、token）
tar -czf xijian-backup-$(date +%F).tar.gz /var/lib/xijian
```

### 8.3 日志轮转（logrotate 示例）
```conf
/var/log/xijian/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 640 xijian xijian
}
```

---

## 9. 相关文档

- `docs/维护教程.md` — 维护者总览（存储布局 / 版本号 / 文档同步规则）
- `docs/BuildGuide.md` — Core 构建与打包指南（PyInstaller）
- `docs/macapp.md` — macOS 客户端（内嵌 Core 的运行方式）
- `docs/Dev. Function List功能清单v2.md` — 功能规格说明
- `docs/notes.md` — 开发日志
- `core/scripts/dev.sh` / `core/scripts/dev.ps1` — 构建/安装/运行一条龙脚本
- `core/xijian_api/app.py` — CLI 参数与启动韧性实现
- `core/xijian_api/utils/log.py` — 日志级别/文件控制
- `core/xijian_api/config.py` — 配置项完整定义
- `core/config.toml` — 默认配置模板

---

## 10. 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-13 | 初版：涵盖环境准备、配置、启动、验证、排查、运维 |
| v1.1 | 2026-07-22 | 启动流程优化：零配置默认值 + CLI 参数；新增一条龙脚本 dev.sh/dev.ps1；日志级别/文件控制；启动错误自动修正（配置/存储/token/后端/端口）；更新排查表 |