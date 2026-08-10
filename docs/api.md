# API.md — 隙间 本地 API 协议规范

> 本文档定义「隙间」跨平台本地 API 的完整协议。
> 阅读对象：UI 端开发者、第三方集成者、API 后端实现者。
> 整体架构与进程模型见 [Dev.md §2](./Dev.md)。

---

## 0. 设计总览

### 0.1 一句话

隙间本地 API 是 **「OAI 兼容 + 隙间扩展」** 的双协议栈：

- **OAI 兼容层**：完整实现 OpenAI 官方 API 表面（chat / embeddings / audio / images / video / models / files / fine-tuning 等），第三方客户端（openai-python、langchain、llama-index）可零修改接入。
- **隙间扩展层**：以 `/v1/xijian/*` 命名空间承载项目特有能力（角色、互动、世界、记忆、保护模块等）。

### 0.2 关键设计决策

| 决策         | 取舍                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **传输**     | HTTP + SSE + WebSocket 三栈并行。请求-响应 → HTTP；流式生成 → SSE 或 NDJSON；双向推送（角色主动消息、AI 控制信号）→ WebSocket                  |
| **OAI 兼容** | 完整支持 chat / embeddings / audio（speech + transcription + translation）/ images（generation + edit + variation）/ video / models / files |
| **流式协议** | SSE 与 NDJSON **同时支持**，由客户端通过 `Accept` 头协商                                                                                  |
| **鉴权**     | Bearer Token，启动时随机生成，仅 `127.0.0.1` 访问                                                                                          |
| **错误格式** | **双格式共存**：`Accept: application/json` 返回 OAI 错误；`Accept: application/json-rpc` 返回 JSON-RPC 2.0 错误                            |
| **幂等性**   | 通过 `Idempotency-Key` 头支持去重，适用于长任务                                                                                            |
| **取消**     | 专用 `POST /v1/chat/abort`、`POST /v1/xijian/generation/abort` 端点，按 `request_id` 立即停止生成                                            |

### 0.3 base URL

```
http://127.0.0.1:{port}/v1
ws://127.0.0.1:{port}/v1/ws
```

端口解析顺序：命令行 `--port` > 环境变量 `XIJIAN_API_PORT` > `config.toml` > 默认 `18500`。
配置端口被占用时 Core **不会退出**：检测到占用并报告占用进程后，自动向上扫描空闲端口
（最多 100 个）并用新端口启动；实际生效端口通过统一临时目录下的
`xijian-<pid>.port` 文件下发（`~/Library/Application Support/XiJian/tmp/xijian-<pid>.port`，
开发与打包模式一致），客户端等待该文件出现即可得知真实端口。需要「占用即退出」
固定端口行为的场景可加 `--port-strict`。

---

## 1. 通用规范

### 1.1 通用请求头

| Header                | 必填 | 说明                                                            |
| --------------------- | ---- | --------------------------------------------------------------- |
| `Authorization`       | ✅   | `Bearer <token>`                                                |
| `Content-Type`        | ✅   | `application/json`（multipart 用于上传）                        |
| `Accept`              | ❌   | 响应格式协商（见 §1.4）                                          |
| `Idempotency-Key`     | ❌   | 幂等键，见 §1.6                                                  |
| `X-XiJian-Request-Id` | ❌   | 客户端生成的请求 ID，用于 abort；不传则服务端自动生成            |
| `X-XiJian-Trace-Id`   | ❌   | 跨调用追踪 ID，服务端会透传到日志                                |

### 1.2 通用响应头

| Header                | 说明                                         |
| --------------------- | -------------------------------------------- |
| `X-XiJian-Request-Id` | 与请求头一致，未传则返回服务端生成的 ID      |
| `X-XiJian-Model-Id`   | 实际使用的模型 ID（含平台后端标记）          |
| `X-XiJian-Backend`    | 实际使用的 AI backend：`mlx` / `gguf`        |
| `X-RateLimit-*`       | OAI 兼容的速率限制头（本地场景一般不限流）   |

### 1.3 HTTP 状态码

| 状态码 | 含义                                                                 |
| ------ | -------------------------------------------------------------------- |
| 200    | 成功（非流式）                                                       |
| 201    | 创建成功（files / fine-tuning jobs 等）                              |
| 204    | 成功无 body（abort、delete）                                         |
| 400    | 请求参数错误                                                          |
| 401    | 鉴权失败                                                              |
| 403    | 权限不足（如关闭保护系统前未双重确认）                                |
| 404    | 资源不存在（角色 / 模型 / 文件）                                      |
| 409    | 资源冲突（幂等键已使用但参数不一致）                                  |
| 413    | 请求体过大                                                            |
| 422    | 语义错误（OAI 风格用得多，隙间扩展偶尔使用）                          |
| 429    | 速率限制（理论上本地不会触发，留作未来）                              |
| 500    | 服务端内部错误                                                        |
| 503    | 服务不可用（模型未加载 / 生成类后端未配置 `backend_unavailable` / 进程启动中） |

### 1.4 错误响应双格式

#### 1.4.1 OAI 错误格式（`Accept: application/json` 或未指定）

```http
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "error": {
    "message": "`temperature` must be a valid number",
    "type": "invalid_request_error",
    "param": "temperature",
    "code": "invalid_numeric_value"
  }
}
```

> **数值校验范围**：数字字段（如 `temperature` / `top_p`）只拒绝**非数字**、布尔值、`NaN` / `±Infinity`（返回 400 `invalid_numeric_value`），**不做** `[0, 2]` 之类的范围钳制——`temperature: 99` 会被接受并原样传给后端。这与 OpenAI 的严格范围校验不同，属设计取舍。

#### 1.4.2 JSON-RPC 2.0 错误格式（`Accept: application/json-rpc`）

```http
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": "req_8f3a2b1c",
  "error": {
    "code": -32602,
    "message": "Invalid params",
    "data": {
      "param": "temperature",
      "expected": "valid number (non-NaN / non-Infinity)",
      "got": "abc"
    }
  }
}
```

**JSON-RPC 错误码映射**：

| JSON-RPC code | 含义       | 对应 HTTP | OAI type               |
| ------------- | ---------- | --------- | ---------------------- |
| -32700        | Parse error | 400       | `invalid_request_error` |
| -32600        | Invalid Request | 400   | `invalid_request_error` |
| -32601        | Method not found | 404 | `invalid_request_error` |
| -32602        | Invalid params | 400     | `invalid_request_error` |
| -32603        | Internal error | 500     | `server_error`          |
| -32001        | Resource not found | 404  | `invalid_request_error` |
| -32002        | Conflict   | 409       | `invalid_request_error` |
| -32003        | Forbidden  | 403       | `permission_error`      |
| -32004        | Rate limit | 429       | `rate_limit_error`      |
| -32005        | Backend unavailable | 503 | `server_error`        |
| -32010        | Protection blocked | 403 | `protection_error`   |
| -32011        | NSFW content gated | 403 | `content_filter`     |

### 1.5 分页

列表类接口统一使用 OAI 分页风格：

```json
{
  "object": "list",
  "data": [...],
  "has_more": true,
  "first_id": "char_abc",
  "last_id": "char_xyz"
}
```

查询参数：`limit`（默认 20，最大 100）、`order`（`asc` / `desc`）、`after` / `before`（游标）。

> **宽容行为**（与 OpenAI 严格 400 不同）：`limit` 超界**静默钳制**到 `[1, 100]`（非数字回退默认 20）；`order` 非法值**静默回退** `asc`；均不返回 400。游标语义：`after=<id>` 保留 id 严格排序在游标之后（desc 为之前）的项目，`before` 对称；两者同时给出时 `after` 优先。

### 1.6 幂等性

- 客户端可在 `POST` 请求中带 `Idempotency-Key: <uuid>`。
- 服务端在 24h 内对相同 key 缓存响应，重复请求返回缓存结果。
- 若同一 key 第二次请求但 body 不同，返回 **409 Conflict** + 错误信息。
- 仅作用于 `POST`；幂等键在日志中脱敏。

---

## 2. OAI 兼容层（`/v1/*`）

> 所有路径对齐 OpenAI 官方 API。OpenAI Python SDK v1.x 可直接用 `openai.OpenAI(base_url="http://127.0.0.1:<port>/v1", api_key="<bearer-token>")` 接入。

### 2.1 模型管理

#### `GET /v1/models`

列出当前可用模型。

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen2.5-7b-mlx-4bit",
      "object": "model",
      "created": 1718000000,
      "owned_by": "xijian",
      "xijian": {
        "backend": "mlx",
        "family": "qwen2.5",
        "size_b": 7.0,
        "quant": "4bit",
        "context_length": 32768,
        "min_ram_gb": 8,
        "loaded": true
      }
    }
  ]
}
```

#### `GET /v1/models/{model_id}`

```json
{
  "id": "qwen2.5-7b-mlx-4bit",
  "object": "model",
  ...
}
```

#### `POST /v1/models/{model_id}/load`

触发模型加载（异步，返回 202）。

```json
// Request
{ "gpu_layers": -1, "context_length": 8192 }

// Response 202
{
  "id": "load_op_abc",
  "object": "model.load",
  "status": "loading",
  "progress_url": "/v1/models/operations/load_op_abc"
}
```

#### `POST /v1/models/{model_id}/unload`

释放模型（同步）。

#### `GET /v1/models/operations/{op_id}`

查询加载/卸载操作状态。

### 2.2 Chat Completions

#### `POST /v1/chat/completions`

完整 OAI 兼容，支持 `stream=true` / `stream_options` / `tools` / `tool_choice` / `response_format` / `logprobs` / `n`。

**请求**：

```json
{
  "model": "qwen2.5-7b-mlx-4bit",
  "messages": [
    {"role": "system", "content": "你是一个温柔的二次元角色。"},
    {"role": "user", "content": "你好呀"}
  ],
  "temperature": 0.7,
  "top_p": 1.0,
  "max_tokens": 1024,
  "stream": false,
  "stop": ["<|im_end|>"],
  "presence_penalty": 0,
  "frequency_penalty": 0,
  "user": "xijian_user_001",
  "xijian": {
    "character_id": "char_yuki",
    "world_id": "world_modern_tokyo",
    "recall": {"enabled": true}
  }
}
```

**`xijian` 扩展字段**（隙间特有，不影响 OAI 客户端）：

| 字段              | 类型     | 说明                                                |
| ----------------- | -------- | --------------------------------------------------- |
| `character_id`    | string   | 当前角色 ID；配合 `recall.enabled` 启用角色系统 Prompt 注入（角色设定/人设/状态/世界上下文）与强制记忆召回管线；也用于对话记忆写入与 A3.2 角色状态门控 |
| `world_id`        | string   | 当前世界 ID；在 MCP 工具管线（`tools.enabled`）中作为 A5.2 门控的世界上下文 |
| `recall.enabled`  | bool     | 是否启用强制记忆召回管线（记忆注入 + `recall_memory` 工具 + 引用审计；默认 `false`；角色级 `memory_config.force_recall_on_history` 为 0 时即使请求置 true 也跳过） |
| `recall.audit`    | bool     | 引用审计开关（默认 `true`） |
| `tools.enabled`   | bool     | 是否启用 MCP 工具管线（与 OAI `tools` 字段等价的入口；开启时优先于召回管线） |
| `backend`         | string   | 仅用于响应头 `X-XiJian-Backend` 标识，不参与后端选择 |
| `inject_memory`   | bool     | **当前版本不生效**：无对应实现；记忆注入由 `recall.enabled` 控制，不按此字段开关 |
| `memory_top_k`    | int      | **当前版本不生效**：无对应实现；注入条数由角色 `memory_config`（`max_long_term` / `max_short_term`）决定 |
| `guard_level`     | string   | **当前版本在 `/v1/chat/completions` 中不生效**：该字段是 safety gate 的全局记录字段（见 §3.5 `GET /v1/xijian/safety/gate/status`），不是请求级参数 |
| `nsfw_allowed`    | bool     | **当前版本在 `/v1/chat/completions` 中不生效**：仅用于 `POST /v1/xijian/interactions/{interaction_id}/trigger`（放行 soft/explicit 互动）与 `POST /v1/xijian/characters`（角色属性）；聊天请求本身不做 NSFW 分级过滤 |

**非流式响应**（200）：

```json
{
  "id": "chatcmpl-9f8a7b6c",
  "object": "chat.completion",
  "created": 1718000000,
  "model": "qwen2.5-7b-mlx-4bit",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好呀~ 见到你很开心！"
      },
      "finish_reason": "stop",
      "logprobs": null
    }
  ],
  "usage": {
    "prompt_tokens": 128,
    "completion_tokens": 18,
    "total_tokens": 146
  },
  "xijian": {
    "backend": "mlx",
    "guard_triggered": false,
    "memory_hits": 3
  }
}
```

**流式响应（SSE）**（`stream=true` 且 `Accept: text/event-stream`）：

```
data: {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","created":1718000000,"model":"qwen2.5-7b-mlx-4bit","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","created":1718000000,"model":"qwen2.5-7b-mlx-4bit","choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}

data: {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","created":1718000000,"model":"qwen2.5-7b-mlx-4bit","choices":[{"index":0,"delta":{"content":"呀~"},"finish_reason":null}]}

data: {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","created":1718000000,"model":"qwen2.5-7b-mlx-4bit","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","created":1718000000,"model":"qwen2.5-7b-mlx-4bit","choices":[],"usage":{"prompt_tokens":128,"completion_tokens":18,"total_tokens":146}}

data: [DONE]
```

**流式响应（NDJSON）**（`stream=true` 且 `Accept: application/x-ndjson`）：

```
{"id":"chatcmpl-9f8a","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant","content":""}}]}
{"id":"chatcmpl-9f8a","object":"chat.completion.chunk","choices":[{"delta":{"content":"你好"}}]}
{"id":"chatcmpl-9f8a","object":"chat.completion.chunk","choices":[{"delta":{"content":"呀~"}}]}
{"id":"chatcmpl-9f8a","object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}
{"id":"chatcmpl-9f8a","object":"chat.completion.chunk","usage":{"prompt_tokens":128,"completion_tokens":18,"total_tokens":146}}
```

### 2.3 Embeddings

#### `POST /v1/embeddings`

完整 OAI 兼容，支持 `input` 为字符串或字符串数组。

> **出厂配置**：默认 `config.toml` 仅注册 mock chat / multimodal / video_understanding 模型，
> 未配置任何 embedding 后端。未配置模型后端时本端点返回 **503 `backend_unavailable`**；
> 需在 `[[models]]` 中注册 `type = "embeddings"` 条目（bge-m3 等）并配置 `[backends.embeddings]` 后可用。
> audio（TTS/STT）、images/videos 生成类端点同理（见 §2.4-§2.6）。

```json
{
  "model": "bge-m3",
  "input": ["你好世界", "Hello world"],
  "encoding_format": "float",
  "dimensions": 1024
}
```

### 2.4 Audio

#### `POST /v1/audio/speech`

TTS，OAI 兼容。

```json
{
  "model": "cosyvoice-tts",
  "input": "你好呀",
  "voice": "yuki-female-jp",
  "response_format": "mp3",
  "speed": 1.0,
  "emotion": "happy",
  "xijian": {
    "voice_clone_ref": "voice_ref_abc"
  }
}
```

返回二进制音频流（`Content-Type: audio/mpeg` 等）。

可选字段 `emotion`（如 `"happy"` / `"sad"` / `"calm"`）透传给 TTS 后端控制语气；不支持的后端会忽略它。

#### `POST /v1/audio/transcriptions`

STT（语音转文字），multipart/form-data。

字段：`file`（必填）、`model`（必填）、`language`（可选）、`prompt`（可选）、`response_format`（`json` / `text` / `srt` / `vtt`，默认 `json`）、`temperature`（可选）。

#### `POST /v1/audio/translations`

把任意语言音频翻译为目标语言（默认英文）。同 transcriptions 字段。

### 2.5 Images

#### `POST /v1/images/generations`

```json
{
  "model": "sdxl-turbo-mlx",
  "prompt": "an anime girl in a coffee shop",
  "n": 1,
  "size": "1024x1024",
  "response_format": "b64_json",
  "xijian": {
    "negative_prompt": "low quality",
    "seed": 42,
    "nsfw_allowed": false
  }
}
```

#### `POST /v1/images/edits`

multipart/form-data：`image`（必填）、`mask`（可选）、`prompt`（必填）、`n` / `size` / `response_format` / `model`。

#### `POST /v1/images/variations`

multipart/form-data：`image`（必填）、`n` / `size` / `response_format` / `model`。

#### `POST /v1/images/understanding`

图像理解（视觉）端点。两种入参：

- **multipart/form-data**：`image`（必填文件）+ 可选 `prompt`（默认 "Describe this image in detail."）+ 可选 `model`
- **JSON**：`image` 或 `url`（base64 data URI 或远程 URL）+ 可选 `prompt` + 可选 `model` + 可选 `temperature` / `max_tokens`

缺图时返回 400（`missing_image`）。返回 OAI 风格 completion 对象（`choices[0].message.content` 为理解文本）。

### 2.6 Video

#### `POST /v1/videos/generations`

```json
{
  "model": "wan2.1-video-mlx",
  "prompt": "角色在樱花树下转身微笑",
  "input_reference": "img_abc",
  "seconds": 4,
  "size": "1280x720",
  "fps": 24,
  "xijian": {
    "seed": 42,
    "nsfw_allowed": false
  }
}
```

视频生成耗时较长，**默认异步**：

```json
// Response 202
{
  "id": "vid_abc",
  "object": "video.generation",
  "status": "queued",
  "created_at": 1718000000,
  "completed_at": null,
  "expires_at": 1718003600,
  "error": null,
  "remixed_from_video_id": null
}
```

#### `GET /v1/videos/{video_id}`

查询任务状态。`status` 取值：`queued` / `in_progress` / `completed` / `failed`。`completed` 时 `url` 字段填充（`http://127.0.0.1:<port>/v1/files/<file_id>/content`）。

#### `GET /v1/videos`

分页列出历史任务。

#### `POST /v1/videos/{video_id}/remix`

基于已有视频做二次生成。

#### `DELETE /v1/videos/{video_id}`

删除任务与对应文件。

#### `POST /v1/videos/understanding`

视频理解端点 —— 通过配置的视频理解后端（默认 ``video_understanding`` 任务类型，
目前为 OpenAI 远程 + mock 实现）理解视频内容并返回文本描述。

- **JSON**：`video`（URL / data URI / 本地路径）+ 可选 `prompt`（默认
  "Describe what is happening in this video."）+ 可选 `model` + 可选 `fps`（默认 1）
  + 可选 `max_frames`（默认 10）
- **multipart/form-data**：`video`（必填文件）+ 可选 `prompt` / `model` / `fps` / `max_frames`

缺视频时返回 400（`missing_video`）；模型不可用时返回 503。成功返回：

```json
{
  "object": "video.understanding",
  "model": "stub-video-understanding",
  "text": "视频内容的文本描述"
}
```

### 2.7 Multimodal（全模态理解）

统一的全模态理解入口，接受文本、图像、音频、视频、文件任意组合的输入。

#### `POST /v1/multimodal/completions`

请求格式与 `/v1/chat/completions` 相同，但 `content` 字段可以是任意 OAI 内容片段列表：

```json
{
  "model": "stub-multimodal",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "这张图里有什么？"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,..."}},
        {"type": "video_url", "video_url": {"url": "file:///tmp/clip.mp4"}}
      ]
    }
  ],
  "stream": false
}
```

支持的内容片段类型：`text` / `image_url` / `audio_url` / `video_url` / `file_url`（URL 支持 `data:` base64、`http(s)://`、`file://` 与裸路径）。

响应为 OAI 风格 completion 对象：

```json
{
  "id": "chatcmpl_xxx",
  "object": "multimodal.completion",
  "created": 1718000000,
  "model": "stub-multimodal",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "理解结果文本"},
      "finish_reason": "stop",
      "logprobs": null
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "xijian": {"backend": "mock"}
}
```

`stream=true` 时返回 SSE 流，chunk 的 `object` 为 `multimodal.completion.chunk`；支持 `stream_options.include_usage` 与中止（见下）。

#### `GET /v1/multimodal/models`

列出配置中 `type = "multimodal"` 的模型：

```json
{"object": "list", "data": [{"id": "stub-multimodal", "type": "multimodal", ...}]}
```

#### `POST /v1/multimodal/abort`

```json
{"request_id": "req_xxx"}
```

已注册的 `request_id` 返回 204；未知返回 200 `{"aborted": false}`；缺 `request_id` 返回 400。

### 2.8 Files

#### `POST /v1/files`

multipart/form-data：`file`（必填）、`purpose`（必填：`assistants` / `vision` / `evals` / `fine-tune` / `user_data`）。

#### `GET /v1/files`

#### `GET /v1/files/{file_id}`

#### `GET /v1/files/{file_id}/content`

返回二进制内容。`Content-Disposition: attachment; filename="<原文件名>"`。

#### `DELETE /v1/files/{file_id}`

### 2.8 Batches

#### `POST /v1/batches`

```json
{
  "input_file_id": "file_abc",
  "endpoint": "/v1/chat/completions",
  "completion_window": "24h",
  "metadata": {}
}
```

#### `GET /v1/batches/{batch_id}`

#### `GET /v1/batches/{batch_id}/results`

下载结果文件。

#### `POST /v1/batches/{batch_id}/cancel`

### 2.9 Fine-tuning

完整 OAI 兼容（用于本地小模型微调）。

- `POST /v1/fine_tuning/jobs`
- `GET /v1/fine_tuning/jobs`
- `GET /v1/fine_tuning/jobs/{job_id}`
- `POST /v1/fine_tuning/jobs/{job_id}/cancel`
- `GET /v1/fine_tuning/jobs/{job_id}/events`
- `GET /v1/fine_tuning/jobs/{job_id}/checkpoints`
- `POST /v1/fine_tuning/jobs/{job_id}/checkpoints/permissions`

### 2.10 Assistants / Threads / Runs

**这是 OAI 兼容层中可选模块**。隙间的角色系统与 Assistants 在概念上重叠，提供是为了让第三方 RAG 工具能直接对接；隙间自有 UI 使用 `/v1/xijian/character/*`。

- `POST /v1/assistants`
- `GET /v1/assistants` / `GET /v1/assistants/{asst_id}`
- `POST /v1/assistants/{asst_id}` / `DELETE /v1/assistants/{asst_id}`
- `POST /v1/threads`
- `GET /v1/threads/{thread_id}` / `POST /v1/threads/{thread_id}` / `DELETE /v1/threads/{thread_id}`
- `POST /v1/threads/{thread_id}/messages` / `GET /v1/threads/{thread_id}/messages` / `GET /v1/threads/{thread_id}/messages/{msg_id}`
- `POST /v1/threads/{thread_id}/runs` / `GET /v1/threads/{thread_id}/runs` / `GET /v1/threads/{thread_id}/runs/{run_id}` / `POST /v1/threads/{thread_id}/runs/{run_id}` / `POST /v1/threads/{thread_id}/runs/{run_id}/cancel` / `POST /v1/threads/{thread_id}/runs/{run_id}/steps` / `GET /v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}` / `POST /v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs`

### 2.11 Completions（Legacy）

为兼容遗留客户端提供：

- `POST /v1/completions`

---

## 3. 隙间扩展层（`/v1/xijian/*`）

隙间特有能力，不在 OAI 范围内。命名空间为 `/v1/xijian/`，错误格式遵循 §1.4 协商。

### 3.1 角色（Character）

#### `POST /v1/xijian/characters`

创建角色（管理员/创作者使用，普通用户走资源邮件提交）。

```json
{
  "name": "雪",
  "display_name": "Yuki",
  "persona_doc": "...",
  "voice_profile": "voice_ref_abc",
  "default_emotion": "neutral",
  "tags": ["tsundere", "student"]
}
```

#### `GET /v1/xijian/characters`

分页列出已加载角色。

#### `GET /v1/xijian/characters/{character_id}`

#### `PATCH /v1/xijian/characters/{character_id}`

#### `DELETE /v1/xijian/characters/{character_id}`

#### `POST /v1/xijian/characters/{character_id}/load`

加载角色到当前 session（加载人设与状态）。

#### `POST /v1/xijian/characters/{character_id}/unload`

#### `POST /v1/xijian/characters/{character_id}/interact`

触发互动（与 §3.2 互动系统交互）。

```json
{
  "interaction_id": "int_hug",
  "context": {
    "location": "home",
    "time_of_day": "evening"
  },
  "idempotency_key": "uuid"
}
```

#### `GET /v1/xijian/characters/{character_id}/state`

获取角色当前状态（好感度、心情、最近记忆摘要等）。

#### `POST /v1/xijian/characters/{character_id}/state`

更新角色状态（受保护模块约束）。

### 3.2 互动（Interaction）

互动是角色对特定情境的预设回应集（回应 + 动作映射）。

#### `GET /v1/xijian/interactions`

列出所有可用互动类型（分页）。

**响应 200**：

```json
{
  "object": "list",
  "data": [
    {
      "id": "int_hug",
      "name": "拥抱",
      "nsfw_level": "safe",
      "category": "affection",
      "cooldown_seconds": 60,
      "requires_state": {"intimacy": {"min": 20}}
    }
  ],
  "has_more": false
}
```

**错误码**：无（分页参数非法时 400）。

#### `POST /v1/xijian/interactions/{interaction_id}/trigger`

手动触发互动（绕过角色自主决策）。

**请求体**：

```json
{
  "character_id": "char_yuki",
  "context": {"location": "home", "mood": 70},
  "nsfw_allowed": false
}
```

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `character_id` | string | 否 | 目标角色；缺省用默认角色 |
| `context` | object | 否 | 触发上下文（位置 / 心情等） |
| `nsfw_allowed` | boolean | 否 | 是否允许 NSFW 回应，默认 `false` |

**响应 200**：`{accepted, reason, response, action, ...}`。

**错误码**：

| 状态码 | code | 说明 |
| ------ | ---- | ---- |
| 404 | `interaction_not_found` | 互动不存在 |

#### `GET /v1/xijian/interactions/{interaction_id}/responses`

查询某互动下角色所有可能的回应与动作映射。

**响应 200**：

```json
{
  "object": "list",
  "data": [{"text": "…", "action": {"type": "hug", "target": "character"}}],
  "has_more": false
}
```

**错误码**：404 `interaction_not_found`。

### 3.3 世界（World）

#### `GET /v1/xijian/worlds`

列出已加载世界。

#### `POST /v1/xijian/worlds/{world_id}/transition`

```json
{
  "from_location": "home",
  "to_location": "school",
  "transport": "walking",  // walking | bicycle | train | taxi | ...
  "eta_seconds": 900
}
```

#### `GET /v1/xijian/worlds/{world_id}/state`

获取经济 / 健康 / 饮食 / 体力 / 心智等维度状态。

#### `PATCH /v1/xijian/worlds/{world_id}/state`

更新状态值（受保护模块约束）。

#### `POST /v1/xijian/worlds/{world_id}/event`

注入世界事件（剧情向）。

### 3.4 记忆（Memory）

#### `POST /v1/xijian/memory/entries`

```json
{
  "character_id": "char_yuki",
  "content": "用户喜欢草莓味的冰淇淋",
  "attributes": {
    "importance": "high",
    "decay": "never",        // never | slow | normal | fast
    "category": "preference"
  },
  "tags": ["food", "ice_cream"]
}
```

#### `GET /v1/xijian/memory/entries`

分页 + 过滤（按 `character_id`、`tags`、`importance` 等）。

#### `GET /v1/xijian/memory/entries/{entry_id}`

#### `PATCH /v1/xijian/memory/entries/{entry_id}`

#### `DELETE /v1/xijian/memory/entries/{entry_id}`

#### `POST /v1/xijian/memory/search`

向量检索。

```json
{
  "query": "用户喜欢吃什么",
  "character_id": "char_yuki",
  "top_k": 5,
  "min_score": 0.7
}
```

#### `POST /v1/xijian/memory/consolidate`

触发记忆整理（异步），将短期会话提炼为长期记忆。

#### `POST /v1/xijian/memory/forget`

触发遗忘（按衰减策略或指定条目）。

### 3.5 安全模块（Safety）

> **已废弃**：原 `/v1/xijian/protection/*` 别名路由已全部移除。安全能力统一走 `/v1/xijian/safety/*` 端点；AI 数据快照与回滚走 `/v1/xijian/backups/*`（A5.3）。原 `protection` 模块的状态桶 `state.protection` 已重命名为 `state.safety_state`；enable/disable 闸门（含两步挑战）与 audit 导出能力已迁移到 `/v1/xijian/safety/gate/*` 与 `/v1/xijian/safety/audit/export`。保护默认开启（`enabled=True`）。

**所有 safety 端点都受安全模块自身监控**——任何尝试绕过安全系统的请求都会写入审计日志。

#### `POST /v1/xijian/safety/scan/input`

预检用户输入。参见 A5.1 安全模块的统一扫描端点。

#### `POST /v1/xijian/safety/scan/output`

后检助手输出。参见 A5.1 安全模块的统一扫描端点。

#### `GET /v1/xijian/safety/gate/status`

返回保护闸门状态。

```json
{
  "enabled": true,
  "guard_level": "standard",
  "audit_log_size": 1234,
  "version": "1.0.0"
}
```

#### `POST /v1/xijian/safety/gate/enable`

启用保护闸门（幂等，默认开启）。返回与 `gate/status` 相同的状态快照。

#### `POST /v1/xijian/safety/gate/disable`

**关闭保护闸门**，必须双重确认：

**Step 1**（请求体不含 `challenge_id`）：

```json
// Request
{ "confirmation": "I understand the risks" }

// Response 200
{
  "challenge_id": "chal_abc",
  "expires_at": 1718000900,
  "challenge_phrase": "关闭保护 Yuki"
}
```

**Step 2**（必须在 60s 内，请求体含 `challenge_id` + `phrase`）：

```json
// Request
{
  "challenge_id": "chal_abc",
  "phrase": "关闭保护 Yuki"
}

// Response 200
{ "enabled": false, "disabled_at": 1718000050 }
```

短语错误返回 `{ "enabled": true, "error": "phrase_mismatch" }`；挑战过期或未知返回 `{ "enabled": true, "error": "challenge_expired" }`。

#### `POST /v1/xijian/safety/audit/export`

导出审计日志（legacy `state.audits` + 统一 `state.safety_audit_log`）为 JSONL 文件，返回 `file_id`，可通过 `GET /v1/files/{file_id}` 下载。

```json
{ "file_id": "file_abc", "bytes": 4096 }
```

### 3.6 会话与上下文

#### `POST /v1/xijian/sessions`

创建新会话。

#### `POST /v1/xijian/sessions/{session_id}/messages`

追加消息到会话（也可直接走 `/v1/chat/completions`）。

#### `GET /v1/xijian/sessions/{session_id}/messages`

#### `DELETE /v1/xijian/sessions/{session_id}`

### 3.7 设置与偏好

#### `GET /v1/xijian/settings`

#### `PATCH /v1/xijian/settings`

#### `GET /v1/xijian/settings/permissions`

查询当前用户已授予的系统权限状态。

### 3.8 资源与导入（资源包系统 §B）

资源包（pack）是固实 7z（或 zip）归档，根级包含 ``manifest.json`` 与
``characters/<id>/``、``worlds/<id>/``、``memories/<id>/`` 目录。
DevKit 导出的归档即为合法资源包（一套格式两用）。安装后的包解压到
``<存储根>/packs/<package_id>/``。

#### `GET /v1/xijian/packs`

列出所有已安装资源包。返回包记录数组（JSON array；每项含
``package_id``/``kind``/``name``/``version``/``path``/``manifest``/``loaded``）。

#### `GET /v1/xijian/packs/{package_id}`

查询单个资源包详情；不存在返回 404（``pack_not_found``）。

#### `POST /v1/xijian/packs/install`

安装资源包。两种入参：

* multipart 表单，``file`` 字段上传归档；
* JSON ``{"path": "/abs/path/to/pack.7z"}``（服务端本地路径，用于目录投放）。

校验 manifest、解压到 ``packs/`` 并加载进运行时；成功 201 返回包记录。

#### `DELETE /v1/xijian/packs/{package_id}`

卸载资源包（移除运行时记录并删除包目录）；不存在返回 404。

#### `POST /v1/xijian/packs/rescan`

重新扫描 ``packs/`` 目录（例如用户手动把归档拖进目录后触发），
返回 ``{installed: n, errors: [...]}``。

#### `POST /v1/xijian/resources/import`

异步导入资源包（zip / 7z）。请求体：

```json
{ "name": "显示名", "kind": "character", "path": "/abs/path/to/pack.7z" }
```

也支持 ``"file_id"``（先经 ``POST /v1/files`` 上传，再引用归档 id）。
后台线程完成解压与加载；202 返回 ``{job_id, status: "queued"}``。

#### `GET /v1/xijian/resources/imports/{job_id}`

查询导入任务；完成时 ``status="completed"`` 且带 ``package_id`` 与结果摘要，
失败时 ``status="failed"`` 且带 ``error`` 描述。

---

### 3.9 剧情（Plot）

C3 剧情运行时：加载 DevKit 导出的剧情设计（节点/边图），在世界内推进剧情。

#### `GET /v1/xijian/plots/designs`

列出 DevKit 工作目录下所有可用剧情设计（分页）。

**响应 200**：``{object, data: [{plot_id, title, node_count, edge_count}], has_more}``。

#### `GET /v1/xijian/plots/designs/{plot_id}`

读取剧情设计详情（节点/边/初始变量）。

**错误码**：404 ``plot_not_found``。

#### `GET /v1/xijian/plots/designs/{plot_id}/nodes`

列出剧情设计的全部节点。

#### `GET /v1/xijian/plots/designs/{plot_id}/edges`

列出剧情设计的全部边。

#### `POST /v1/xijian/plots/runtime`

创建并启动一个剧情运行时实例。

**请求体**：

```json
{
  "plot_id": "plot_demo",
  "world_id": "world_modern_tokyo",
  "initial_variables": {"player_name": "阿月"}
}
```

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `plot_id` | string | ✅ | 剧情设计 id |
| `world_id` | string | ✅ | 目标世界 |
| `initial_variables` | object | 否 | 初始剧情变量 |

**响应 201**：运行时记录（含 ``runtime_id``、``current_node_id``、``status``）。

**错误码**：400 ``missing_fields`` / ``plot_error``；404 ``world_not_found``。

#### `GET /v1/xijian/plots/runtime`

列出运行时实例。查询参数：``world_id`` / ``plot_id`` / ``status``（可选）。

#### `GET /v1/xijian/plots/runtime/{runtime_id}`

读取运行时状态。**错误码**：404 ``runtime_not_found``。

#### `POST /v1/xijian/plots/runtime/{runtime_id}/advance`

推进剧情（执行当前节点、流转边）。

**请求体**（可选）：

```json
{ "choose_edge_id": "edge_1_to_2" }
```

**错误码**：400 ``plot_error``。

#### `POST /v1/xijian/plots/runtime/{runtime_id}/pause` / `.../resume`

暂停 / 恢复剧情。**错误码**：400 ``plot_error``。

#### `DELETE /v1/xijian/plots/runtime/{runtime_id}`

删除运行时实例；成功 204。**错误码**：404 ``runtime_not_found``。

#### `GET /v1/xijian/plots/runtime/{runtime_id}/nodes`

列出运行时所有节点（含 ``is_current`` / ``is_completed`` / ``is_unlocked`` 标记）。

#### `GET /v1/xijian/plots/runtime/{runtime_id}/nodes/{node_id}`

读取单节点详情。**错误码**：404 ``node_not_found``。

#### `GET /v1/xijian/plots/runtime/{runtime_id}/edges`

列出运行时所有边；可选 ``?node_id=`` 过滤出边。

#### `POST /v1/xijian/plots/scheduler/tick`

手动触发一次剧情触发器评估（仅开发环境，``XIJIAN_DEV=1`` 时可用）。

**请求体**（可选）：``{"world_id": "..."}``（缺省评估全世界）。

**错误码**：404 ``route_not_found``（非 dev 环境）。

---

### 3.10 DevKit 预览与测试

桥接独立 DevKit 进程的保存目录：发现、预览、加载角色/世界（C0 本地预览循环）。

#### `GET /v1/xijian/devkit/status`

检查 DevKit 目录可用性并返回摘要：

```json
{
  "available": true,
  "directory": "/Users/.../DevKit",
  "character_count": 3,
  "world_count": 1,
  "loaded_characters": 2,
  "loaded_worlds": 0
}
```

不可用时返回 ``available: false`` 与 ``error``。

#### `GET /v1/xijian/devkit/characters`

列出 DevKit 目录下的角色。查询参数：``loaded_only=true`` 只返回已加载角色。
每条记录附 ``_loaded`` / ``_persona_exists`` / ``_memories_count`` 预览元数据。

#### `GET /v1/xijian/devkit/characters/{id}`

返回单个角色的完整预览数据。**错误码**：404 ``not_found``。

#### `POST /v1/xijian/devkit/characters/{id}/load`

将角色加载进核心运行时（重复加载会替换旧记录）。

**响应 200**：``{ok: true, data: <加载后的角色记录>}``。**错误码**：404 ``not_found``。

#### `DELETE /v1/xijian/devkit/characters/{id}`（或 `POST .../unload`）

从核心运行时卸载角色。**错误码**：404 ``not_found``。

#### `GET /v1/xijian/devkit/worlds`

列出 DevKit 目录下的世界（附 ``_loaded`` / ``_doc_exists`` / ``_config_exists``）。

#### `GET /v1/xijian/devkit/worlds/{id}`

返回单个世界的完整预览数据。**错误码**：404 ``not_found``。

#### `POST /v1/xijian/devkit/worlds/{id}/load`

将世界加载进核心运行时。**错误码**：404 ``not_found``。

#### `DELETE /v1/xijian/devkit/worlds/{id}`（或 `POST .../unload`）

从核心运行时卸载世界。**错误码**：404 ``not_found``。

#### `GET /v1/xijian/devkit/loaded`

返回全部已加载项，按 ``characters`` / ``worlds`` 分组。

#### `POST /v1/xijian/devkit/reload`

重新扫描 DevKit 目录并重新加载。查询参数：``kind=character|world``（可选，缺省两者）。

**响应 200**：``{ok: true, reloaded: {characters: n, worlds: n}}``。**错误码**：400 ``invalid_kind``。

#### `GET /v1/xijian/devkit/{kind}` / `GET /v1/xijian/devkit/{kind}/{id}`

通用访问别名；``kind`` 为 ``characters`` 或 ``worlds``。**错误码**：400 ``invalid_kind``；404 ``not_found``。

---

### 3.11 实时通话（A6）

通话会话状态机：``idle → ringing → active → ended``。所有 ``call_id`` 不存在时返回 404
（``voice_call_not_found``）；状态迁移非法时返回 400 ``voice_call_error``。

#### `GET /v1/xijian/voice-calls`

列出通话记录（分页，按 ``started_at`` 倒序）。查询参数：``character_id`` / ``status`` / ``direction``（可选过滤）。

#### `POST /v1/xijian/voice-calls`

创建通话会话（状态 ``idle``）。

**请求体**：

```json
{
  "character_id": "char_yuki",
  "direction": "user_initiated",   // user_initiated | character_initiated
  "user_id": "local_user"
}
```

**响应 201**：通话记录（含 ``call_id``、``status: "idle"``、``direction``）。
**错误码**：400 ``missing_character_id``。

#### `GET /v1/xijian/voice-calls/{call_id}`

查询通话详情。**错误码**：404 ``voice_call_not_found``。

#### `POST /v1/xijian/voice-calls/{call_id}/ring`

发起来电（``idle → ringing``）。**错误码**：404；400 ``voice_call_error``（状态不允许）。

#### `POST /v1/xijian/voice-calls/{call_id}/accept`

接听（``ringing → active``）。

#### `POST /v1/xijian/voice-calls/{call_id}/reject`

拒接（→ ``ended``）。

#### `POST /v1/xijian/voice-calls/{call_id}/end`

结束通话（→ ``ended``）。

#### `GET /v1/xijian/voice-calls/{call_id}/events`

通话事件流（按时间正序）。查询参数：``kind``（可选过滤）、``limit``（默认 100）。

#### `POST /v1/xijian/voice-calls/{call_id}/speech`

向通话投喂一段用户语音（STT → AI 回复 → TTS 全双工循环）。请求体 ``audio_base64``（原始音频字节的
base64）与 ``text``（显式文本，跳过 STT）二选一；可选 ``language``、``synchronous``（默认 false，后台线程执行）。

**响应 200**：``{ok: true, turn, user_text, reply, interrupted_previous}``。
**错误码**：400 ``invalid_audio`` / ``invalid_text`` / ``voice_call_error``（通话未激活）；
STT 后端不可用时不抛异常，返回 **503** ``{ok: false, error: ...}``（通话可继续）。

#### `POST /v1/xijian/voice-calls/{call_id}/barge-in`

置位/清除打断标志（AC-3：新语音到达时中断当前 TTS 播放）。请求体 ``{"active": true}``（缺省 true）。

#### `POST /v1/xijian/voice-calls/{call_id}/song`

歌唱 stub（DiffSinger 风格）。请求体 ``lyrics``（必填）、``voice_part``（默认 ``lead``）、可选
``melody`` / ``midi_path``。**错误码**：400 ``missing_lyrics`` / ``voice_call_error``。

> WS 推送：通话状态迁移时广播 ``call.state_changed``，追加事件时广播 ``call.event``。
> 注意：语音链路（STT/TTS）依赖已配置的模型后端，默认出厂配置下返回 503（见 §2.4）。

---

### 3.12 主动发起（A7）

角色主动联系用户的消息/来电动作队列（状态机 ``pending → sent → accepted|declined|ignored``）。

#### `GET /v1/xijian/initiated-actions`

列出动作（分页）。查询参数：``character_id`` / ``kind``（``message``|``voice_call``） / ``status`` / ``user_response``。

#### `POST /v1/xijian/initiated-actions`

手动创建一条主动发起动作。

**请求体**：

```json
{ "character_id": "char_yuki", "kind": "message", "payload": {"text": "在吗？"} }
```

**响应 201**：动作记录（``action_id``、``status: "pending"``）。**错误码**：400 ``missing_character_id``。

#### `GET /v1/xijian/initiated-actions/{action_id}`

查询动作详情。**错误码**：404 ``initiated_action_not_found``。

#### `POST /v1/xijian/initiated-actions/{action_id}/respond`

用户回应。请求体 ``user_response`` 必填，取 ``accepted`` | ``declined`` | ``ignored``。
拒绝（``declined``）时在 stub 层触发 AC-2「角色理解」记忆回写。**错误码**：400 ``invalid_user_response``。

#### `POST /v1/xijian/initiated-actions/scan`

手动触发一次触发器扫描（后台 tick 线程亦会周期性执行）。返回 ``{scanned: true, created_count, created: [...]}``。

#### `GET /v1/xijian/initiated-actions/notifications`

全局 + 各角色通知权限摘要（AC-3）。

#### `PATCH /v1/xijian/initiated-actions/notifications`

修改全局通知策略（``enabled`` / ``max_per_hour`` / ``cooldown_seconds`` 等）。

#### `GET/PATCH /v1/xijian/initiated-actions/notifications/{character_id}`

单角色通知权限的读取/修改。

> WS 推送：创建动作时广播 ``character.initiated_action``，用户回应时广播 ``character.initiated_response``。
> 推送依赖 WebSocket 通道，waitress 服务器下不可用（见 §5.6）。

---

### 3.13 桌面宠物与动态壁纸（A8）

桌宠（pet）与动态壁纸（wallpaper）的 CRUD + 激活状态 + 动作审计日志 + 桌面客户端执行循环。

#### `GET /v1/xijian/desktop/pets` / `POST /v1/xijian/desktop/pets`

列出（分页；``?character_id=`` / ``?is_active=`` 过滤）/ 创建桌宠。创建请求体：
``character_id``（必填）、``can_fly``、``can_interact``、``spawn_x`` / ``spawn_y``（float）、``is_active``、``name``。
**响应 201**：宠物记录。**错误码**：400 ``missing_character_id``。

#### `GET /v1/xijian/desktop/pets/{pet_id}` / `PATCH ...` / `DELETE ...`

查询 / 修改（``update_pet`` 宽容合并）/ 删除桌宠。**错误码**：404 ``desktop_pet_not_found``。

#### `POST /v1/xijian/desktop/pets/{pet_id}/activate` / `.../deactivate`

显示 / 隐藏桌宠。

#### `GET /v1/xijian/desktop/wallpapers` / `POST /v1/xijian/desktop/wallpapers`

列出 / 创建动态壁纸。创建请求体：``character_id``（必填）、``world_id``、``env_settings``、
``can_layout``（默认 true）、``is_active``（默认 false）。**响应 201**。**错误码**：400 ``missing_character_id``。

#### `GET /v1/xijian/desktop/wallpapers/{wallpaper_id}` / `PATCH ...` / `DELETE ...`

查询 / 修改 / 删除壁纸。**错误码**：404 ``wallpaper_not_found``。

#### `POST /v1/xijian/desktop/wallpapers/{wallpaper_id}/activate` / `.../deactivate`

激活壁纸会使同角色的桌宠自动隐藏（AC-4），反之为互斥语义。

#### `GET /v1/xijian/desktop/actions`

全局桌宠动作审计日志。查询参数：``action_kind``、``limit``（默认 100）。

#### `GET /v1/xijian/desktop/pets/{pet_id}/actions`

单宠物动作日志。**错误码**：404 ``desktop_pet_not_found``。

#### `POST /v1/xijian/desktop/pets/{pet_id}/actions`

派发 / 记录一次桌宠动作（AC-2 审计）。请求体 ``action_kind``（必填）、``payload``。**响应 201**。
**错误码**：400 ``missing_action_kind``。

#### 桌面客户端执行循环（A5.2 标记缺口）

* `GET /v1/xijian/mcp/pending` — 轮询待执行动作队列（``?status=``、``?limit=`` 默认 50、``?claim=1`` 顺手认领）
* `GET /v1/xijian/mcp/pending/{action_id}` — 单条查询；**错误码**：404 ``pending_action_not_found``
* `POST /v1/xijian/mcp/pending/{action_id}/claim` — 认领执行（``pending → claimed``）
* `POST /v1/xijian/mcp/pending/{action_id}/result` — 回写结果，``status`` 取 ``executed`` | ``failed``，
  可选 ``pet_id``；AC-4 门控（壁纸模式禁写）在 stub 层强制。**错误码**：400 ``invalid_result_status``

> WS 推送：``desktop_pet.event`` / ``wallpaper.event`` / ``desktop_pet.action`` / ``desktop_pet.pending``
> 为 stub 层尽力而为的广播（不保证送达）。

---

### 3.14 旧数据迁移（v2.10）

旧 ``~/.xijian`` 目录首次启动自动迁移到 CORE_ROOT（``~/Library/Application Support/XiJian/Core``）：
幂等（写 ``.migrated_from_xijian`` 标记）、非破坏（旧目录永不删除）、冲突感知（同名不同内容不覆盖，记入冲突清单）。

#### `GET /v1/xijian/migration/status`

迁移状态：``legacy_exists`` / ``migrated`` / ``items`` / ``conflicts`` / ``error``。

#### `GET /v1/xijian/migration/conflicts`

已记录的冲突清单（``{conflicts: [...]}``）。

#### `POST /v1/xijian/migration/resolve`

解决一条冲突。请求体 ``conflict_id``（必填）、``keep`` 取 ``legacy``（保留旧版本）| ``new``（保留新位置版本）。

**错误码**：400 ``missing_conflict_id`` / ``invalid_keep`` / ``resolve_failed``；404 ``conflict_not_found``。

---

### 3.15 场景生成（A4.1）

已触发世界事件实例（``instance_id``）附带场景记录的读取与（重新）生成。

#### `GET /v1/xijian/generation/scene/{instance_id}`

读取事件实例附带的场景记录。**错误码**：404 ``event_scene_not_found``（实例不存在或不需要场景）。

#### `POST /v1/xijian/generation/scene/{instance_id}/generate`

（重新）生成实例场景。尽力而为：核心图像后端不可用时**降级为占位场景**（``status: "placeholder"``，AC-2），
不报错。**错误码**：404 ``event_scene_not_found``。

> 通用生成中断仍走 §4.2 的 `POST /v1/xijian/generation/abort`。

---

### 3.16 手动备份与受保护模块（A1.1）

A1.1 用户管理与自动备份的 HTTP 面：受保护模块注册表 + 手动备份/恢复。备份文件为 zstd 压缩的
``{character_id}_{ISO8601}_v{n}.bak``，单角色最多保留 10 个版本（AC-3）。

#### `GET /v1/protected-modules`

受保护模块注册表（分页）。查询参数：``character_id``（可选，返回该角色关联视图）。
出厂注册 4 个模块：``memory_entries`` / ``character_documents`` / ``world_documents`` / ``safety_snapshots``。

#### `GET /v1/characters/{character_id}/protected-modules`

单角色的受保护模块关联视图（含 ``auto_backup`` / ``last_backup_at``）。

#### `PATCH /v1/characters/{character_id}/protected-modules`

切换角色的自动备份开关。请求体 ``module_name``（必填）、``enabled``（或 ``auto_backup``，默认 true）。
**错误码**：400 ``missing_module_name`` / ``unknown_protected_module``。

#### `POST /v1/backups`

触发一次手动备份。

**请求体**：

```json
{
  "character_id": "char_yuki",
  "scope": "all",        // all | memory_only | state_only | doc_only
  "created_by": "user"   // user | system
}
```

**响应 201**：备份记录（``backup_id``、``file_path``、``size_bytes``、``created_at``）。
**错误码**：400 ``missing_character_id`` / ``invalid_created_by`` / ``invalid_scope``；404 ``character_not_found``。

#### `GET /v1/backups`

列出备份（分页）。查询参数：``character_id``、``limit``（默认 50）。

#### `GET /v1/backups/{backup_id}`

查询备份详情。**错误码**：404 ``backup_not_found``。

#### `DELETE /v1/backups/{backup_id}`

删除备份。**错误码**：404 ``backup_not_found``。

#### `POST /v1/backups/{backup_id}/restore`

恢复备份（US-A1.1-03：可恢复至任意角色，可只恢复部分切片）。请求体可选 ``scope``、
``target_character_id``。**错误码**：404 ``backup_not_found``；400 ``invalid_scope``。

---

## 4. 取消与中断

### 4.1 流式请求的取消

#### `POST /v1/chat/abort`

```json
// Request
{ "request_id": "req_8f3a2b1c" }

// Response 204（有活跃流时中止成功，无 body）
// Response 200（无活跃流：幂等，返回 {"aborted": false, "request_id": ...}）
```

服务端立即停止对应生成，释放上下文。任何 SSE/NDJSON 连接收到 `event: abort` 块后关闭：

**SSE**：

```
data: {"id":"chatcmpl-9f8a","choices":[{"finish_reason":"abort"}]}

data: [DONE]
```

**NDJSON**：

```
{"id":"chatcmpl-9f8a","choices":[{"finish_reason":"abort"}]}
```

### 4.2 隙间扩展的中断

#### `POST /v1/xijian/generation/abort`

中断任意进行中的生成任务（包括 TTS、图像、视频）。

```json
{
  "request_id": "gen_abc",
  "scope": "all"   // all | chat | tts | image | video
}
```

### 4.3 应急快捷键

UI 层注册的全局应急快捷键（macOS 默认 `⌃⌥⌘.`，Win/Linux 可配置）触发后，UI 端调用上述 abort 端点并清空队列。

---

## 5. WebSocket 通道（`/v1/ws`）

### 5.1 用途

- 角色主动消息推送（应用在前台时的实时通知）
- 长任务进度推送（视频生成、模型加载、记忆整理）
- UI ↔ 服务端双向控制信号（如「角色打断」、「桌宠紧急暂停」）

### 5.2 连接

```
ws://127.0.0.1:{port}/v1/ws
Sec-WebSocket-Protocol: xijian.v1, bearer.<token>
```

或连接后第一帧发送：

```json
{"type": "auth", "token": "<bearer-token>"}
```

### 5.3 消息格式

```json
{
  "id": "evt_001",
  "type": "character.proactive_message",
  "ts": 1718000000,
  "data": { ... }
}
```

### 5.4 事件类型

| `type`                          | 方向          | 说明                       |
| ------------------------------- | ------------- | -------------------------- |
| `hello`                         | server→client | 连接建立成功               |
| `ping` / `pong`                 | 双向          | 心跳（30s 间隔）           |
| `auth.ok` / `auth.failed`       | server→client | 鉴权结果                   |
| `character.proactive_message`   | server→client | 角色主动消息                |
| `character.emotion_changed`     | server→client | 角色情感变化                |
| `character.action_triggered`    | server→client | 角色动作触发                |
| `world.event_occurred`          | server→client | 世界事件                    |
| `world.state_changed`           | server→client | 世界状态变化                |
| `memory.consolidated`           | server→client | 记忆整理完成                |
| `protection.alert`              | server→client | 保护模块告警                |
| `generation.progress`           | server→client | 异步生成进度（视频/图像）   |
| `generation.completed`          | server→client | 异步生成完成                |
| `generation.failed`             | server→client | 异步生成失败                |
| `desktop_pet.emergency_pause`   | client→server | 桌宠紧急暂停                |
| `desktop_pet.command`           | client→server | 桌宠控制指令                |
| `client.cancel_request`         | client→server | 客户端主动取消某 request_id  |

### 5.5 示例

**Server → Client**：

```json
{
  "id": "evt_001",
  "type": "character.proactive_message",
  "ts": 1718000000,
  "data": {
    "character_id": "char_yuki",
    "message": "你今天还好吗？",
    "suggested_replies": ["我很好", "有点累"],
    "emotion": "concerned"
  }
}
```

**Client → Server**：

```json
{
  "id": "cmd_001",
  "type": "client.cancel_request",
  "ts": 1718000000,
  "data": { "request_id": "req_8f3a2b1c" }
}
```

### 5.6 服务器驱动要求（重要）

`/v1/ws` 依赖 `flask-sock`（simple-websocket），**只有 werkzeug 服务器驱动支持 WebSocket**：

* **默认（`--server auto` / `--server werkzeug`）**：解析为 werkzeug 多线程服务器，`/v1/ws` 可用
  （hello / auth.ok / ping→pong / client.cancel_request ack / 事件广播实测通过）。
* **`--server waitress`（或 config.toml ``[server] driver = "waitress"``）**：性能更好但**不支持
  WebSocket**——`/v1/ws` 握手即返回 500 `internal_error`（`Cannot obtain socket from WSGI
  environment`）。

依赖 WS 的功能（A6 实时通话状态推送、A7 主动发起通知、UI 双向控制）在 waitress 模式下不可用；
生产部署如需 WS，请使用默认 werkzeug 驱动或通过独立通道提供。

---

## 6. 内容分级与保护联动

### 6.1 NSFW 分级

互动、TTS 文本、图像、视频均带 `nsfw_level`：

- `safe` —— 默认放行
- `soft` —— 默认隐藏，需 `xijian.nsfw_allowed=true` 或在设置中开启
- `explicit` —— 同 `soft`，额外记录审计

### 6.2 保护模块联动

所有出站内容（OAI 响应、自有响应）都经过保护模块 `guard_output` 过滤。被拦截的内容不返回客户端，写入审计日志（§3.5）。

---

## 7. 速率限制与配额

本地单用户场景下默认不限流，但保留 OAI 兼容头：

- `X-RateLimit-Limit-Requests`
- `X-RateLimit-Remaining-Requests`
- `X-RateLimit-Limit-Tokens`
- `X-RateLimit-Remaining-Tokens`
- `X-RateLimit-Reset-Requests`
- `X-RateLimit-Reset-Tokens`

可通过 `POST /v1/xijian/settings` 中的 `rate_limit` 字段开启软限流（保护硬件）。

---

## 8. 版本与兼容

- 路径前缀带版本（当前 `/v1`）。破坏性变更走 `/v2`，旧版保留至少 6 个月。
- 响应中带 `X-XiJian-API-Version: 1.0.0`。
- 客户端可通过 `GET /v1`（根信息）查询服务端版本与能力集。
  > 注意：`GET /v1` 的 `capabilities` 是**协议能力声明**（静态列表），不代表对应模型后端已配置——
  > 生成类能力（embeddings/audio/images/videos 生成）未配置后端时实际请求仍返回 503（见 §2.3）。

---

## 9. 安全约束

- **仅监听 `127.0.0.1`**，绝不允许 `0.0.0.0`。
- **Token 通过临时文件传递**：统一临时目录 `~/Library/Application Support/XiJian/tmp/xijian-<pid>.token`，
  文件权限 `0600`，API 进程启动时读取后立即 `unlink`。
- **实际端口通过文件传递**：`~/Library/Application Support/XiJian/tmp/xijian-<pid>.port`，
  与 token 文件同目录（开发与打包模式一致）；端口被占用自动换端口后，客户端以该文件为准。
- **CORS 默认禁用**；如调试需要可临时开启（仅 `127.0.0.1`）。
- **所有写操作走保护模块审计**。
- **不缓存敏感响应**（NSFW 内容、保护日志等）。

---

## 10. 调试与排错

### 10.1 常用工具

```bash
# macOS
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/v1/models | jq

# 流式 SSE
curl -N -H "Authorization: Bearer $TOKEN" -H "Accept: text/event-stream" \
  -X POST http://127.0.0.1:$PORT/v1/chat/completions \
  -d '{"model":"...","messages":[{"role":"user","content":"hi"}],"stream":true}'

# WebSocket 调试
wscat -c "ws://127.0.0.1:$PORT/v1/ws" \
  -H "Authorization: Bearer $TOKEN"
```

### 10.2 错误排查

- **401**：检查 token 文件是否正确写入并被读取
- **404 + model not found**：`GET /v1/models/{id}` 对未注册 id 返回 404；但 **chat 请求中的未知 model_id 不会 404**——
  它走「自由 model_id」回退路径：按配置的默认 chat 后端链（`[backends.chat] default/fallbacks`）选择后端；
  mock 后端存在（开发/测试配置）时返回 200，否则 503 `backend_unavailable`（见 §2.2 与 AIBackend.md §6.2）
- **403 + protection_error**：触发保护模块，查看审计日志
- **503 + backend unavailable**：生成类端点（embeddings/TTS/STT/image/video 生成）未配置模型后端；
  查看 `/v1/xijian/safety/audit` 与进程日志

### 10.3 日志位置

- API 服务日志：默认仅输出到 stderr；设置 ``XIJIAN_LOG_FILE`` 后写入指定文件
  （开发与打包模式统一为 ``~/Library/Application Support/XiJian/Core/logs/xijian-api.log``）
- 安全模块审计日志：进程内 ``state.safety_audit_log`` + ``state.audits``，经 ``/v1/xijian/safety/audit`` 查询
- AI backend 日志：随 backend 子进程 stderr 输出（若设置 ``XIJIAN_LOG_FILE`` 则与其同文件）

---

## 11. 后续扩展方向

- **MCP 桥接**：通过 `/v1/xijian/mcp/*` 暴露 MCP 工具，让外部 MCP-aware Agent 调用隙间角色
- **多用户**（理论上）：当前为单用户，多用户需要加会话隔离 + 资源配额
- **远程调用**：未来如需开放远程访问，必须额外加 OAuth + TLS + 双向认证，**当前协议不允许**

---

_本文档随协议演进持续更新；任何破坏性变更必须先开 RFC 流程。_