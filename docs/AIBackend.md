# AIBackend.md — AI Backend 实现文档

> 本文档描述隙间项目的 AI 后端架构、各后端的实现细节、配置方法及已知限制。
> API 协议见 [api.md](./api.md)；整体架构见 [Dev.md](./Dev.md)。

---

## 1. 架构概览

隙间 API 通过统一的抽象层与多种 AI 后端交互，业务代码只与 `xijian_api.ai.types` 中定义的抽象基类对话，不直接 import 具体后端。

```
┌──────────────────────────────────────────────────────┐
│  Route Layer (/v1/chat/completions, /v1/embeddings…) │
├──────────────────────────────────────────────────────┤
│  Stub Layer (xijian_api.stubs.chat)                   │
│  ├─ _resolve_backend_for(model_id)  ← 注册模型路径    │
│  └─ _select_default_backend()       ← 自由模型ID路径  │
├──────────────────────────────────────────────────────┤
│  ModelRegistry (xijian_api.ai.model_registry)         │
│  └─ model_id → LoadedModel(instance, entry, path)     │
├──────────────────────────────────────────────────────┤
│  Backend Registry (xijian_api.ai.registry)            │
│  └─ name → class  (mlx / gguf / openai / mock)        │
├──────────────────────────────────────────────────────┤
│  Backends                                             │
│  ├─ mlx/    (MLX — Apple Silicon 原生)                │
│  ├─ gguf/   (llama.cpp — 跨平台)                      │
│  ├─ openai/ (OpenAI 兼容远程 API)                     │
│  └─ mock/   (测试/开发用)                             │
└──────────────────────────────────────────────────────┘
```

### 支持的任务类型

| 任务 | 抽象基类 | 端点 |
|------|----------|------|
| Chat | `ChatBackend` | `/v1/chat/completions` |
| Embeddings | `EmbeddingBackend` | `/v1/embeddings` |
| TTS | `TTSBackend` | `/v1/audio/speech` |
| STT | `STTBackend` | `/v1/audio/transcriptions` |
| Image | `ImageGenBackend` | `/v1/images/generations` |
| Video | `VideoGenBackend` | `/v1/video/generations` |

---

## 2. MLX 后端（Apple Silicon 原生）

### 2.1 Chat — `backends/mlx/chat.py`

- **纯文本模型**：基于 `mlx_lm`，支持 `load` / `stream_generate` / `generate`
- **视觉语言模型 (VLM)**：基于 `mlx_vlm`，自动检测模型架构
  - 检测方式：`config.json` 中的 `architectures` 字段 + `preprocessor_config.json` 存在性
  - 支持 `image_url` 内容部分（`file://`、`http(s)://`、`data:image/...;base64,...`）
  - 图片 URL 自动解析为本地临时文件
- **多模态降级**：当纯文本模型收到多模态内容时，自动将 `image_url` / `audio_url` / `video_url` 替换为 `[image]` / `[audio]` / `[video]` 占位符

### 2.2 Embedding — `backends/mlx/embedding.py`

- 优先使用 `mlx_embeddings` 原生路径
- 回退到手写 forward pass（基于 `mlx_lm` 加载权重）

### 2.3 TTS — `backends/mlx/tts.py`

- 基于 `mlx_audio`，支持语音合成

### 2.4 STT — `backends/mlx/stt.py`

- 基于 `mlx_whisper`，支持语音转文字

### 2.5 Image — `backends/mlx/image.py`

- 优先使用 `mlx_stable_diffusion`（不在 PyPI，需手动安装）
- 回退到 `diffusers` + `torch`（MPS 后端）
- 支持 `StableDiffusionPipeline.from_pretrained`（目录）和 `from_single_file`（检查点文件）
- MPS 失败时自动降级到 CPU

### 2.6 Video — `backends/mlx/video.py`

- 基于模型生成视频帧序列

### 已安装的 MLX 扩展包

| 包 | 版本 | 用途 |
|----|------|------|
| `mlx-lm` | — | 纯文本 chat + embedding 回退 |
| `mlx-vlm` | 0.6.6 | 视觉语言模型 |
| `mlx-embeddings` | 0.1.0 | 原生 embedding |
| `mlx-audio` | — | TTS |
| `mlx-whisper` | — | STT |
| `diffusers` | 0.39.0 | 图像生成回退 |
| `torch` | — | diffusers 后端 (MPS) |

---

## 3. GGUF 后端（基于 llama.cpp）

### 3.1 Chat — `backends/gguf/chat.py`

- 基于 `llama-cpp-python`，包装 `Llama.create_chat_completion`
- 支持 streaming（SSE）和 blocking 模式
- 多模态内容以 OAI dict 透传给 `llama_cpp`（取决于绑定是否支持）

### 3.2 Embedding — `backends/gguf/embedding.py`

- 基于 `llama-cpp-python` 的 embedding 接口

### 3.3 TTS — `backends/gguf/tts.py`

- 占位实现，`is_available()` 返回 `False`（无可用 GGUF TTS 绑定）

### 3.4 STT — `backends/gguf/stt.py`

- 基于 `pywhispercpp`（已安装 1.5.0）

### 3.5 Image — `backends/gguf/image.py`

- 基于 `stable_diffusion_cpp`（**未安装** — PyPI 上无可用分发）
- `is_available()` 返回 `False`
- 使用 MLX image 后端或 OpenAI 远程后端替代

### 3.6 Video — `backends/gguf/video.py`

- 占位实现

---

## 4. OpenAI 兼容远程后端

### 4.1 概述

连接任何实现了 OpenAI API 的远程端点（OpenAI 官方、Azure OpenAI、vLLM、Ollama、LM Studio、llama.cpp server 等）。所有 HTTP 流量通过 `httpx`（默认）或 `openai` SDK 传输。

### 4.2 支持的端点

| 远程端点 | 本地后端方法 |
|----------|-------------|
| `POST /chat/completions` | `remote_chat_completion` |
| `POST /embeddings` | `remote_embeddings` |
| `POST /audio/speech` | `remote_tts` |
| `POST /audio/transcriptions` | `remote_stt` |
| `POST /images/generations` | `remote_image_generate` |
| `POST /video/generations` | submit/poll 模式 |

### 4.3 配置方式

支持两种配置方式（可组合使用）：

#### 方式一：全局 `[backends.openai]` 段

```toml
[backends.openai]
base_url = "https://api.openai.com/v1"
api_key = ""                    # 空则使用 $OPENAI_API_KEY
default_model = "gpt-4o"
transport = "httpx"             # httpx | openai_sdk
headers = {}
video_endpoint = "/video/generations"
```

#### 方式二：逐模型 `[[models]].extra`

```toml
[[models]]
id = "gpt-4o-remote"
type = "chat"
backend = "openai"
filename = ""                   # 远程后端不使用
context_length = 128000
loaded = false

[models.extra]
model_name = "gpt-4o"           # 远程 API 的模型名
base_url = "https://api.openai.com/v1"
api_key = ""                    # 空则使用 $OPENAI_API_KEY
```

#### 配置优先级

1. `[[models]].extra` 逐模型字段（最高）
2. `[backends.openai]` 全局段
3. 环境变量（`OPENAI_API_KEY`、`OPENAI_BASE_URL`）
4. 内置默认值（`https://api.openai.com/v1`，空 key）

### 4.4 文件清单

| 文件 | 职责 |
|------|------|
| `_client.py` | 共享 HTTP 客户端、`RemoteConfig`、`resolve_config`、高层 API |
| `chat.py` | `OpenAIChatBackend` — 流式 SSE + 阻塞 + 多模态透传 |
| `embedding.py` | `OpenAIEmbeddingBackend` |
| `tts.py` | `OpenAITTSBackend` |
| `stt.py` | `OpenAISTTBackend` |
| `image.py` | `OpenAIImageBackend` — b64_json/url 归一化 |
| `video.py` | `OpenAIVideoBackend` — submit/poll 模式 |

---

## 5. 多模态内容支持

### 5.1 ChatMessage 扩展

`ChatMessage.content` 类型从 `str` 扩展为 `Union[str, list]`，支持 OAI list-of-parts 格式：

```python
# 纯文本
ChatMessage(role="user", content="Hello")

# 多模态（OAI 格式）
ChatMessage(role="user", content=[
    {"type": "text", "text": "What's in this image?"},
    {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
])
```

### 5.2 `text_content` 属性

`ChatMessage.text_content` 属性从多模态内容中提取纯文本：
- 字符串内容：原样返回
- 列表内容：拼接所有 `{"type": "text"}` 部分的 `text` 字段

### 5.3 各后端处理方式

| 后端 | 处理方式 |
|------|----------|
| OpenAI 远程 | 原样透传（远程 API 负责处理） |
| MLX VLM | 解析 `image_url`，下载/解码为本地文件，传给 `mlx_vlm` |
| MLX 纯文本 | 降级为 `[image]` / `[audio]` / `[video]` 占位符 |
| GGUF | OAI dict 透传给 `llama_cpp` |
| Mock | 通过 `text_content` 提取纯文本处理 |

---

## 6. 后端选择逻辑

### 6.1 注册模型（`model_id` 匹配 `[[models]]` 条目）

```
_resolve_backend_for(model_id)
  → config.model_by_id(model_id)  查找 ModelEntry
  → ModelRegistry.load(model_id, config)
    → _resolve_backend_class(entry.type, entry.backend)
    → 实例化 + load(absolute_path, **kwargs)
    → 缓存到进程级单例
```

### 6.2 自由模型 ID（未注册的 `model_id`）

```
_select_default_backend()
  → 读取 config.backends.chat.default + fallbacks
  → 追加 "mock" 到 fallbacks 末尾
  → get_chat_backend(requested, fallbacks)
  → 如果选中 MLX/GGUF 但未加载 → 回退到 mock
```

### 6.3 流式响应的上下文保持

Chat 路由使用 `flask.stream_with_context()` 包装流式生成器，确保在生成器迭代期间 Flask 应用上下文（含 `XIJIAN_CONFIG`）仍然可用。

---

## 7. 已知限制

1. **`stable-diffusion-cpp` 未安装**：PyPI 上无可用分发（需要 C++ 构建工具链）。GGUF image 后端不可用，请使用 MLX image（diffusers 回退）或 OpenAI 远程后端。

2. **MLX image 生成依赖 `diffusers` + `torch`**：`mlx_stable_diffusion` 不在 PyPI 上。当前使用 `diffusers` + `torch`（MPS 后端）作为回退，首次加载较慢。

3. **桌面控制工具为转发骨架**：桌面级操作（启动应用、控制浏览器、模拟键鼠）需要客户端实现拉取/回写端点。

---

## 8. 测试

- `tests/test_mock_backend.py` — Mock chat 后端契约测试
- `tests/test_openai_backend.py` — OpenAI 远程后端测试（22 项，含配置解析、生命周期、阻塞/流式 chat、多模态透传、错误处理）
- `tests/test_chat_stream_sse.py` — SSE 流式 chat 集成测试
- `tests/test_chat_sync.py` — 阻塞 chat 集成测试
- `tests/test_models.py` — 模型注册/加载/卸载测试
