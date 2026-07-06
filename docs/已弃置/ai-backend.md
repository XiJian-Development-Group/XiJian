# AI-Backend.md — AI Backend 实现指南

> 本文档面向需要**实现或扩展** AI backend（MLX / GGUF / 未来可能的云端）的开发者。
> 阅读对象：负责 backend 适配层（`core/ai/backends/<name>/`）的开发者。
> API 协议见 [api.md](./api.md)；整体架构见 [Dev.md §4.1](./Dev.md)。

---

## 0. 为什么需要这一层

隙间业务代码（`core/services/`）只与 **AI 抽象层**（`core/ai/base.py`）对话，**禁止**直接 import 具体 backend。这意味着：

- 新增一个 backend（MLX、GGUF、未来云端）只需要实现 ABC，不动业务。
- 跨平台切换（macOS MLX ↔ Win/Linux GGUF）对业务层透明。
- backend 可以独立升级、独立测试、独立打 hotfix。

**强约束**（CI 检查）：

```bash
# 这条命令必须返回空结果
rg -l "from core.ai.backends" core/services/
```

---

## 1. 抽象接口

### 1.1 核心 ABC

完整的 ABC 接口代码块较长，见仓库 `core/ai/base.py`。下面是要点速览：

**核心 dataclass**：

```python
@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None

@dataclass
class ChatResponse:
    id: str
    model: str
    created: int
    choices: list[ChatChoice]
    usage: ChatUsage | None = None

@dataclass
class GenerationParams:
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None
    stop: Sequence[str] | None = None
    # ... 其余 OAI 参数
```

**错误层级**：

```python
class BackendError(Exception):
    code = "backend_error"
    recoverable = True

class BackendUnavailable(BackendError): code = "backend_unavailable"
class ModelNotFound(BackendError):      code = "model_not_found";       recoverable = False
class ModelNotLoaded(BackendError):     code = "model_not_loaded"
class ContextLengthExceeded(BackendError): code = "context_length_exceeded"; recoverable = False
class GenerationAborted(BackendError):  code = "generation_aborted";    recoverable = False
class GuardBlocked(BackendError):       code = "protection_blocked";    recoverable = False
```

**6 个 ABC 接口**：

```python
class ChatBackend(ABC):
    name: str
    def list_models(self) -> list[dict]: ...
    def load(self, model_id, **kwargs) -> None: ...
    def unload(self) -> None: ...
    def is_loaded(self) -> bool: ...
    def chat(self, messages, params, *, stream=False, abort_signal=None) -> ...: ...
    def tokenize(self, text) -> list[int]: ...
    def health(self) -> dict: ...

class EmbeddingBackend(ABC):
    def list_models(self) -> list[dict]: ...
    def load(self, model_id, **kwargs) -> None: ...
    def embed(self, texts, *, model_id=None) -> list[list[float]]: ...
    def dimensions(self) -> int: ...

class TTSBackend(ABC):
    def list_voices(self) -> list[dict]: ...
    def synth(self, text, *, voice, response_format="mp3", speed=1.0,
              emotion=None, voice_clone_ref=None, abort_signal=None) -> bytes: ...

class STTBackend(ABC):
    def transcribe(self, audio, *, language=None, prompt=None,
                   response_format="json") -> str | dict: ...

class ImageGenBackend(ABC):
    def generate(self, prompt, *, model_id, n=1, size="1024x1024",
                 negative_prompt=None, seed=None, abort_signal=None) -> list[bytes]: ...

class VideoGenBackend(ABC):
    def submit(self, prompt, *, model_id, input_reference=None,
               seconds=4, size="1280x720", fps=24, seed=None,
               progress_callback=None, abort_signal=None) -> str: ...
    def poll(self, task_id) -> dict: ...
```

### 1.2 AbortSignal

backend 必须支持**协作式取消**（区别于线程强杀）：

```python
# core/ai/abort.py
import threading

class AbortSignal:
    def __init__(self):
        self._ev = threading.Event()
    def set(self): self._ev.set()
    def is_set(self): return self._ev.is_set()
    def raise_if_aborted(self):
        if self._ev.is_set():
            raise GenerationAborted("aborted by client")
    def reset(self): self._ev.clear()
```

API 网关层在收到 abort 请求时调用 `abort_signal.set()`，backend 在下一个 token 生成前 check 并抛出。

---

## 2. 注册与选择

### 2.1 registry

```python
# core/ai/registry.py
import sys, os
from typing import Type
from .base import (ChatBackend, EmbeddingBackend, TTSBackend, STTBackend,
                   ImageGenBackend, VideoGenBackend)

_chat_backends: dict[str, Type[ChatBackend]] = {}
# ... 其余 5 个 dict

def register_chat(name):
    def deco(cls):
        _chat_backends[name] = cls
        return cls
    return deco

def get_chat_backend(name=None) -> ChatBackend:
    if name is None:
        name = _default_chat_backend()
    return _chat_backends[name]()

def _default_chat_backend() -> str:
    env = os.environ.get("XIJIAN_AI_BACKEND")
    if env:
        return env
    if sys.platform == "darwin":
        return "mlx"
    if sys.platform in ("win32", "linux"):
        return "gguf"
    raise RuntimeError(f"No AI backend available for {sys.platform}")
```

### 2.2 backend 自注册

每个 backend 在自己的 `__init__.py` 末尾调用 `register_*`：

```python
# core/ai/backends/mlx/__init__.py
from .chat import MLXChatBackend
from core.ai.registry import register_chat

register_chat("mlx")(MLXChatBackend)
```

### 2.3 通过环境变量覆盖

允许的高级用法：

- `XIJIAN_AI_BACKEND=gguf` 在 macOS 上强制使用 GGUF（开发用）
- `XIJIAN_AI_BACKEND=remote` 未来用于云端 backend（详见 §6）

---

## 3. MLX Backend（macOS）

### 3.1 依赖

```toml
# core/pyproject.toml
[project.optional-dependencies]
mlx = [
    "mlx>=0.20",
    "mlx-lm>=0.20",
    "mlx-whisper>=0.1",   # STT
]
```

### 3.2 模型目录结构

```
~/.xijian/models/
├── chat/
│   ├── qwen2.5-7b-mlx-4bit/
│   │   ├── config.json
│   │   ├── weights.safetensors
│   │   ├── tokenizer.json
│   │   └── xijian_meta.json    # 隙间扩展元信息
│   └── ...
├── embed/
├── tts/
├── stt/
├── image/
└── video/
```

`xijian_meta.json` 示例：

```json
{
  "xijian": {
    "backend": "mlx",
    "family": "qwen2.5",
    "size_b": 7.0,
    "quant": "4bit",
    "context_length": 32768,
    "min_ram_gb": 8,
    "tags": ["chat", "zh", "en"]
  }
}
```

### 3.3 MLXChatBackend 关键实现点

```python
# core/ai/backends/mlx/chat.py —— 简化示意
from pathlib import Path
import mlx.core as mx
from mlx_lm import load, generate, stream_generate

from core.ai.base import (
    ChatBackend, ChatMessage, ChatResponse, ChatChoice,
    GenerationParams, ModelNotLoaded, GenerationAborted
)
from core.ai.registry import register_chat


@register_chat("mlx")
class MLXChatBackend(ChatBackend):
    name = "mlx"

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._model_id = None
```

**加载 / 卸载**：

```python
def load(self, model_id: str, **kwargs) -> None:
    path = self._resolve_path(model_id)
    self._model, self._tokenizer = load(str(path))
    self._model_id = model_id

def unload(self) -> None:
    self._model = None
    self._tokenizer = None
    self._model_id = None
    mx.metal.clear_cache()  # 释放显存

def is_loaded(self) -> bool:
    return self._model is not None
```

**chat 入口**：

```python
def chat(self, messages, params, *, stream=False, abort_signal=None):
    if not self.is_loaded():
        raise ModelNotLoaded("no model loaded")

    prompt = self._tokenizer.apply_chat_template(
        [m.__dict__ for m in messages],
        tokenize=False,
        add_generation_prompt=True,
    )

    if stream:
        return self._stream(prompt, params, abort_signal)
    return self._blocking(prompt, params, abort_signal)
```

**阻塞生成**：

```python
def _blocking(self, prompt, params, abort_signal):
    text = generate(
        self._model, self._tokenizer,
        prompt=prompt,
        max_tokens=params.max_tokens or 1024,
        temp=params.temperature,
        top_p=params.top_p,
    )
    if abort_signal and abort_signal.is_set():
        raise GenerationAborted()
    return ChatResponse(
        id=f"chatcmpl-{uuid4().hex[:12]}",
        model=self._model_id,
        created=int(time.time()),
        choices=[ChatChoice(
            message=ChatMessage(role="assistant", content=text),
            finish_reason="stop",
        )],
    )
```

**流式生成**：

```python
def _stream(self, prompt, params, abort_signal):
    for chunk in stream_generate(
        self._model, self._tokenizer,
        prompt=prompt,
        max_tokens=params.max_tokens or 1024,
        temp=params.temperature,
    ):
        if abort_signal and abort_signal.is_set():
            raise GenerationAborted()
        yield ChatResponse(
            id=f"chatcmpl-{uuid4().hex[:12]}",
            model=self._model_id,
            created=int(time.time()),
            choices=[ChatChoice(
                delta=ChatMessage(role="assistant", content=chunk.text),
            )],
        )
    # 结束帧
    yield ChatResponse(
        id=f"chatcmpl-{uuid4().hex[:12]}",
        model=self._model_id,
        created=int(time.time()),
        choices=[ChatChoice(
            delta=ChatMessage(role="assistant", content=""),
            finish_reason="stop",
        )],
    )
```
### 3.4 注意事项

- **量化**：MLX 支持 4-bit / 8-bit，模型目录需明确量化位宽
- **context length**：超过上下文长度抛 `ContextLengthExceeded`
- **metal cache**：unload 时调用 `mx.metal.clear_cache()` 释放显存
- **单实例**：一个进程同时只加载一个 chat 模型（避免显存爆炸）

---

## 4. GGUF Backend（Windows / Linux）

### 4.1 依赖

```toml
[project.optional-dependencies]
gguf = [
    "llama-cpp-python>=0.3",
    # 或使用 Ollama 的 HTTP API:
    # "ollama>=0.4",
]
```

### 4.2 模型目录

```
~/.xijian/models/
├── chat/
│   └── qwen2.5-7b-gguf-q4km/
│       ├── model-q4_k_m.gguf
│       ├── tokenizer.json   # 可选（GGUF 自带）
│       └── xijian_meta.json
├── embed/
├── tts/
├── stt/
├── image/
└── video/
```

### 4.3 GGUFChatBackend 实现要点

```python
# core/ai/backends/gguf/chat.py —— 简化示意
from llama_cpp import Llama

from core.ai.base import (
    ChatBackend, ChatMessage, ChatResponse, ChatChoice,
    GenerationParams, ModelNotLoaded, GenerationAborted
)
from core.ai.registry import register_chat


@register_chat("gguf")
class GGUFChatBackend(ChatBackend):
    name = "gguf"

    def __init__(self):
        self._llama: Llama | None = None
        self._model_id: str | None = None

    def load(self, model_id: str, **kwargs) -> None:
        path = self._resolve_path(model_id) / "model-q4_k_m.gguf"
        ctx = kwargs.get("context_length", 8192)
        n_gpu_layers = kwargs.get("gpu_layers", -1)  # -1 = 全部
        self._llama = Llama(
            model_path=str(path),
            n_ctx=ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
        self._model_id = model_id

    def chat(self, messages, params, *, stream=False, abort_signal=None):
        if self._llama is None:
            raise ModelNotLoaded()
        kwargs = dict(
            messages=[m.__dict__ for m in messages],
            temperature=params.temperature,
            top_p=params.top_p,
            max_tokens=params.max_tokens or 1024,
            stop=list(params.stop or []),
            stream=stream,
        )
        if stream:
            return self._stream(kwargs, abort_signal)
        return self._blocking(kwargs, abort_signal)

    def _blocking(self, kwargs, abort_signal):
        out = self._llama.create_chat_completion(**kwargs)
        if abort_signal and abort_signal.is_set():
            raise GenerationAborted()
        return self._parse_oai_response(out)

    def _stream(self, kwargs, abort_signal):
        for chunk in self._llama.create_chat_completion(**kwargs):
            if abort_signal and abort_signal.is_set():
                raise GenerationAborted()
            yield self._parse_oai_chunk(chunk)
```

### 4.4 Ollama 变体

如果选择通过 Ollama 跑 GGUF（不直接用 llama-cpp-python）：

```python
# core/ai/backends/gguf/ollama_chat.py
import httpx

from core.ai.registry import register_chat
from core.ai.base import (
    ChatBackend, ChatMessage, ChatResponse, ChatChoice,
    GenerationParams, ModelNotLoaded, GenerationAborted
)


@register_chat("gguf-ollama")
class OllamaChatBackend(ChatBackend):
    """通过本地 Ollama daemon 跑 GGUF。"""
    name = "gguf-ollama"
    BASE = "http://127.0.0.1:11434"

    def load(self, model_id: str, **kwargs) -> None:
        # Ollama 模型通过 ollama pull 拉取，这里只记录
        self._model_id = model_id

    def chat(self, messages, params, *, stream=False, abort_signal=None):
        # Ollama 兼容 OAI 端点，直接复用
        url = f"{self.BASE}/v1/chat/completions"
        # ... httpx 调用与流式处理
        ...
```

### 4.5 注意事项

- **GPU 层数**：`gpu_layers=-1` 让 llama.cpp 自动判断（Metal / CUDA / Vulkan 全部加载到 GPU）
- **内存映射**：默认 `use_mmap=True` 适合大模型；小模型可以关掉以提速
- **KV cache**：长对话场景下注意 OOM，必要时显式设置 `n_ctx`
- **量化命名**：GGUF 量化后缀（`q4_k_m` / `q5_k_s` / `q8_0`）需要在 `xijian_meta.json` 中标清楚

---

## 5. 错误模型与传播

### 5.1 backend → service 层

backend 抛出 `BackendError` 子类，service 层捕获并转换为业务异常：

```python
# core/services/character/service.py
from core.ai.base import (
    ChatBackend, BackendUnavailable, ModelNotLoaded,
    ContextLengthExceeded, GenerationAborted, GuardBlocked
)
from core.ai.registry import get_chat_backend

class CharacterService:
    def reply(self, character_id, user_input, history, abort_signal=None):
        try:
            backend = get_chat_backend()
            response = backend.chat(
                messages, params,
                stream=False, abort_signal=abort_signal,
            )
        except GenerationAborted:
            # 客户端取消，不报错，返回空
            return None
        except ModelNotLoaded:
            raise ServiceError("character_not_ready", "请先加载模型")
        except ContextLengthExceeded:
            raise ServiceError("context_too_long", "对话过长，请开启新一轮")
        except BackendUnavailable as e:
            raise ServiceError("ai_unavailable", str(e), retry_after=5)
```

### 5.2 service → API 层

API 层把 `ServiceError` 转成 OAI / JSON-RPC 错误响应：

```python
# core/api/errors.py
from flask import jsonify, request
from core.services.errors import ServiceError

def error_response(status: int, message: str, type_: str, **extra):
    if "application/json-rpc" in request.headers.get("Accept", ""):
        return jsonify({
            "jsonrpc": "2.0",
            "id": getattr(request, "xijian_request_id", None),
            "error": {
                "code": _to_jsonrpc_code(type_),
                "message": message,
                "data": extra,
            },
        }), status, {"Content-Type": "application/json"}
    return jsonify({
        "error": {"message": message, "type": type_, **extra}
    }), status


def _to_jsonrpc_code(type_: str) -> int:
    return {
        "invalid_request_error": -32602,
        "server_error": -32603,
        "not_found_error": -32001,
        "permission_error": -32003,
        "rate_limit_error": -32004,
        "backend_unavailable": -32005,
        "protection_error": -32010,
        "content_filter": -32011,
    }.get(type_, -32603)
```

### 5.3 错误对照表

| Backend 异常             | HTTP status | OAI type                | JSON-RPC code |
| ------------------------ | ----------- | ----------------------- | ------------- |
| `BackendUnavailable`     | 503         | `server_error`          | -32005        |
| `ModelNotFound`          | 404         | `invalid_request_error` | -32001        |
| `ModelNotLoaded`         | 409         | `invalid_request_error` | -32002        |
| `ContextLengthExceeded`  | 400         | `invalid_request_error` | -32602        |
| `GenerationAborted`      | 204 / 200   | (无 error，正常结束)    | (无)          |
| `GuardBlocked`           | 403         | `protection_error`      | -32010        |

---

## 6. 未来扩展

### 6.1 云端 backend（暂不在路线图）

如果未来引入云端 backend（OpenAI / Anthropic / 自建），实现需满足：

- 实现完整 `ChatBackend` 等接口
- 注册为 `cloud-openai` / `cloud-anthropic` 等
- 通过 `XIJIAN_AI_BACKEND=cloud-openai` 选择
- **强制走保护模块**：所有出站内容过 `guard_output`，保护日志写入本地
- 用户必须显式授权云端调用（设置项 + API key 由用户自填）

### 6.2 MCP 桥接

`core/ai/backends/mcp/` 暴露隙间角色为 MCP tool，让外部 Agent 通过 MCP 协议调用。**这是单向暴露（隙间→外部）**，不是让外部控制隙间。

### 6.3 多 backend 组合

例如「主对话用 MLX，embedding 用远程 API」——通过 `XIJIAN_BACKEND_CHAT=mlx` + `XIJIAN_BACKEND_EMBED=cloud-bge` 分别配置。
