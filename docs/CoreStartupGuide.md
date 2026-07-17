# XiJian Core 启动指南

> 文档版本：v1.0  
> 适用版本：XiJian Core API v0.1.0+  
> 维护者：隙间开发组

---

## 1. 概览

XiJian Core 是一个基于 Flask 的 OpenAI 兼容 API 服务端，提供：
- OpenAI 兼容的聊天补全、Embeddings、音频、图像、视频生成接口
- 角色/世界/记忆/状态等隙间特有业务接口
- WebSocket 实时事件流
- 模型管理、过载防护、安全审查等运维能力

**默认监听地址**：127.0.0.1（全网卡，**允许外部访问**）  
**必须通过环境变量指定端口**：`XIJIAN_API_PORT`

---

## 2. 环境准备

### 2.1 系统要求
| 平台 | 最低版本 | 推荐配置 [TODO: 补充详细的配置信息] |
|------|----------|----------|
| macOS | 13.0 (Ventura) | **等待填写** |
| Windows | Windows >= 24H2 | **等待填写** |
| iOS | iOS >= 26.0 | 良好的网络连接 |
| Android | Android >= 12.0 | 良好的网络连接 |

### 2.2 Python 环境

```bash
# 推荐 Python 3.11 或 3.12
python3 --version  # >= 3.11

# 创建虚拟环境
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

### 3.1 从源码安装（开发/生产通用）
```bash
cd /path/to/XiJian/core

# 可选：开发模式安装（含测试/代码检查依赖）
pip install -e .[dev]

# 仅运行时依赖
pip install -e .
```

### 3.2 验证安装
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

### 4.2 必须设置的环境变量
| 变量名 | 示例 | 说明 |
|--------|------|------|
| `XIJIAN_API_PORT` | `18500` | **必须**，监听端口 (1-65535) |

### 4.3 常用可选环境变量
| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `XIJIAN_DEV` | `false` | 开发模式：保留 token 文件、启用测试路由 |
| `XIJIAN_DEV_TOKEN_FILE` | `false` | 开发模式下不删除 token 文件 |
| `XIJIAN_LOG_LEVEL` | `INFO` | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `XIJIAN_OVERLOAD_MONITOR` | `true` | 过载防护监控开关（Windows 建议 `false`） |
| `XIJIAN_CONFIG` | 自动搜索 | 自定义配置文件绝对路径 |

### 4.4 config.toml 关键段落（生产环境建议修改）

```toml
[server]
host = "0.0.0.0"      # 已默认开放外部访问
port = 18500          # 文档默认，实际以 XIJIAN_API_PORT 为准
dev = false           # 生产环境必须 false
keep_token_file = false

[auth]
token_file = "/var/lib/xijian/xijian-{pid}.token"  # 建议持久化路径

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

> **Windows 部署特别注意**：将所有 `[backends.*].default` 改为 `"gguf"`，`[overload] monitor = false`。详见 `docs/CoreWinSupport.md`。

---

## 5. 启动方式

### 5.1 生产环境标准启动（推荐）
```bash
# 设置端口（必须）
export XIJIAN_API_PORT=18500

# 可选：生产日志级别
export XIJIAN_LOG_LEVEL=INFO

# 启动（前台运行，配合 systemd/supervisor 使用）
python -m xijian_api
```

**启动成功标志**：
```
[xijian-api] starting xijian-api on 0.0.0.0:18500
[xijian-api] serving via waitress on 0.0.0.0:18500
```
> 终端会打印监听地址和端口，确认外部可访问。

### 5.2 开发/调试模式启动
```bash
export XIJIAN_API_PORT=18500
export XIJIAN_DEV=1
export XIJIAN_LOG_LEVEL=DEBUG

python -m xijian_api
```

**开发模式特征**：
- Token 写入 `/tmp/xijian-{pid}.token` 并在日志打印明文
- 启用 `/v1/xijian/_test/emit` 等测试路由
- 如未安装 `waitress` 自动回退到 Flask 单线程开发服务器

### 5.3 后台守护进程部署（systemd 示例）

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

### 5.4 Docker 部署（参考）

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
# {"status":"ok"}
```

### 6.2 根路径与能力发现
```bash
curl -s http://localhost:18500/
# {"name":"xijian-api","server_version":"0.1.0","api_version":"1.0.0","status":"ok"}

curl -s http://localhost:18500/v1
# {"api_version":"1.0.0","server_version":"0.1.0","capabilities":[...]}
```

### 6.3 认证 Token 获取
```bash
# 生产环境：从配置的 token_file 读取
TOKEN=$(cat /var/lib/xijian/xijian-*.token)

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
    "model": "qwen2.5-7b-mlx-4bit",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

### 6.5 外部访问测试（验证 0.0.0.0 监听生效）
```bash
# 在局域网另一台机器上执行
curl -s http://<SERVER_IP>:18500/healthz
# 应返回 {"status":"ok"}
```

---

## 7. 常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 启动报 `XIJIAN_API_PORT is required` | 未设置环境变量 | `export XIJIAN_API_PORT=18500` |
| 启动报 `XIJIAN_API_PORT must be an integer` | 端口非数字 | 设置纯数字端口 |
| 启动报 `XIJIAN_API_PORT out of range` | 端口不在 1-65535 | 修正端口范围 |
| 外部无法访问 | 防火墙拦截 | 放行端口：`ufw allow 18500` / `firewall-cmd --add-port=18500/tcp` / Windows 防火墙入站规则 |
| `ImportError: No module named 'mlx'` | macOS 非 Apple Silicon / Windows/Linux | 配置 `backends.*.default = "gguf"` |
| `waitress not installed` 警告 | 生产环境未装 waitress | `pip install waitress` |
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

- `docs/CoreWinSupport.md` — Windows 平台不支持功能清单
- `docs/Dev. Function List功能清单v2.md` — 功能规格说明
- `core/xijian_api/config.py` — 配置项完整定义
- `core/config.toml` — 默认配置模板

---

## 10. 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-13 | 初版：涵盖环境准备、配置、启动、验证、排查、运维 |