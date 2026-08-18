# Dev.md — 隙间 开发者技术文档

> 本文档面向参与「隙间」项目代码与资源开发的贡献者。
> 阅读对象：需要修改代码、提交 PR、参与架构设计的开发者。
> 用户文档请见 [README.md](../README.md)（docs/ 下的文档索引见 [docs/README.md](README.md)）。

---

## 1. 项目定位与目标平台

### 1.1 一句话

「隙间」是一款本地优先的二次元 AI 聊天 / 社交应用，围绕角色沉浸感、长期记忆、Apple 生态深度集成、跨平台可用来设计。

### 1.2 目标平台

| 平台       | 状态     | UI 实现          | AI 推理                | 备注                                                                                |
| ---------- | -------- | ---------------- | ---------------------- | ----------------------------------------------------------------------------------- |
| macOS      | 主目标   | Swift / SwiftUI  | MLX（mlx-swift）       | 最低 macOS 26.0（推荐 macOS 26 Tahoe）；推荐内存 64–128 GB；可用磁盘 ≥ 32 GB     |
| iOS / iPad | 伴侣端   | —                | —                      | 无原生客户端；需连接运行隙间的 macOS/Windows 电脑，仅作远程控制/通知面板                 |
| Windows    | 副目标   | Python + Pywebview | GGUF（llama.cpp / Ollama） | 最低 Windows 10 22H2 / 11（建议 24H2 及以上）；最低 20GB 可用显存（参见 README.md）；可用磁盘 ≥ 64 GB                                  |
| Linux      | 暂不支持 | —                | —                      | 无原生客户端计划，后端 Core API 理论可跑，但无 UI 前端                                                 |

### 1.3 关键约束

- **AI 完全本地运行**：不依赖云端 AI，所有推理在用户设备上完成。
- **MLX vs GGUF 分平台锁定**：
  - macOS 一律使用 **MLX** 模型（性能 / 能效最优）。
  - Windows / Linux 一律使用 **GGUF** 模型（llama.cpp / Ollama 生态成熟）。
  - 跨平台通用业务逻辑只调用 **AI 抽象接口**（§4.1），不得在业务代码里直接 import mlx / llama-cpp。
- **完全开源、无付费、无广告**。
- **用户数据默认本地化**，所有外发操作必须经用户显式授权。

---

## 2. 总体架构

### 2.1 一张图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          UI 层（平台相关）                            │
│  ┌──────────────────────────┐    ┌──────────────────────────────┐   │
│  │  macOS 端：Swift +       │    │  Win/Linux 端：              │   │
│  │  SwiftUI + AppKit 桥接   │    │  Python + Pywebview          │   │
│  └────────────┬─────────────┘    └──────────────┬───────────────┘   │
│               │                                  │                   │
│               │    HTTP / WebSocket / SSE        │                   │
│               └──────────────┬───────────────────┘                   │
│                              │                                       │
│                              ▼                                       │
│               ┌──────────────────────────────┐                       │
│               │  本地 API 网关（跨平台共享）    │                       │
│               │  Python Flask + Flask-SocketIO│                       │
│               │  端口握手 / 健康检查 / 鉴权    │                       │
│               └──────────────┬───────────────┘                       │
│                              │                                       │
│                              ▼                                       │
│   ┌──────────────────────────────────────────────────────────────┐   │
│   │                  业务服务层（纯 Python，跨平台共享）           │   │
│   │  角色服务 · 互动服务 · 世界服务 · 记忆服务 · 保护模块 ·        │   │
│   │  资源加载 · 资产管线 · 通知调度                                │   │
│   └──────────────────────────┬───────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│               ┌──────────────────────────────┐                       │
│               │     AI 抽象层（Backend ABC）  │                       │
│               └──────────────┬───────────────┘                       │
│                              │                                       │
│              ┌───────────────┼────────────────┐                      │
│              ▼                                ▼                      │
│  ┌────────────────────┐               ┌──────────────────────────┐   │
│  │  MLX Backend       │               │  GGUF Backend            │   │
│  │  mlx-swift / Python │              │  llama.cpp / Ollama      │   │
│  │  仅 macOS          │               │  仅 Win / Linux           │   │
│  └────────────────────┘               └──────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 三层职责

| 层           | 语言                  | 跨平台？ | 职责                                                                 |
| ------------ | --------------------- | -------- | -------------------------------------------------------------------- |
| UI 层        | Swift / Python+HTML   | 否       | 渲染界面、采集用户输入、调用本地 API、处理平台特性（灵动岛、桌宠等） |
| 本地 API 网关 | Python（Flask）       | 是       | 进程托管、端口握手、跨进程协议、鉴权、日志、扩展点                   |
| 业务服务层   | Python                | 是       | 角色 / 互动 / 世界 / 记忆 / 保护模块等核心逻辑                       |
| AI 抽象层    | Python ABC + 实现     | 是（接口） | 统一 Chat / Embedding / TTS / Voice Clone 接口                       |
| AI 后端      | 平台相关              | 否       | macOS → MLX；Win/Linux → GGUF（llama.cpp / Ollama）                 |

### 2.3 进程模型

每个平台都是 **「主 UI 进程 + 内嵌 Python API 子进程」**：

1. **主 UI 进程**（Swift 应用 / Python Pywebview 应用）启动时把 Python API 程序释放到本地目录（首次或版本变更时）。
2. **主 UI 进程** 作为子进程管理器 fork / 启动 Python API 程序。
3. **Python API 程序** 监听 `127.0.0.1` 的随机空闲端口（避免端口冲突）。
4. **主 UI 进程** 轮询 / 订阅本地端口，扫描到候选端口后发送握手请求。
5. **Python API** 在 `/healthz` 返回约定的特定文本（例如 `XIJIAN_OK_<version>`），握手成功。
6. **主 UI 进程** 与 Python API 建立正式连接（HTTP + WebSocket），进入正常运行。

> **为什么要这么设计？**
>
> - 业务核心逻辑只写一次（Python），降低跨平台开发难度。
> - UI 可以根据平台特性自由发挥（Swift 直接调 MLX 也行，但业务逻辑不依赖）。
> - 端口握手 + 本地环回（`127.0.0.1`）保证不会出现网络暴露，外部无法访问。
> - Python 进程崩溃时主 UI 能立刻检测到并自动重启。

### 2.4 为什么是 Flask 而不是 FastAPI / 其他

- Flask 体积小、依赖少，打进包不会显著膨胀。
- Flask-SocketIO / SSE 生态成熟，能同时支持请求-响应、流式响应、推送。
- 团队已有 Flask 经验。
- 性能上 Flask + werkzeug（默认服务器驱动，支持 /v1/ws WebSocket）/ waitress（可显式 `--server waitress` 切换，但不支持 WebSocket）完全够本地单用户使用。

如未来需要切换到 FastAPI，迁移成本低（接口形态不变即可）。

---

## 3. 仓库目录结构

实际结构如下（构建产物与缓存目录如 `build/`、`dist/`、`*.egg-info/`、`__pycache__/` 未列出）：

```
XiJian/
├── README.md                       # 用户向项目介绍（入口，指向 docs/）
├── LICENSE
├── Config/
│   └── Config.json                 # 项目元数据 + 版本号唯一事实源（人工编辑）
├── core/                           # Core API（Flask 服务，运行时主进程）
│   ├── config.toml                 # 服务配置（存储路径、模型、后端等）
│   ├── pyproject.toml              # Python 包元数据（版本号由 sync-versions.py 同步）
│   ├── scripts/
│   │   ├── dev.sh / dev.ps1        # 开发/构建脚本
│   │   ├── sync-versions.py        # 版本同步脚本（Config.json → 各目标）
│   │   ├── xijian-api.spec         # PyInstaller 打包描述（独立分发包用）
│   │   └── eval_safety.py / dist-readme.txt
│   ├── tests/                      # pytest 测试（xijianBase 环境跑）
│   └── xijian_api/                 # Core 源码包
│       ├── app.py / launch.py      # Flask 入口 / 打包后入口点
│       ├── runtime.py              # 运行时环境检测（frozen vs 开发模式）
│       ├── config.py / auth.py / ports.py / discovery.py / handshake.py
│       ├── ai/                     # AI 抽象层（base.py / registry.py / model_registry.py / backends/）
│       ├── mcp/                    # MCP 服务端（protocol / registry / tools / resources / prompts）
│       ├── routes/                 # HTTP 路由（xijian_*.py + OpenAI 兼容路由）
│       ├── stubs/                  # 业务逻辑实现（state / memory / chat / npcs / packs …）
│       └── utils/                  # 通用工具（log / ids / time / params）
├── devkit/                         # 开发者工具（Pywebview 独立应用）
│   ├── main.py / app.py / api.py   # Pywebview 入口与 DevKit 本地 API
│   ├── character_editor.py / world_editor.py / memory_editor.py /
│   │   plot_editor.py / motion_editor.py / model_viewer.py / dialog_editor.py …
│   ├── ai/                         # DevKit 侧 AI 封装（base.py / registry.py / backends/）
│   ├── ui/                         # 前端资源（index.html / devkit.css / devkit.js / vendor/）
│   ├── tests/                      # pytest 测试
│   ├── pyproject.toml / requirements.txt / xijian-devkit.spec / build-devkit.sh
│   └── version.py                  # 运行时读 Config.json 的版本（脚本同步）
├── macapp/                         # macOS 桌面客户端（SwiftUI，内嵌 Core 子进程）
│   ├── Sources/                    # Swift 源码（App / Models / Services / ViewModels / Views / Theme）
│   ├── Resources/                  # Assets.xcassets、Localizable.xcstrings、Core/（内嵌 Core 产物）
│   ├── Tests/                      # 单元测试（XiJianKit framework，无宿主）
│   ├── project.yml                 # XcodeGen 工程定义（PyInstaller spec 统一在 core/scripts/）
│   ├── build-core.sh / build-macapp.sh
│   └── Entitlements.entitlements
├── website/                        # 纯静态落地页（index.html + assets/）
├── devkit_data/                    # 仓库内空目录（plot 运行时回退工作目录）
├── docs/                           # 全部文档（索引见 docs/README.md；行为变更必须同步文档）
├── build.sh                        # core venv + 测试 + wheel/sdist 构建
└── project.py                      # 项目管理器入口（占位）
```

文档索引见 [docs/README.md](README.md)；macapp 的结构细节见 [docs/macapp.md](macapp.md) §1。

---

## 4. 核心模块接口

### 4.1 AI 抽象层（`core/ai/base.py`）

**这是整个跨平台架构的关键契约。** 所有业务代码只能依赖这个接口，不能直接 import 具体 backend。

```python
# core/ai/base.py —— 简化示意
from abc import ABC, abstractmethod
from typing import Iterator, Sequence

class ChatMessage(dict):
    """统一消息格式：{"role": "user"|"assistant"|"system", "content": str}"""

class ChatBackend(ABC):
    @abstractmethod
    def load(self, model_id: str, **kwargs) -> None: ...

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
        stream: bool = False,
    ) -> str | Iterator[str]: ...

    @abstractmethod
    def unload(self) -> None: ...

class EmbeddingBackend(ABC):
    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

class TTSBackend(ABC):
    @abstractmethod
    def synth(self, text: str, voice: str, **kwargs) -> bytes: ...

class VoiceCloneBackend(ABC):
    @abstractmethod
    def clone(self, reference_audio: bytes, text: str, **kwargs) -> bytes: ...
```

**Backend 选择逻辑**（`core/ai/registry.py`）：

```python
def get_backend() -> ChatBackend:
    if sys.platform == "darwin":
        from .backends.mlx import MLXChatBackend
        return MLXChatBackend()
    elif sys.platform in ("win32", "linux"):
        from .backends.gguf import GGUFChatBackend
        return GGUFChatBackend()
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
```

**强制约束**：

- `core/xijian_api/stubs/` 下的任何模块 **禁止** 直接 `from .ai.backends.mlx import ...` 或 `from .ai.backends.gguf import ...`，必须走 `get_backend()`。
- 新增 backend（如未来支持远程 / 云端）必须实现 `ChatBackend`，并通过 `BACKEND_<NAME>` 环境变量选择。

### 4.2 本地 API 网关（`core/xijian_api/`）

#### 4.2.1 启动流程

```python
# core/xijian_api/app.py —— 简化示意
import os
import socket
from flask import Flask, jsonify

EXPECTED_HANDSHAKE = "XIJIAN_OK_v1"

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def create_app() -> Flask:
    app = Flask(__name__)
    port = int(os.environ["XIJIAN_API_PORT"])  # 由主 UI 进程预分配

    @app.get("/healthz")
    def healthz():
        return EXPECTED_HANDSHAKE, 200, {"Content-Type": "text/plain"}

    # ... 注册业务路由
    return app
```

#### 4.2.2 主 UI 侧握手（伪代码）

**macOS (Swift)**：

```swift
// 伪代码
let port = try ProcessLauncher.launchPythonScript(args: ["-m", "xijian.api"])
// 端口通过环境变量或临时文件传递给 UI 进程

for _ in 0..<30 {
    try? await Task.sleep(nanoseconds: 500_000_000)
    let url = URL(string: "http://127.0.0.1:\(port)/healthz")!
    if let (data, _) = try? await URLSession.shared.data(from: url),
       let text = String(data: data, encoding: .utf8),
       text.hasPrefix("XIJIAN_OK_") {
        // 握手成功，建立正式 session
        apiClient.connect(baseURL: url)
        return
    }
}
throw .apiTimeout
```

**Win/Linux (Python) — 创作者工具 / DevKit**：

```python
# devkit/discovery.py —— Core API 发现示意（非主客户端）
import os, time, urllib.request
from typing import Optional

def wait_for_handshake(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                body = r.read().decode("utf-8")
                if body.startswith("XIJIAN_OK_"):
                    return True
        except Exception:
            time.sleep(0.3)
    return False
```

> ⚠️ **注意**：Win/Linux 目前**没有原生主客户端**（Python + Pywebview 主客户端在计划中，当前未完成）。上述代码为 DevKit（创作者工具）发现本地 Core API 的示例。主客户端仅 macOS (SwiftUI) 已实现。

#### 4.2.3 API 协议规范

详见 `docs/api.md`，以下是要点：

- 全部基于 **HTTP + WebSocket**（`ws://127.0.0.1:<port>/ws`），**不暴露到 0.0.0.0**。
- 鉴权：进程启动时生成随机 token，写入 `127.0.0.1` 才能读取的临时文件；主 UI 进程读取后所有请求带上 `Authorization: Bearer <token>`。
- 流式响应优先使用 **SSE**（`text/event-stream`）；双向推送用 WebSocket。
- 业务路径示例：
  - `POST /v1/chat/completions` —— 单轮 / 多轮对话（OpenAI 兼容）
  - `POST /v1/chat/completions`（`stream=true`）—— 流式对话（SSE / NDJSON）
  - `POST /v1/embeddings` —— 向量化
  - `POST /v1/audio/speech` —— 语音合成（TTS）
  - `POST /v1/xijian/memory/entries` 及 `GET / PATCH / DELETE /v1/xijian/memory/entries/{entry_id}` —— 长期记忆管理
  - `POST /v1/xijian/worlds/{world_id}/transition`、`GET / PATCH /v1/xijian/worlds/{world_id}/state` —— 世界系统读写
  - `POST /v1/xijian/interactions/{interaction_id}/trigger` —— 互动触发
  - `/v1/xijian/safety/*`（扫描 / 审计）与 `/v1/xijian/backups/*`（快照与回滚）—— 保护模块
  - `GET  /v1/models` —— 当前可用模型列表

  完整端点清单见 `docs/api.md`（核心端点约 141 个；机器可读全量见 `docs/openapi.yaml`，271 个 path）。

### 4.3 业务服务层（`core/xijian_api/stubs/`）

服务层只与 AI 抽象层对话，不关心具体 backend：

```python
# core/xijian_api/stubs/characters.py —— 简化示意（角色服务）
from xijian_api.ai.base import get_backend, ChatMessage
from xijian_api.stubs.safety import guard_input, guard_output  # 示意名，实际接口见 safety.py

class CharacterService:
    def reply(self, character_id: str, user_input: str, history: list[ChatMessage]) -> str:
        backend = get_backend()
        system = self._load_system_prompt(character_id)
        long_term = self._recall_memory(character_id, user_input)

        # 所有输入都先经过保护模块
        safe_input = guard_input(user_input, context=system)

        msgs = [system, *long_term, *history, {"role": "user", "content": safe_input}]
        raw = backend.chat(msgs)
        return guard_output(raw, expected_role=character_id)
```

#### 4.3.1 角色服务（`stubs/characters.py`）

- 加载角色人设、模型引用、互动配置
- 拼装 Prompt 时强制走保护模块
- 维护角色级状态（心情、好感度等）

#### 4.3.2 互动服务（`stubs/scene_interactions.py`）

- 互动配置加载（`nsfwLevel` 字段）
- 角色对互动可「同意 / 拒绝」
- NSFW 内容默认隐藏，需用户在设置中开启

#### 4.3.3 模拟世界（`stubs/npcs.py` 等）

- 系统维度：经济、健康、饮食、体力、心智
- 突发事件：基于状态值 + 概率表
- 场景 / 交通：状态变更 + 场景切换

#### 4.3.4 长期记忆（`stubs/memory.py`）

- 短期：会话窗口（可配置 token 上限）
- 长期：摘要 + 向量检索，每次互动后异步写入
- 用户可手动增删改记忆条目
- 跨平台统一使用 FAISS 或 HNSW（Python 实现），向量 backend 不分平台

#### 4.3.5 保护模块（`stubs/safety.py` / `stubs/snapshots.py`，**核心**）

项目最关键的安全模块，**不允许以「方便调试」为由绕过**。

- **提示词注入防御**：
  - 输入侧：对用户输入、系统检索到的外部内容、文件附件内容做标记化与隔离
  - 输出侧：检测模型输出是否出现 OOC、指令泄露
- **OOC 检测**：规则 + 模型自检两层
- **数据版本化**：
  - AI 相关数据（记忆、人设、配置）每次变更生成版本快照（时间戳 + 哈希）
  - 用户可查看历史版本并回滚
- **关闭保护系统**：
  - 必须 **双重确认**（UI 弹窗 + 输入确认短语）
  - 关闭后变更仍记录，但不再做防御检测
  - 关闭状态本身写入审计日志

### 4.4 UI 层

#### 4.4.1 macOS（Swift / SwiftUI）

- **进程管理**：使用 `Process` 启动 Python API 子进程，通过 `Pipe` 捕获端口写入 stdout / 临时文件
- **网络**：标准 `URLSession` + WebSocket 客户端
- **渲染**：VRM 1.0 (GLTF) + Metal 渲染管线（统一采用 VRM，详见功能清单 v2.1 决议）
- **平台特性**：
  - TouchBar：`NSTouchBar`
  - 自建「灵动岛」：`NSScreen` 顶部区域 + 自绘，**注意与其他应用冲突**
  - 桌宠 / 壁纸：透明背景窗口 + 屏幕录制 API（需用户授权）⚠️ **开发中**
  - 屏幕操控：CGEvent 模拟键鼠 ⚠️ **开发中**
  - **应急快捷键**：默认 `⌃⌥⌘Q`，按下立即中断 AI 操作 ⚠️ **开发中**

#### 4.4.2 Win / Linux（Python + Pywebview）— 计划中，当前未完成

- **进程管理**：`subprocess.Popen` 启动 Python API 子进程
- **Pywebview**：使用系统 WebView（Win 上 Edge WebView2 / Linux 上 GTK WebKit）
- **前端**：HTML / CSS / JS
    - 渲染：VRM 1.0 (GLTF) + three.js（统一采用 VRM）
- **平台特性**：
  - 桌宠：Pywebview 的 frameless 模式 + 透明背景 ⚠️ **开发中**
  - 屏幕观察 / 操控：mss（截屏）+ pyautogui / xdotool（操控）⚠️ **开发中**
  - 应急快捷键：`pynput` 注册全局热键 ⚠️ **开发中**
- **打包**：PyInstaller → `.exe` / AppImage / `.deb`

---

## 5. 跨平台开发约束

### 5.1 强约束（CI 会检查）

1. **业务代码不得直接依赖平台特定库**。`rg -l "from .ai.backends" core/xijian_api/stubs/` 应该为空。
2. **平台特性封装在 UI 层**。`core/` 下不得出现 `import Cocoa` / `import win32gui` / `import Xlib`。
3. **所有路径使用 `pathlib.Path`**，不得硬编码 `/` 或 `\`。
4. **所有用户可见字符串走 i18n**，禁止硬编码（详见 §6）。

### 5.2 弱约束（建议但非强制）

- 业务服务函数尽量无副作用，便于在 Win/Linux 上直接跑测试。
- 涉及文件 I/O 时使用 `core/utils/fs.py` 提供的辅助函数，统一处理路径与编码。

---

## 6. 国际化

- 业务层（`core/`）国际化设施尚未建立（`core/i18n/` 词表未建），字符串管理现状见 `docs/维护教程.md` §6。
- 目标语言：zh-Hans / en / ja（与 macapp String Catalog 一致）。
- UI 层（macOS SwiftUI / 前端 JS）走各自的标准 i18n 方案
- PR 中若新增用户可见字符串，必须同时提供中英文

---

## 7. 资源文件提交规范

> 代码与资源走两条不同的流程，请勿混用。

### 7.1 资源文件清单

| 资源                          | 审核要求         |
| ----------------------------- | ---------------- |
| VRM 1.0 模型 (GLTF)           | 质量审核         |
| 基本声音数据（用于声音克隆）  | 质量审核         |
| 互动配置文件（JSON）+ 动作信息 | NSFW 分级审核     |
| 场景配置文件（JSON）          | 无审核           |
| 场景图片包（建议 7Z 固实）    | 无审核           |
| 详细人设文档                  | 质量审核         |
| 详细世界观文档 + 世界配置 JSON | 有审核           |

### 7.2 资源提交流程

1. 资源附上 **完整可读的简体中文描述**
2. 邮件发送至 [support@mail.skyc8266.uk](mailto:support@mail.skyc8266.uk)
3. 等待审核、打包、处理
4. 管理员将资源合并到主分支
5. **不要**通过 PR 提交资源

### 7.3 NSFW 内容规范

- 所有 NSFW 内容必须在配置中明确标注 `nsfwLevel`
- 主程序默认隐藏所有 `soft` 及以上级别内容
- 互动响应中涉及 NSFW 的文本/动作走与图片相同的分级

---

## 8. 代码贡献流程

### 8.1 准备工作

1. 注册 [GitHub](https://github.com) 账号
2. 克隆仓库
3. **macOS**：安装 Xcode 16+、Swift 5.9+、Python 3.11+
4. **Windows**：安装 Python 3.11+、Visual Studio Build Tools、Edge WebView2 Runtime
5. **Linux**：安装 Python 3.11+、PyGObject / webkit2gtk

### 8.2 提交流程

- **代码变更**：通过 Pull Request
- **资源变更**：见 §7
- **重大改动**：开 Issue 讨论 → 维护者同意 → 建分支 → 开发

### 8.3 PR 要求

- 一个 PR 只做一件事
- 必须包含：
  - 改动说明（动机 + 设计要点）
  - 测试用例（行为变更）
  - 截图 / 录屏（UI 变更）
  - 中英文双语更新（用户可见改动）
- 标题格式：`[模块名] 简要描述`，例如 `[Protection] 增加对工具调用结果的注入防御`
- 关联相关 Issue：`Fixes #123` / `Refs #456`

### 8.4 提交信息规范

推荐 Conventional Commits：

```
feat(character): 增加互动拒绝动画
fix(protection): 修复关闭保护后未记录版本的问题
docs(dev): 补充 MLX 模型选型说明
refactor(memory): 将向量检索抽离为独立服务
feat(api): 新增 /v1/world/transition 路由
test(world): 覆盖经济系统边界值
```

### 8.5 Code Review

- 通常至少 1 名维护者通过
- 涉及保护模块、记忆系统、AI backend 选择逻辑的改动需 **2 名维护者** 通过
- 涉及 NSFW 相关逻辑的改动需 **全员** 审核
- 涉及 `core/xijian_api/handshake.py`、`core/ai/registry.py` 等跨平台关键路径的改动需特别关注

### 8.6 中国大陆地区开发者

若无法访问 GitHub：

1. 邮件联系管理员 [support@mail.skyc8266.uk](mailto:support@mail.skyc8266.uk)
2. 或在开发组群内联系
3. 管理员可授予 contributor 权限或代为提交

---

## 9. 安全与隐私基线

- **本地优先原则**：默认所有数据本地处理，外发必须经用户授权
- **本地 API 只监听 127.0.0.1**：绝不允许监听 `0.0.0.0`，避免外部访问
- **进程间鉴权**：API 启动时生成随机 token，存放在仅本机可读的临时文件中
- **权限最小化**：

  | 权限           | 用途                         |
  | -------------- | ---------------------------- |
  | 相册 / 文件    | 用户发送附件                 |
  | 摄像头 / 麦克风 | 实时通话                     |
  | 辅助功能       | 部分机型的灵动岛             |
  | 通知           | 角色主动发起通话 / 回复消息  |
  | 屏幕录制       | 桌宠 / 壁纸模式              |

- **桌宠屏幕观察 / 操控**：
  - 首次启用必须显示同意页（含免责声明）
  - 必须有可配置的应急快捷键，macOS 默认 `⌃⌥⌘.`，Win/Linux 默认可在设置中修改
  - 所有用户授权记录写入保护模块审计日志

---

## 10. 性能与质量基线

### 10.1 启动

- 冷启动到主界面：≤ 3 s（macOS M2 / 32 GB；Win/Linux 中端机型）
- Python API 进程冷启动：≤ 1.5 s
- 端口握手超时：≤ 15 s，超时后 UI 报错并提示排查

### 10.2 推理

- 单轮对话 TTFT（首 token 时间）：
  - macOS（7B MLX, 4-bit）≤ 1.5 s
  - Win/Linux（7B GGUF Q4_K_M）≤ 2.0 s
- 长时间运行内存增长：≤ 200 MB / 小时（不含模型本身）

### 10.3 渲染

- VRM 渲染帧率：≥ 60 FPS
- 桌宠模式空闲 CPU 占用：≤ 5%

### 10.4 测试

- 所有 PR 必须通过单元测试
- 保护模块必须达到 90% 行覆盖率
- AI 抽象层必须有 mock backend 测试
- 跨平台握手流程必须有集成测试（用 mock UI 进程跑完整握手）

---

## 11. 调试与排错建议

- **API 握手失败**：
  - macOS：`lsof -iTCP -sTCP:LISTEN -P -n | grep LISTEN` 看端口
  - Win：`netstat -ano | findstr LISTENING`
  - Linux：`ss -tlnp`
  - 检查防火墙、Python 虚拟环境、`XIJIAN_API_PORT` 环境变量
- **MLX 推理异常**：先确认模型格式 → 再确认 mlx-swift 版本 → 复现最小 demo
- **GGUF 推理异常**：先确认 `llama-cpp-python` / `Ollama` 版本 → 单独跑 CLI 验证 → 再走 backend
- **保护模块误判**：开启详细日志，issue 附完整 prompt + 输出
- **Pywebview 渲染异常**：先确认系统 WebView 版本（Win → WebView2、Linux → webkit2gtk）
- **桌宠无响应**：检查辅助功能 / 屏幕录制权限
- **跨平台差异**：在 `core/xijian_api/utils/log.py`（或 `ids.py` / `params.py` / `time.py`）中加入 `git rev-parse HEAD` 输出到日志，方便定位

---

## 12. 路线图（开发视角）

> 里程碑进度以 [docs/notes.md](notes.md)（开发日志）与
> [docs/Dev. Function List功能清单v2.md](Dev.%20Function%20List%E5%8A%9F%E8%83%BD%E6%B8%85%E5%8D%95v2.md) 为准。

- **M0 — 架构定型**：API 网关 + AI 抽象层 + 保护模块骨架 + 端口握手
- **M1 — 单角色可用**：macOS 实时对话 + VRM 渲染 + 基本记忆 ⚠️ VRM 渲染开发中
- **M2 — 模拟世界**：经济 / 健康 / 互动 / 场景切换
- **M3 — 生态特性**：TouchBar / 灵动岛 / 壁纸 / 桌宠 ⚠️ 开发中
- **M4 — 主动消息与通知** ⚠️ 开发中
- **M5 — Win / Linux Pywebview 端**：复用 core，复用 AI 抽象层换 GGUF backend ⚠️ 计划中，当前未完成
- **M6 — iOS / Android**（待评估，作为连接电脑的伴侣端）

## 13. 行为准则

- 尊重所有贡献者，不接受任何形式的骚扰
- 涉及 NSFW 内容的工作仅在合规场景下进行
- 不要把未通过审核的资源合并进主分支
- 不要绕过保护模块（即使是「临时调试」）
- 不要在 `core/` 下写平台特定代码，所有跨平台差异收敛到 UI 层
- **不要滥用开发者工具**：开发者工具仅供提交**合法的创作内容**使用，不得用于任何其他行为；违规内容将直接清除相关数据，并按情节处理提交者

---

## 14. 联系方式

- 邮箱：[support@mail.skyc8266.uk](mailto:support@mail.skyc8266.uk)
- QQ：2500693887

---

## 15. DevKit 预览/测试环境（C0 循环）

### 15.1 架构

DevKit 是独立 Pywebview 进程，Core API 是 Flask 服务器。预览/测试通过**共享文件系统**桥接：

```
┌─────────────────────┐      ┌─────────────────────────────┐
│  DevKit (Pywebview)  │      │  Core API (Flask)           │
│                     │      │                             │
│  保存数据到工作目录    │      │  GET /v1/xijian/devkit/*   │
│  ↓                   │      │  ↑                         │
│  ~/Library/.../DevKit│◄─────│  扫描 & 加载到 runtime     │
│  ├── characters/     │      │                             │
│  ├── worlds/         │      │  用户通过主程序对话测试      │
│  ├── memories/       │      │                             │
│  └── plots/          │      │                             │
└─────────────────────┘      └─────────────────────────────┘
```

**零侵入**——Core 直接读取 DevKit 的保存目录，无需修改 DevKit 代码。

### 15.2 端点清单

所有端点位于 `/v1/xijian/devkit/`：

| Method | Path | 用途 |
|--------|------|------|
| `GET` | `/status` | DevKit 目录可用性 + 统计 |
| `GET` | `/characters` | 列出 DevKit 中保存的角色 |
| `GET` | `/characters/<id>` | 角色详细预览 |
| `POST` | `/characters/<id>/load` | 载入 core runtime |
| `DELETE` 或 `POST /unload` | `/characters/<id>` | 从 runtime 卸载 |
| `GET` | `/worlds` | 列出 DevKit 中保存的世界 |
| `GET` | `/worlds/<id>` | 世界详细预览 |
| `POST` | `/worlds/<id>/load` | 载入 core runtime |
| `DELETE` 或 `POST /unload` | `/worlds/<id>` | 从 runtime 卸载 |
| `GET` | `/loaded` | 当前已加载的 devkit 条目 |
| `POST` | `/reload?kind=character|world` | 重新扫描并刷新 runtime |

### 15.3 预览/测试流程（C0 循环）

```
DevKit 编辑 → 保存
    ↓
切到主程序 → 开发者工具 → 预览
    ↓
看到角色/世界列表
    ↓
点击「加载」→ 出现在对话列表
    ↓
聊天测试 → 满意？
    ├── 是 → DevKit 点「提交」
    └── 否 → DevKit 修改 → 保存 → 主程序「重新加载」→ 再测
```

### 15.4 实现模块

- `core/xijian_api/stubs/devkit.py` — 目录扫描、数据解析、runtime 加载/卸载
- `core/xijian_api/routes/xijian_devkit.py` — HTTP 端点

### 15.5 DevKit 路径

默认路径：`~/Library/Application Support/XiJian/DevKit/`

可通过环境变量 `XIJIAN_DEVKIT_DIR` 覆盖。

---

## 16. 附录：常见问题（FAQ）

**Q：业务逻辑写一次还是两次？**
A：业务逻辑（角色、互动、世界、记忆、保护）一律写在 `core/`，跨平台共享。AI 推理由 backend 适配。

**Q：能不能只用 MLX，不分平台？**
A：不能。MLX 仅支持 Apple Silicon；Win/Linux 走 GGUF（llama.cpp / Ollama）。

**Q：能不能加上云端推理？**
A：不在路线图内。本项目核心理念之一就是本地优先。

**Q：主 UI 进程如何获取 API 进程的端口？**
A：推荐方式 —— Python 启动时把端口写入统一临时目录（`~/Library/Application Support/XiJian/tmp/xijian-<pid>.port`），主 UI 启动后读取该文件。stdout 传端口在 Windows 上不可靠。

**Q：为什么不用 gRPC / 直接 Swift 调 MLX？**
A：跨平台 + 单代码库 + 快速迭代是当前优先级。gRPC 增加打包体积与复杂度；Swift 直调 MLX 会让 Win/Linux 端重复实现业务逻辑。

**Q：NSFW 内容怎么提交？**
A：所有 NSFW 内容必须在资源配置中明确标注，主程序默认隐藏。提交流程同普通资源，但审核要求更严。

**Q：Pywebview 在不同 Linux 桌面环境上是否一致？**
A：不一致。打包时需明确目标环境（GNOME / KDE），并在文档中标注已知差异。

---

## 17. DevKit 自动更新器安全声明

> **⚠️ 必读：DevKit 自动更新器安全声明**
>
> **故意禁用 TLS 证书验证**：由于中国大陆用户必须通过代理/加速器访问 GitHub，代理的 TLS 拦截会导致证书验证失败。为保证更新检查/下载基本可用，更新器在所有网络请求中禁用 TLS 验证（`ssl.CERT_NONE`）。
>
> **无代码签名 / 无校验和验证**：本项目为完全开源免费软件，开发组不持有、也不打算获取代码签名证书。因此无法提供代码签名验证（Apple notarization、Windows Authenticode、Linux GPG 等）。考虑到分发渠道不可控、GitHub Releases 自身可能被篡改、无签名基础设施下 SHA 校验和只能提供虚假安全感，**故意不实现**下载后的校验和/签名验证。
>
> **风险自担**：更新检查/下载流程**不提供传输层机密性/完整性保证**。任何能劫持用户到 GitHub 连接的攻击者（包括但不限于代理运营商、ISP、DNS 劫持者）均可注入任意更新包，导致以当前用户权限执行恶意代码。**使用自动更新功能即表示您知晓并接受上述风险**。如需更高安全性，建议用户**手动**从 GitHub Releases 页面下载并验证，或使用操作系统自带的包管理器（如 Homebrew、Scoop、Flatpak 等）分发。

---

_本文档随项目演进持续更新；如有疑问或想补充的内容，请通过 Issue 或邮件反馈。_