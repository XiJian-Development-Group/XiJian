# BuildGuide.md — 构建与打包指南

> 本文档描述如何将 XiJian Core API 编译为独立可执行文件并打包为 zip 分发包，
> 供 UI 程序解压运行。适用于 macOS（Apple Silicon）和 Windows（x64）平台。

---

## 1. 概述

XiJian Core API 使用 [PyInstaller](https://pyinstaller.org/) 将 Flask 应用
及其所有依赖（含 Python 解释器）打包为一个独立可执行文件。打包后的产物
不依赖目标机器上预装的 Python 环境，UI 程序只需解压 zip 并运行可执行文件
即可启动 API 服务。

### 打包模式 vs 开发模式

| 特性 | 开发模式 | 打包模式 (frozen) |
|------|----------|-------------------|
| Python 解释器 | 系统/conda 提供 | 内嵌于 `_internal/` |
| 依赖安装 | `pip install` | 已打包进可执行文件 |
| 配置文件 | `core/config.toml` | `<exe_dir>/config.toml` |
| 日志文件 | `~/Library/Application Support/XiJian/Core/logs/xijian-api.log` | 同左（统一 CORE_ROOT/logs） |
| Token 文件 | `~/Library/Application Support/XiJian/tmp/xijian-<pid>.token` | 同左（统一临时目录 tmp/） |
| 端口文件 | `~/Library/Application Support/XiJian/tmp/xijian-<pid>.port` | 同左（统一临时目录 tmp/） |
| 存储目录 | `~/Library/Application Support/XiJian/Core` | 同左（统一 CORE_ROOT，`XIJIAN_DATA_DIR` 可整体覆盖） |
| 外部 AI 依赖 | conda/pip 安装 | `<exe_dir>/external_libs/` |

运行时环境检测由 [runtime.py](file:///Users/mofan/Documents/MyProjects/XiJian/core/xijian_api/runtime.py) 负责，通过 `sys.frozen` 判断当前模式。

---

## 2. 构建产物结构

```
xijian-api/                    # 解压后的根目录
├── xijian-api                 # 可执行文件（macOS/Linux）
│                               # 或 xijian-api.exe（Windows）
├── _internal/                  # PyInstaller 运行时
│   ├── Python 解释器
│   ├── 依赖库（Flask/werkzeug/waitress/...）
│   └── xijian_api 包代码
├── config.toml                # 默认配置（用户可编辑）
├── README.txt                 # 使用说明
├── logs/                      # 日志目录（自动创建）
├── tmp/                       # 统一临时目录：token / port / discovery（自动创建）
├── data/                      # 存储根目录（自动创建）
└── external_libs/             # 外部 AI 依赖（可选）
```

---

## 3. 构建前准备

### 3.1 环境要求

- **macOS**: Apple Silicon (arm64)，macOS 26.0+
- **Windows**: Windows 10/11 x64
- **conda**: Anaconda/Miniconda/Miniforge
- **Python**: 3.12（由 conda 环境提供）
- **磁盘空间**: 至少 2GB（含 AI 依赖时约 3GB）

### 3.2 初始化环境

首次使用时，dev 脚本会自动创建 conda 环境 `xijianBase`：

```bash
# macOS / Linux
./core/scripts/dev.sh

# Windows
.\core\scripts\dev.ps1
```

按交互式向导完成环境配置后，即可进行构建。

### 3.3 安装 AI 后端依赖（可选）

如果需要在打包产物中包含本地 AI 后端（MLX / llama.cpp），需在构建前安装：

```bash
# macOS: 安装 MLX 后端
./core/scripts/dev.sh --with-mlx --no-install

# 任意平台: 安装 GGUF 后端
./core/scripts/dev.sh --with-gguf --no-install
```

未安装的 AI 后端不会被打包，但 OpenAI 兼容远程后端始终可用。

---

## 4. 构建命令

### 4.1 通过 dev 脚本（推荐）

```bash
# macOS / Linux
./core/scripts/dev.sh --build --zip --clean

# Windows
.\core\scripts\dev.ps1 -Build -Zip -Clean
```

参数说明：

| 参数 | 说明 |
|------|------|
| `--build` / `-Build` | 执行打包 |
| `--zip` / `-Zip` | 打包后生成 zip 分发包 |
| `--clean` / `-Clean` | 打包前清理 `build/` 和 `dist/` 目录 |
| `--no-install` / `-NoInstall` | 跳过依赖安装（已安装时使用） |

### 4.2 交互式向导

不传任何参数运行 dev 脚本，进入交互式向导：

```bash
./core/scripts/dev.sh    # 或 .\core\scripts\dev.ps1
```

向导会引导选择操作（启动/打包/两者都做）、配置参数、确认后执行。

### 4.3 直接调用 PyInstaller

```bash
cd core
pyinstaller scripts/xijian-api.spec --noconfirm --clean
```

注意：直接调用 PyInstaller 不会自动拷贝 `config.toml` 和 `README.txt`，
也不会生成 zip 分发包。建议使用 dev 脚本。

---

## 5. 构建产物验证

### 5.1 冒烟测试

```bash
cd dist/xijian-api
./xijian-api --no-serve --dev    # macOS/Linux
xijian-api.exe --no-serve --dev  # Windows
```

看到 `--no-serve 已指定，初始化完成但不启动 WSGI 服务` 即表示打包成功。

### 5.2 启动服务测试

```bash
./xijian-api --dev --port 18600
```

然后在新终端测试 API：

```bash
# 健康检查
curl http://localhost:18600/healthz

# 模型列表（需替换为日志中的 token）
curl -H "Authorization: Bearer <token>" http://localhost:18600/v1/models
```

---

## 6. UI 程序集成

> 本节描述 UI 程序（Electron / Tauri / 原生应用）集成 Core 的通用流程。
> macOS 客户端（macapp）的落地实现——Core 复制/合并 → 启动 → 实际端口 → token 的
> 完整 6 步见 [docs/macapp.md §3 运行方式](macapp.md#3-运行方式)。

### 6.1 工作流程

UI 程序（如 Electron / Tauri / 原生应用）的工作流程：

```
1. 解压 xijian-core-<platform>-<arch>.zip 到 <app_data>/xijian-core/
2. （可选）解压 AI 扩展包到 <app_data>/xijian-core/external_libs/
3. 启动子进程:
   <app_data>/xijian-core/xijian-api --port 18500 --config <path>/config.toml
4. 等待服务就绪:
   - 监听 stdout/stderr 出现 "werkzeug 服务启动"（或 "waitress 服务启动"，若显式 `--server waitress`）
   - 或轮询 GET /healthz 直到返回 200
5. 使用 API
6. 退出时发送 SIGTERM (Unix) / Ctrl+C (Windows) 或 kill 进程
```

### 6.2 就绪检测

服务启动后，日志会输出 `werkzeug 服务启动: 127.0.0.1:<port> (WebSocket 可用)`。
默认服务器驱动为 werkzeug（支持 /v1/ws WebSocket）；如显式指定 `--server waitress`
（或 config.toml `[server].driver = "waitress"`），则输出 `waitress 服务启动` 并带
WARNING「waitress 不支持 WebSocket，/v1/ws 将不可用」。
UI 程序可通过以下方式检测就绪：
```javascript
// 轮询健康检查端点
async function waitForReady(port, maxRetries = 30) {
    for (let i = 0; i < maxRetries; i++) {
        try {
            const resp = await fetch(`http://localhost:${port}/healthz`);
            if (resp.ok) return true;
        } catch (e) {}
        await new Promise(r => setTimeout(r, 500));
    }
    return false;
}
```

### 6.3 Token 获取

服务自动生成 Bearer token 并写入统一临时目录
`~/Library/Application Support/XiJian/tmp/xijian-<pid>.token`（开发与打包模式一致）。
日志会输出 `dev token: <token>`。UI 程序可：

1. 从日志输出中解析 token
2. 或读取 `tmp/` 目录下的 `.token` 文件

macapp 的具体读取时机见 [macapp.md §3](macapp.md#3-运行方式) 第 6 步。

### 6.4 跨平台路径

| 平台 | 可执行文件路径 |
|------|----------------|
| macOS | `<dir>/xijian-api` |
| Linux | `<dir>/xijian-api` |
| Windows | `<dir>\xijian-api.exe` |

---

## 7. 外部 AI 依赖（external_libs）

### 7.1 用途

打包后的核心可执行文件可能不包含 MLX / llama_cpp 等大型二进制依赖
（取决于构建时是否安装）。用户可按需下载 AI 扩展包，解压到
`external_libs/` 目录，启动时会自动加入 `sys.path`。

### 7.2 制作 AI 扩展包

在目标平台上，安装所需的 AI 依赖后，将 site-packages 中的相关包
复制到 `external_libs/` 目录：

```bash
# macOS: MLX 扩展包
mkdir -p external_libs
cp -r ~/miniconda3/envs/xijianBase/lib/python3.12/site-packages/mlx* external_libs/
cp -r ~/miniconda3/envs/xijianBase/lib/python3.12/site-packages/llama_cpp external_libs/
```

然后压缩为 zip 分发。

### 7.3 加载机制

[runtime.py](file:///Users/mofan/Documents/MyProjects/XiJian/core/xijian_api/runtime.py) 的 `setup_external_libs()` 函数在启动时检查
`external_libs/` 目录是否存在，若存在则加入 `sys.path` 前部，
使其中安装的包能被正常 import。

---

## 8. 配置文件策略

打包后的配置文件优先级（从高到低）：

1. `--config` 命令行参数指定的路径
2. `$XIJIAN_CONFIG` 环境变量
3. `<exe_dir>/config.toml`（打包模式默认）
4. `<cwd>/config.toml`
5. 内置默认配置

用户可直接编辑 `<exe_dir>/config.toml` 修改配置，重启服务生效。

---

## 9. 体积优化

### 9.1 排除不必要的包

[scripts/xijian-api.spec](file:///Users/mofan/Documents/MyProjects/XiJian/core/scripts/xijian-api.spec) 的 `excludes` 列表已排除：

- pytest / pytest_cov（测试框架）
- IPython / jupyter / notebook（IDE）
- tkinter（GUI 工具包）
- 未使用的数据库驱动

### 9.2 按需打包 AI 依赖

如果不需要本地 AI 后端，构建前不安装 MLX / llama_cpp，
打包产物将仅包含核心 + OpenAI 远程后端，体积约 80-100MB。

### 9.3 UPX 压缩

spec 文件已启用 UPX 压缩（`upx=True`）。如果系统未安装 UPX，
PyInstaller 会跳过压缩。安装 UPX 可进一步减小体积：

```bash
# macOS
brew install upx

# Windows
choco install upx
```

---

## 10. 故障排查

### 10.1 打包失败

**症状**: `ModuleNotFoundError: No module named 'xxx'`

**原因**: PyInstaller 静态分析未发现该模块。

**解决**: 在 spec 文件的 `hiddenimports` 列表中手动添加该模块名。

### 10.2 启动失败

**症状**: `FileNotFoundError: config.toml`

**原因**: 打包脚本未正确拷贝配置文件。

**解决**: 确保使用 `--build` 选项通过 dev 脚本构建，而非直接调用
PyInstaller。dev 脚本会自动拷贝 `config.toml`。

### 10.3 AI 后端不可用

**症状**: `BackendError: mlx_lm is not installed`

**原因**: 构建时未安装 MLX 依赖，或未通过 `external_libs/` 提供。

**解决**:
1. 重新构建时加 `--with-mlx` 参数
2. 或将 AI 扩展包解压到 `external_libs/` 目录
3. 或使用 OpenAI 兼容远程后端（无需本地依赖）

### 10.4 端口冲突

**症状**: `端口 18500 已被占用`

**解决**: 使用 `--port` 指定其他端口，或释放被占用的端口。

---

## 11. 文件清单

| 文件 | 说明 |
|------|------|
| [scripts/xijian-api.spec](file:///Users/mofan/Documents/MyProjects/XiJian/core/scripts/xijian-api.spec) | PyInstaller spec 文件 |
| [scripts/dist-readme.txt](file:///Users/mofan/Documents/MyProjects/XiJian/core/scripts/dist-readme.txt) | 打包产物的 README 模板 |
| [scripts/dev.sh](file:///Users/mofan/Documents/MyProjects/XiJian/core/scripts/dev.sh) | macOS/Linux 开发+构建脚本 |
| [scripts/dev.ps1](file:///Users/mofan/Documents/MyProjects/XiJian/core/scripts/dev.ps1) | Windows 开发+构建脚本 |
| [xijian_api/runtime.py](file:///Users/mofan/Documents/MyProjects/XiJian/core/xijian_api/runtime.py) | 运行时环境检测 |
| [xijian_api/launch.py](file:///Users/mofan/Documents/MyProjects/XiJian/core/xijian_api/launch.py) | 打包后入口点 |
