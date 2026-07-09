# Dev.md — 隙间 开发者技术文档

> 本文档面向参与「隙间」项目代码与资源开发的贡献者。
> 阅读对象：需要修改代码、提交 PR、参与架构设计的开发者。
> 用户文档请见 [项目文档.md](已弃置/“隙间”项目文档.md)。

---

## 1. 项目定位与目标平台

### 1.1 一句话

「隙间」是一款本地优先的二次元 AI 聊天 / 社交应用，强调角色沉浸感、长期记忆、Apple 生态深度集成、跨平台可用。

### 1.2 目标平台

| 平台       | 状态     | UI 实现          | AI 推理                | 备注                                                                                |
| ---------- | -------- | ---------------- | ---------------------- | ----------------------------------------------------------------------------------- |
| macOS      | 主目标   | Swift / SwiftUI  | MLX（mlx-swift）       | 推荐 macOS 26 Tahoe，最低 macOS 13 Ventura；推荐内存 64–128 GB；可用磁盘 ≥ 32 GB     |
| iOS / iPad | 暂不支持 | —                | —                      | 目前项目无 iOS 上架计划                                                              |
| Windows    | 副目标   | Python + Pywebview | GGUF（llama.cpp / Ollama） | 最低 Windows 11；推荐显存 128 GB；可用磁盘 ≥ 64 GB                                  |
| Linux      | 副目标   | Python + Pywebview | GGUF（llama.cpp / Ollama） | 同 Windows                                                                          |

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
- 性能上 Flask + gunicorn / waitress 完全够本地单用户使用。

如未来需要切换到 FastAPI，迁移成本低（接口形态不变即可）。

---

## 3. 仓库目录结构

> **这是建议结构**，仓库正式建立后以 `README.md` 为准。

```
XiJian/
├── README.md                       # 用户向介绍
├── “隙间”项目文档.md                  # 用户文档
├── Dev.md                          # 本文件
├── LICENSE
├── AGENTS.md                       # 给 AI 协作 Agent 的项目约定
│
├── core/                           # 跨平台共享核心（Python 业务 + AI）
│   ├── api/                        # Flask API 网关
│   │   ├── app.py                  # Flask 入口
│   │   ├── handshake.py            # 端口握手逻辑
│   │   ├── routes/                 # 各业务路由
│   │   ├── sockets/                # WebSocket 事件
│   │   └── auth.py                 # 进程间鉴权 token
│   ├── services/                   # 业务服务层
│   │   ├── character/              # 角色服务
│   │   ├── interaction/            # 互动服务（NSFW 分级）
│   │   ├── world/                  # 模拟世界（经济/健康/饮食/体力/心智）
│   │   ├── memory/                 # 长期记忆 + 向量检索
│   │   ├── protection/             # 保护模块（注入防御、OOC、数据版本回滚）
│   │   └── notifier/               # 主动消息调度
│   ├── ai/                         # AI 抽象层 + 实现
│   │   ├── base.py                 # AI Backend ABC
│   │   ├── backends/
│   │   │   ├── mlx/                # macOS MLX 后端
│   │   │   └── gguf/               # Win/Linux llama.cpp / Ollama 后端
│   │   ├── prompt.py               # Prompt 模板 + 注入防御入口
│   │   └── registry.py             # 根据平台自动选择 backend
│   ├── resources/                  # 资源加载器（角色、世界、场景）
│   ├── models/                     # ORM / 数据模型
│   ├── store/                      # SQLite / FAISS 封装
│   ├── i18n/                       # zh_CN, en_US
│   ├── utils/                      # 通用工具、日志、错误处理
│   ├── tests/
│   └── pyproject.toml
│
├── ui/                             # UI 层
│   ├── macos/                      # Swift 应用
│   │   ├── Package.swift
│   │   ├── XiJian.xcodeproj
│   │   ├── Sources/
│   │   │   ├── App/
│   │   │   ├── Core/               # 网络客户端、进程管理
│   │   │   ├── Live2D/             # Cubism SDK 渲染
│   │   │   ├── DynamicIsland/      # 灵动岛自建
│   │   │   ├── TouchBar/
│   │   │   ├── DesktopPet/         # 桌宠 + 屏幕观察 + 操控
│   │   │   ├── Wallpaper/          # 壁纸模式
│   │   │   └── UI/                 # SwiftUI 视图
│   │   └── Tests/
│   │
│   └── desktop/                    # Win/Linux 端（Python + Pywebview）
│       ├── pyproject.toml
│       ├── xijian_desktop/
│       │   ├── main.py             # Pywebview 入口
│       │   ├── process_manager.py  # 启动/监控 core API 进程
│       │   ├── port_scanner.py     # 端口握手扫描
│       │   ├── web/                # 前端资源（HTML/CSS/JS）
│       │   │   ├── index.html
│       │   │   ├── live2d/         # Live2D for Web（Cubism Web SDK）
│       │   │   ├── three/          # 3D 模型（three.js）
│       │   │   └── assets/
│       │   └── tests/
│       └── packaging/              # PyInstaller 配置（产出 .exe / AppImage）
│
├── resources/                      # 角色、世界、场景资源（不进 PR，邮件提交）
│   ├── characters/
│   └── worlds/
│
├── docs/
│   ├── architecture.md             # 架构决策记录
│   ├── api.md                      # API 协议规范
│   ├── ai-backend.md               # AI backend 实现指南
│   └── adr/                        # 架构决策记录
│
└── scripts/
    ├── bootstrap.sh                # 本地一键启动
    └── package.sh                  # 打安装包
```

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

- `core/services/` 下的任何模块 **禁止** 直接 `from .ai.backends.mlx import ...` 或 `from .ai.backends.gguf import ...`，必须走 `get_backend()`。
- 新增 backend（如未来支持远程 / 云端）必须实现 `ChatBackend`，并通过 `BACKEND_<NAME>` 环境变量选择。

### 4.2 本地 API 网关（`core/api/`）

#### 4.2.1 启动流程

```python
# core/api/app.py —— 简化示意
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

**Win/Linux (Python)**：

```python
# ui/desktop/xijian_desktop/port_scanner.py —— 简化示意
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

#### 4.2.3 API 协议规范

详见 `docs/api.md`，以下是要点：

- 全部基于 **HTTP + WebSocket**（`ws://127.0.0.1:<port>/ws`），**不暴露到 0.0.0.0**。
- 鉴权：进程启动时生成随机 token，写入 `127.0.0.1` 才能读取的临时文件；主 UI 进程读取后所有请求带上 `Authorization: Bearer <token>`。
- 流式响应优先使用 **SSE**（`text/event-stream`）；双向推送用 WebSocket。
- 业务路径示例：
  - `POST /v1/chat` —— 单轮 / 多轮对话
  - `POST /v1/chat/stream` —— 流式对话（SSE）
  - `POST /v1/embeddings` —— 向量化
  - `POST /v1/tts` —— 语音合成
  - `POST /v1/memory/add` / `/list` / `/delete` / `/update` —— 长期记忆管理
  - `POST /v1/world/state` / `/transition` —— 世界系统读写
  - `POST /v1/interaction/trigger` —— 互动触发
  - `POST /v1/protection/rollback` —— 保护模块数据回滚
  - `GET  /v1/models` —— 当前可用模型列表

### 4.3 业务服务层（`core/services/`）

服务层只与 AI 抽象层对话，不关心具体 backend：

```python
# core/services/character/service.py —— 简化示意
from core.ai.base import get_backend, ChatMessage
from core.services.protection import guard_input, guard_output

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

#### 4.3.1 角色服务（`character/`）

- 加载角色人设、Live2D 模型引用、互动配置
- 拼装 Prompt 时强制走保护模块
- 维护角色级状态（心情、好感度等）

#### 4.3.2 互动服务（`interaction/`）

- 互动 JSON 配置加载（`nsfwLevel` 字段）
- 角色对互动可「同意 / 拒绝」
- NSFW 内容默认隐藏，需用户在设置中开启

#### 4.3.3 模拟世界（`world/`）

- 系统维度：经济、健康、饮食、体力、心智
- 突发事件：基于状态值 + 概率表
- 场景 / 交通：状态变更 + 场景切换

#### 4.3.4 长期记忆（`memory/`）

- 短期：会话窗口（可配置 token 上限）
- 长期：摘要 + 向量检索，每次互动后异步写入
- 用户可手动增删改记忆条目
- 跨平台统一使用 FAISS 或 HNSW（Python 实现），向量 backend 不分平台

#### 4.3.5 保护模块（`protection/`，**核心**）

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
- **Live2D**：Cubism SDK for Native + Metal 渲染管线
- **平台特性**：
  - TouchBar：`NSTouchBar`
  - 自建「灵动岛」：`NSScreen` 顶部区域 + 自绘，**注意与其他应用冲突**
  - 桌宠 / 壁纸：透明背景窗口 + 屏幕录制 API（需用户授权）
  - 屏幕操控：CGEvent 模拟键鼠
  - **应急快捷键**：默认 `⌃⌥⌘.`，按下立即中断 AI 操作

#### 4.4.2 Win / Linux（Python + Pywebview）

- **进程管理**：`subprocess.Popen` 启动 Python API 子进程
- **Pywebview**：使用系统 WebView（Win 上 Edge WebView2 / Linux 上 GTK WebKit）
- **前端**：HTML / CSS / JS
  - Live2D：Cubism Web SDK
  - 3D：three.js（备用）
- **平台特性**：
  - 桌宠：Pywebview 的 frameless 模式 + 透明背景
  - 屏幕观察 / 操控：mss（截屏）+ pyautogui / xdotool（操控）
  - 应急快捷键：`pynput` 注册全局热键
- **打包**：PyInstaller → `.exe` / AppImage / `.deb`

---

## 5. 跨平台开发约束

### 5.1 强约束（CI 会检查）

1. **业务代码不得直接依赖平台特定库**。`rg -l "from .ai.backends" core/services/` 应该为空。
2. **平台特性封装在 UI 层**。`core/` 下不得出现 `import Cocoa` / `import win32gui` / `import Xlib`。
3. **所有路径使用 `pathlib.Path`**，不得硬编码 `/` 或 `\`。
4. **所有用户可见字符串走 i18n**，禁止硬编码（详见 §6）。

### 5.2 弱约束（建议但非强制）

- 业务服务函数尽量无副作用，便于在 Win/Linux 上直接跑测试。
- 涉及文件 I/O 时使用 `core/utils/fs.py` 提供的辅助函数，统一处理路径与编码。

---

## 6. 国际化

- 必须支持：`zh_CN`、`en_US`
- 业务层（`core/`）通过 `core/i18n/` 提供的 `t("key")` 函数获取字符串，**禁止硬编码中文 / 英文**
- UI 层（macOS SwiftUI / 前端 JS）走各自的标准 i18n 方案
- PR 中若新增字符串，必须同时提供中英文

---

## 7. 资源文件提交规范

> 代码与资源走两条不同的流程，请勿混用。

### 7.1 资源文件清单

| 资源                          | 审核要求         |
| ----------------------------- | ---------------- |
| Live2D 模型 或 3D 模型        | 质量审核         |
| 基本声音数据（用于声音克隆）  | 质量审核         |
| 互动配置文件（JSON）+ 动作信息 | NSFW 分级审核     |
| 场景配置文件（JSON）          | 无审核           |
| 场景图片包（建议 7Z 固实）    | 无审核           |
| 详细人设文档                  | 质量审核         |
| 详细世界观文档 + 世界配置 JSON | 有审核           |

### 7.2 资源提交流程

1. 资源附上 **完整可读的简体中文描述**
2. 邮件发送至 [panmofan@icloud.com](mailto:panmofan@icloud.com)
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
- 涉及 `core/api/handshake.py`、`core/ai/registry.py` 等跨平台关键路径的改动需特别关注

### 8.6 中国大陆地区开发者

若无法访问 GitHub：

1. 邮件联系管理员 [panmofan@icloud.com](mailto:panmofan@icloud.com)
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

- Live2D 渲染帧率：≥ 60 FPS
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
- **跨平台差异**：在 `core/utils/platform.py` 中加入 `git rev-parse HEAD` 输出到日志，方便定位

---

## 12. 路线图（开发视角）

> 以仓库 `docs/roadmap.md` 为准。

- **M0 — 架构定型**：API 网关 + AI 抽象层 + 保护模块骨架 + 端口握手
- **M1 — 单角色可用**：macOS 实时对话 + Live2D + 基本记忆
- **M2 — 模拟世界**：经济 / 健康 / 互动 / 场景切换
- **M3 — 生态特性**：TouchBar / 灵动岛 / 壁纸 / 桌宠
- **M4 — 主动消息与通知**
- **M5 — Win / Linux Pywebview 端**：复用 core，复用 AI 抽象层换 GGUF backend
- **M6 — iOS / Android**（待评估）

---

## 13. 行为准则

- 尊重所有贡献者，不接受任何形式的骚扰
- 涉及 NSFW 内容的工作仅在合规场景下进行
- 不要把未通过审核的资源合并进主分支
- 不要绕过保护模块（即使是「临时调试」）
- 不要在 `core/` 下写平台特定代码，所有跨平台差异收敛到 UI 层

---

## 14. 联系方式

- 邮箱：[panmofan@icloud.com](mailto:panmofan@icloud.com)
- QQ：2500693887

---

## 15. 附录：常见问题（FAQ）

**Q：业务逻辑写一次还是两次？**
A：业务逻辑（角色、互动、世界、记忆、保护）一律写在 `core/`，跨平台共享。AI 推理由 backend 适配。

**Q：能不能只用 MLX，不分平台？**
A：不能。MLX 仅支持 Apple Silicon；Win/Linux 走 GGUF（llama.cpp / Ollama）。

**Q：能不能加上云端推理？**
A：不在路线图内。本项目核心理念之一就是本地优先。

**Q：主 UI 进程如何获取 API 进程的端口？**
A：推荐方式 —— Python 启动时把端口写入一个仅本进程可读的临时文件（`/tmp/xijian-<pid>.port`），主 UI 启动后读取该文件。stdout 传端口在 Windows 上不可靠。

**Q：为什么不用 gRPC / 直接 Swift 调 MLX？**
A：跨平台 + 单代码库 + 快速迭代是当前优先级。gRPC 增加打包体积与复杂度；Swift 直调 MLX 会让 Win/Linux 端重复实现业务逻辑。

**Q：NSFW 内容怎么提交？**
A：所有 NSFW 内容必须在资源配置中明确标注，主程序默认隐藏。提交流程同普通资源，但审核要求更严。

**Q：Pywebview 在不同 Linux 桌面环境上是否一致？**
A：不一致。打包时需明确目标环境（GNOME / KDE），并在文档中标注已知差异。

---

_本文档随项目演进持续更新；如有疑问或想补充的内容，请通过 Issue 或邮件反馈。_