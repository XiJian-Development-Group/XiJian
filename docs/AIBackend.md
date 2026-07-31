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
| Image Understanding | `MultimodalBackend` | `/v1/images/understanding` |
| Video | `VideoGenBackend` | `/v1/video/generations` |
| Video Understanding | `VideoUnderstandingBackend` | `/v1/multimodal/completions` |
| Multimodal (理解) | `MultimodalBackend` | `/v1/multimodal/completions` |

全模态理解（Multimodal）是新一代接口：单个后端接受文本、图像、音频、视频、文件任意组合的输入并返回理解结果。实现方式分两类：

- **原生多模态**：OpenAI 兼容远程后端（GPT-4o 等）单模型理解所有模态
- **组合式多模态**：MLX / GGUF 本地后端通过编排单模态模型协同工作（图像→VLM、音频→STT、视频→ffmpeg 抽帧→VLM、文件→文本提取）

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
- 支持 `emotion` 情感参数（`synth(..., emotion="happy")`），透传给底层 `mlx_audio.generate`
- 内置歌唱能力检测：`_detect_singing_support()` 检查 `mlx_audio.generate_singing` 是否存在，或模型路径是否暗示歌唱支持（CosyVoice / Bark / XTTS 关键词）

### 2.4 STT — `backends/mlx/stt.py`

- 基于 `mlx_whisper`，支持语音转文字

### 2.5 Image — `backends/mlx/image.py`

- 优先使用 `mlx_stable_diffusion`（不在 PyPI，需手动安装）
- 回退到 `diffusers` + `torch`（MPS 后端）
- 支持 `StableDiffusionPipeline.from_pretrained`（目录）和 `from_single_file`（检查点文件）
- MPS 失败时自动降级到 CPU

### 2.6 Video — `backends/mlx/video.py`

- 基于模型生成视频帧序列

### 2.7 Multimodal Understanding — `backends/mlx/multimodal.py`

组合式全模态理解后端（`MLXMultimodalBackend`，`register_multimodal("mlx")`）：

- 复用 `MLXChatBackend` 的加载与 VLM 检测逻辑
- 音频片段 → MLX STT 转录为文本后注入
- 视频片段 → ffmpeg 抽帧（`_extract_frames_from_video`，默认最多 5 帧）→ 图像片段
- 文件片段 → 文本/二进制提取
- 图像 + 文本 → 原样交给 VLM
- `modalities()` 报告各模态可用性（text/image/audio/video/file）

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
- **VLM 检测**：`_detect_vlm(path)` 检查目录中是否存在 `.mmproj` 文件（llama.cpp VLM 加载的明确标志），或检查 `config.json` 的 `architectures` 字段是否包含已知 VLM 架构名称（`vl` / `vision` / `llava` / `qwen2vl` / `paligemma` / `idefics` / `pixtral` / `internvl` 等）
- VLM 模式下，消息中的 `image_url` 内容片段会被解析为本地路径（`file://` / `http(s)://` / `data:` URI）并传递给 `create_chat_completion()`

### 3.2 Embedding — `backends/gguf/embedding.py`

- 基于 `llama-cpp-python` 的 embedding 接口

### 3.3 TTS — `backends/gguf/tts.py`

- 占位实现，`is_available()` 返回 `False`（无可用 GGUF TTS 绑定）

### 3.4 STT — `backends/gguf/stt.py`

- 基于 `pywhispercpp`（已安装 1.5.0）

### 3.5 Image — `backends/gguf/image.py`

- 基于 `stable_diffusion_cpp`（**未安装**）
- `is_available()` 返回 `False`
- 使用 MLX image 后端或 OpenAI 远程后端替代
- 注：`stable-diffusion-cpp-python`（0.4.7+）已在 PyPI 分发（对应 stable-diffusion.cpp，支持 SD/SDXL/Flux/Qwen-Image/Wan 等 GGUF），安装后即可启用

### 3.6 Video — `backends/gguf/video.py`

- 占位实现

### 3.7 Multimodal Understanding — `backends/gguf/multimodal.py`

组合式全模态理解后端（`GGUFMultimodalBackend`，`register_multimodal("gguf")`）：

- 图像理解 → llama.cpp VLM（通过 GGUF chat backend，依赖 mmproj）
- 音频理解 → GGUF STT backend（pywhispercpp）转录为文本
- 视频理解 → ffmpeg 帧提取 → VLM
- 文件理解 → 文本/二进制提取
- 文本理解 → GGUF chat backend

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
| `tts.py` | `OpenAITTSBackend` — 支持 `emotion`（非标准字段，透传/忽略） |
| `stt.py` | `OpenAISTTBackend` |
| `image.py` | `OpenAIImageBackend` — b64_json/url 归一化 |
| `video.py` | `OpenAIVideoBackend` — submit/poll 模式 |
| `multimodal.py` | `OpenAIMultimodalBackend` — 原生全模态理解（GPT-4o 等） |
| `video_understanding.py` | `OpenAIVideoUnderstandingBackend` — ffmpeg 抽帧 + 多图理解 |

### 4.5 全模态理解（Multimodal）

`OpenAIMultimodalBackend`（`register_multimodal("openai")`）利用 GPT-4o / GPT-4o-audio-preview 等模型的原生全模态能力：

- 文本、图像、音频、视频帧输入通过 OpenAI `/chat/completions` API 发送
- 音频 → `input_audio` 原生格式（base64 + format）
- 视频 → ffmpeg 抽帧 → 多 `image_url`（OpenAI 无原生视频输入）
- 支持模态清单统计（`_inventory_modalities`）与流式/阻塞两种模式

`OpenAIVideoUnderstandingBackend`（`register_video_understanding("openai")`）：

- ffmpeg 抽帧（默认最多 10 帧，1 fps）→ 帧图像 → 远程 VLM 理解
- 支持视频 URL（http/file/base64）解析与时间线问答

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
| GGUF | OAI dict 透传给 `llama_cpp`（VLM 模式解析 image_url 为本地路径） |
| Mock | 通过 `text_content` 提取纯文本处理 |

### 5.4 全模态内容片段辅助函数（`ai/types.py`）

```python
make_text_part(text)              # → {"type": "text", "text": ...}
make_image_part(url)              # → {"type": "image_url", ...}
make_audio_part(url, format=...)  # → {"type": "audio_url", ...}
make_video_part(url, format=...)  # → {"type": "video_url", ...}
make_file_part(url, mime_type=...)  # → {"type": "file_url", ...}
resolve_part_to_path(part)        # data:/http/file/裸路径 → 本地文件路径
resolve_part_content(part)        # → bytes（媒体）或 str（文本）
detect_part_mime(part)            # 推断 MIME 类型
```

`resolve_part_to_path` 支持 `data:` base64、`http(s)://` 下载、`file://` 与裸路径四种输入。

### 5.5 `/v1/multimodal/completions` 端点

统一全模态理解入口（`routes/multimodal.py`，`stubs/multimodal.py` 调度）：

- `POST /v1/multimodal/completions` — 同步或流式（`stream=true`）理解，消息格式与 `/v1/chat/completions` 相同，但 `content` 可含 `audio_url` / `video_url` / `file_url` 等任意 OAI 内容片段
- `GET /v1/multimodal/models` — 列出配置中 `type = "multimodal"` 的模型
- `POST /v1/multimodal/abort` — 通过 `request_id` 中止流式请求
- `POST /v1/images/understanding` — 图像理解专用端点（multipart `image` 或 JSON `image`/`url` + 可选 `prompt`）

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

1. **`stable-diffusion-cpp` 未安装**：GGUF image 后端当前不可用（依赖 C++ 构建；`stable-diffusion-cpp-python` 已在 PyPI 提供分发，可 `pip install` 启用）。请使用 MLX image（diffusers 回退）或 OpenAI 远程后端。

2. **MLX image 生成依赖 `diffusers` + `torch`**：`mlx_stable_diffusion` 不在 PyPI 上。当前使用 `diffusers` + `torch`（MPS 后端）作为回退，首次加载较慢。

3. **桌面控制工具为转发骨架**：桌面级操作（启动应用、控制浏览器、模拟键鼠）需要客户端实现拉取/回写端点。

4. **本地歌唱 TTS 不可用**：当前 mlx-audio 支持列表不含专门的歌唱模型（CosyVoice/Bark/XTTS 为旧版 mlx-audio 思路，已不在现行支持列表）。情感/语气可通过 Qwen3-TTS CustomVoice 的 `instruct` 参数近似实现。

5. **`video_understanding` 后端覆盖**：目前仅 OpenAI 远程 + mock 实现；GGUF/MLX 尚无对应后端，因此配置默认指向 `openai`（不再指向不存在的 mlx/gguf）。未注册远程端点时会返回 503（`backend_unavailable`）。

6. **全模态可用性依赖模型与依赖**：MLX 组合式后端需要 `mlx_vlm`（图像/视频）与 `mlx_whisper`/`mlx_audio`（音频）；GGUF 需要 `llama_cpp`（VLM + mmproj）与 `pywhispercpp`（音频）。任一缺失时对应模态自动降级，`modalities()` 会如实报告。

---

## 8. 测试

- `tests/test_mock_backend.py` — Mock chat 后端契约测试
- `tests/test_openai_backend.py` — OpenAI 远程后端测试（22 项，含配置解析、生命周期、阻塞/流式 chat、多模态透传、错误处理）
- `tests/test_chat_stream_sse.py` — SSE 流式 chat 集成测试
- `tests/test_chat_sync.py` — 阻塞 chat 集成测试
- `tests/test_models.py` — 模型注册/加载/卸载测试
- `tests/test_multimodal.py` — 全模态测试（25 项：`/v1/multimodal/completions` 同步/流式/中止、`/v1/multimodal/models`、`/v1/images/understanding`、`/v1/audio/speech` emotion 透传、stub 调度、mock 后端契约）
