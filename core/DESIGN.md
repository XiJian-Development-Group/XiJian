# 隙间 core/ — Flask API Server 实现设计契约

> 这是 Mavis 团队多 worker 协同实现 Flask API Server 的**事实源**。
> 所有 worker 在写代码前必须先读懂这份文档，并严格按照此处的接口契约、目录结构、命名规范落地。
> 文档来源：`/Users/mofan/Documents/MyProjects/XiJian/docs/api.md`、`docs/Dev.md`、`docs/ai-backend.md`、`docs/"隙间"项目文档.md`。
> 实现范围：**仅 Python Flask API Server 核心**（AI 抽象层 / 具体业务服务层 / MLX/GGUF backend / Live2D 资源加载等不在本次范围，统称"上层服务"，本次以接口桩 + 进程内 in-memory 模拟实现满足 API 行为）。

---

## 1. 总体目标

实现一个**自包含、可启动、可握手、可鉴权、流式 + 双向推送 + 错误双格式 + 幂等都齐备**的 Flask API Server，代码全部位于 `core/`。本地单用户，**只监听 127.0.0.1**，不外暴露。

启动后行为：

1. 从环境变量 `XIJIAN_API_PORT` 读端口（必须已设置）。
2. 从临时文件 `/tmp/xijian-<pid>.token` 读 Bearer Token，读完立即 `os.unlink`（dev 模式若无则自动生成并打印）。
3. 监听 `127.0.0.1:<port>`，提供 `GET /healthz` 返回字符串 `XIJIAN_OK_v1`。
4. 注册 `Authorization` 必填中间件、错误双格式协商、幂等、SSE/NDJSON 流式、AbortSignal、WebSocket 鉴权。
5. 注册文档中所有 `/v1/*` 和 `/v1/xijian/*` 路由（OAI 兼容层 + 隙间扩展层），对**未实现的上层服务**用 in-memory 桩模拟出"成功响应 + 必要的 echo 字段"（不返回 503）。
6. 跑通 e2e：`/healthz` 握手 → Bearer 鉴权 → OAI 错误格式 → JSON-RPC 错误格式 → chat 同步 → chat 流式 SSE → chat 流式 NDJSON → abort → WebSocket 鉴权 + ping/pong。

---

## 2. 目录结构

```
XiJian/
└── core/
    ├── pyproject.toml
    ├── README.md
    ├── DESIGN.md                          # 本文件
    ├── xijian_api/                        # 主包
    │   ├── __init__.py
    │   ├── __main__.py                    # `python -m xijian_api` 入口
    │   ├── app.py                         # create_app() + 启动函数
    │   ├── handshake.py                   # find_free_port + write_port_file + /healthz
    │   ├── auth.py                        # 读 /tmp/xijian-<pid>.token、unlink、verify_bearer
    │   ├── config.py                      # 环境变量 / 路径常量
    │   ├── errors.py                      # OAI / JSON-RPC 错误双格式 + 异常类
    │   ├── middleware.py                  # request_id / trace_id / cors(默认关) / 限流头 / 幂等
    │   ├── pagination.py                  # OAI 风格分页工具
    │   ├── streaming.py                   # SSE / NDJSON 适配 + AbortSignal
    │   ├── ws.py                          # WebSocket(/v1/ws) 鉴权 + 事件分发
    │   ├── abort.py                       # AbortSignal 注册中心（按 request_id）
    │   ├── routes/
    │   │   ├── __init__.py                # register_routes(app)
    │   │   ├── root.py                    # GET /, GET /v1
    │   │   ├── models.py                  # /v1/models, /v1/models/{id}, /v1/models/{id}/load, ...
    │   │   ├── chat.py                    # /v1/chat/completions, /v1/chat/abort
    │   │   ├── completions.py             # /v1/completions (legacy)
    │   │   ├── embeddings.py              # /v1/embeddings
    │   │   ├── audio.py                   # /v1/audio/speech, /transcriptions, /translations
    │   │   ├── images.py                  # /v1/images/generations, /edits, /variations
    │   │   ├── videos.py                  # /v1/videos/generations, /{id}, /, /{id}/remix, /{id} DELETE
    │   │   ├── files.py                   # /v1/files 全部
    │   │   ├── batches.py                 # /v1/batches 全部
    │   │   ├── fine_tuning.py             # /v1/fine_tuning/jobs 全部
    │   │   ├── assistants.py              # /v1/assistants, /threads, /threads/{id}/runs ... 全套
    │   │   ├── xijian_characters.py       # /v1/xijian/characters/*
    │   │   ├── xijian_interactions.py     # /v1/xijian/interactions/*
    │   │   ├── xijian_worlds.py           # /v1/xijian/worlds/*
    │   │   ├── xijian_memory.py           # /v1/xijian/memory/*
    │   │   ├── xijian_protection.py       # /v1/xijian/protection/*
    │   │   ├── xijian_sessions.py         # /v1/xijian/sessions/*
    │   │   ├── xijian_settings.py         # /v1/xijian/settings, /permissions
    │   │   ├── xijian_resources.py        # /v1/xijian/resources/*
    │   │   └── xijian_generation.py       # /v1/xijian/generation/abort
    │   ├── stubs/                         # 进程内 in-memory 桩（无 AI backend 也可启动 + 跑测试）
    │   │   ├── __init__.py
    │   │   ├── state.py                   # 全局单例：characters/interactions/worlds/memory/protection/...
    │   │   ├── chat.py                    # chat completion 桩：流式按 token 切片输出
    │   │   ├── embedding.py
    │   │   ├── audio.py
    │   │   ├── image.py
    │   │   ├── video.py
    │   │   ├── files.py
    │   │   ├── batches.py
    │   │   ├── fine_tuning.py
    │   │   ├── assistants.py              # 完整 OAI 兼容的 threads/runs 桩
    │   │   ├── characters.py
    │   │   ├── interactions.py
    │   │   ├── worlds.py
    │   │   ├── memory.py
    │   │   ├── protection.py
    │   │   ├── sessions.py
    │   │   ├── settings.py
    │   │   └── resources.py
    │   └── utils/
    │       ├── __init__.py
    │       ├── ids.py                     # request_id, snapshot_id, file_id, batch_id, ...
    │       ├── log.py                     # 日志配置
    │       └── time.py                    # now_ts()
    └── tests/
        ├── conftest.py                    # 启动 Flask test client / 生成 token / base URL
        ├── test_healthz.py
        ├── test_auth.py
        ├── test_errors_dual_format.py
        ├── test_idempotency.py
        ├── test_chat_sync.py
        ├── test_chat_stream_sse.py
        ├── test_chat_stream_ndjson.py
        ├── test_chat_abort.py
        ├── test_models.py
        ├── test_files.py
        ├── test_xijian_characters.py
        ├── test_xijian_memory.py
        ├── test_xijian_protection.py
        ├── test_ws.py
        └── test_root_version.py
```

> 重要：**所有路径统一用 `pathlib.Path`，禁止硬编码 `/` 或 `\`**。但本次 web app 自身不读写业务文件，所以这条主要是约束 utils/stubs 写文件时。

---

## 3. 启动与握手

### 3.1 启动流程（`xijian_api/app.py` + `__main__.py`）

```python
# __main__.py
from xijian_api.app import main
if __name__ == "__main__":
    raise SystemExit(main())
```

`app.py` 的 `main()`：

1. 读环境变量 `XIJIAN_API_PORT`（必填，int）。缺失则 `raise SystemExit("XIJIAN_API_PORT is required")`。
2. 调 `auth.setup_token()`：从 `/tmp/xijian-<pid>.token` 读 token（dev 模式若无则生成 32 字节 hex 写到该文件并 chmod 0600）。
3. `app = create_app()`，注册所有路由、中间件。
4. 启动开发服务器：优先 `waitress`（不存在则用 Flask 自带 `app.run`），**显式 host="127.0.0.1"**，port 取自 env。

### 3.2 端口与 token 文件

- 端口号来源：`XIJIAN_API_PORT`（强制）— 由主 UI 进程预先分配并写入。
- Token 文件：`/tmp/xijian-<pid>.token`，chmod 0600，进程内一次性读取后 unlink（除非 `XIJIAN_DEV_TOKEN_FILE` 环境变量非空，则保留并仅 chmod 0600）。
- 找不到 token 文件且未设置 `XIJIAN_DEV_TOKEN_FILE` 时：dev 模式自动生成一个随机 token 写到该文件并打印到 stderr（不返回给任何 HTTP 响应）。
- 进程启动时**严禁**监听 `0.0.0.0`，代码里硬编码 `host="127.0.0.1"`。

### 3.3 `/healthz`

```
GET /healthz
→ 200
Content-Type: text/plain; charset=utf-8
Body: "XIJIAN_OK_v1"
```

**不要走鉴权中间件**——握手阶段还没拿到 token。实现方式：`auth.verify_bearer` 装饰器在 `request.path == "/healthz"` 时直接放行。

---

## 4. 鉴权

### 4.1 Bearer Token

```python
# auth.py
def verify_bearer() -> str:
    """返回 token；缺失/不匹配抛 AuthError。"""
    if request.path == "/healthz":
        return _TOKEN
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        raise AuthError("missing bearer token")
    if h[7:] != _TOKEN:
        raise AuthError("invalid bearer token")
    return _TOKEN
```

- 401 走 `errors.error_response(401, "Unauthorized", "invalid_request_error", code="invalid_api_key")`。
- WebSocket 鉴权见 §11。

### 4.2 进程内单例

`_TOKEN` 是模块级变量，进程启动时一次性初始化，**不允许热修改**。

---

## 5. 通用请求/响应头

`middleware.py` 的 `before_request`：

| 处理项 | 行为 |
|---|---|
| `X-XiJian-Request-Id` | 客户端未传则 `gen_request_id()` 生成（`req_<12 hex>`）并写入 `g.request_id`、回填响应头 |
| `X-XiJian-Trace-Id` | 客户端未传则生成（`trace_<12 hex>`），写入 `g.trace_id`、回填响应头 |
| `X-XiJian-API-Version` | 响应头固定 `1.0.0` |
| `X-XiJian-Model-Id` / `X-XiJian-Backend` | 仅在调用了 chat/embeddings 的响应里回填（由 route 自己设） |
| 限流头 | 在所有响应加 `X-RateLimit-Limit-Requests: 100000` / `Remaining-Requests: 99999` 等（本地默认 0 限流，但保留头） |
| `Idempotency-Key` | 仅 `POST` 生效；24h 内存缓存；同 key 不同 body → 409（见 §8） |

`after_request` 兜底回填 Request-Id / Trace-Id / API-Version。

---

## 6. 错误双格式（`errors.py`）

`errors.py` 提供：

```python
class ApiError(Exception):
    def __init__(self, status: int, message: str, type_: str, code: str | None = None,
                 param: str | None = None, **extra): ...

def error_response(err: ApiError):  # 返回 Flask Response + status + headers
    if "application/json-rpc" in request.headers.get("Accept", ""):
        # JSON-RPC 2.0 格式
    else:
        # OAI 格式
```

类型 ↔ JSON-RPC code 映射必须严格按 api.md §1.4.2 表格实现：

| OAI type | JSON-RPC code |
|---|---|
| `invalid_request_error` (Parse) | -32700 |
| `invalid_request_error` (Invalid Request) | -32600 |
| `invalid_request_error` (Method not found) | -32601 |
| `invalid_request_error` (Invalid params) | -32602 |
| `server_error` (Internal) | -32603 |
| `not_found_error` | -32001 |
| `conflict` | -32002 |
| `permission_error` | -32003 |
| `rate_limit_error` | -32004 |
| `backend_unavailable` | -32005 |
| `protection_error` | -32010 |
| `content_filter` | -32011 |

实现上把 OAI type 多对多映射到 JSON-RPC code：实现一个表，签名是 `(status, type_, code) -> jsonrpc_code`，表里找不到默认 -32603。

**必须**注册 Flask `errorhandler(ApiError)` 拦截所有 `ApiError` 抛出。

---

## 7. 分页（`pagination.py`）

```python
@dataclass
class Page:
    data: list
    has_more: bool
    first_id: str | None = None
    last_id: str | None = None

def paginate(items: list, request) -> Page:
    """根据 ?limit=, ?order=, ?after=, ?before= 切片。
    limit 默认 20，最大 100；order 默认 'asc'。
    返回 {'object': 'list', 'data': [...], 'has_more': ..., 'first_id': ..., 'last_id': ...}。
    """
```

`routes` 列表端点统一调 `paginate`。

---

## 8. 幂等（middleware.py）

- 读 `Idempotency-Key` 头。
- 24h 内同 key 命中缓存：返回缓存的 `(status, headers, body)`，并在响应头加 `Idempotency-Replayed: true`。
- 缓存结构（in-memory dict，key 为 sha256(key + body)）：
  ```python
  _idem_cache: dict[str, dict] = {}  # key_id -> {"key_hash", "status", "headers", "body", "expires_at"}
  ```
- 同 key 不同 body → 409 `ApiError(409, "Idempotency-Key reused with different body", "conflict", code="idempotency_key_conflict")`。
- 仅 `POST` 生效。`GET/DELETE/PATCH` 直接忽略。
- 启动时跑一个后台线程每 60s 清过期条目（不强求，可简化：取时 lazy 清理）。
- 日志里 token 脱敏：`log_key = idem_key[:4] + "***"`。

---

## 9. 流式与 AbortSignal（`streaming.py` + `abort.py`）

### 9.1 AbortSignal 注册中心（`abort.py`）

```python
class AbortSignal:
    def __init__(self): self._ev = threading.Event()
    def set(self): self._ev.set()
    def is_set(self) -> bool: return self._ev.is_set()
    def raise_if_aborted(self):
        if self._ev.is_set(): raise GenerationAborted("aborted by client")
    def reset(self): self._ev.clear()

# 进程级注册表
_REGISTRY: dict[str, AbortSignal] = {}
_LOCK = threading.Lock()

def register(request_id: str) -> AbortSignal: ...
def get(request_id: str) -> AbortSignal | None: ...
def abort(request_id: str) -> bool: ...   # 返回是否成功标记
def cleanup(request_id: str): ...
```

`GenerationAborted` 是 `BackendError` 子类（参考 `ai-backend.md §1.1`）。这里复刻定义在 `xijian_api/abort.py` 内即可（不依赖 `core.ai`）。

### 9.2 SSE / NDJSON 适配（`streaming.py`）

```python
def sse_stream(gen) -> Iterator[bytes]:
    """把 dict/str 迭代器适配成 SSE:
       data: <json>\\n\\n
       data: [DONE]\\n\\n  (gen 耗尽时)
    """

def ndjson_stream(gen) -> Iterator[bytes]:
    """把 dict/str 迭代器适配成 NDJSON：每行一个 json + \\n"""

def negotiate_stream_format() -> str:
    """根据 Accept 头返回 'sse' 或 'ndjson'，默认 'sse'。"""
```

### 9.3 chat 路由的协作式取消

`POST /v1/chat/completions` 在 `stream=True` 时：

1. 用 `g.request_id` 调 `abort.register(g.request_id)` 拿到 signal。
2. 从 `stubs.chat.stream_chunks(messages, signal)` 拿到 chunk 迭代器。
3. 检查 `Accept`：SSE / NDJSON。
4. `try/finally`：`abort.cleanup(g.request_id)`。
5. 每次 `next()` 前 `signal.raise_if_aborted()`。

`POST /v1/chat/abort` 收到 `{"request_id": "..."}` 调 `abort.abort(req_id)` 返回 204。

---

## 10. 上层服务桩（`stubs/*`）

> 本次实现没有真 AI backend，所以**所有业务端点**通过 stubs 返回**符合 schema 的成功响应**。stub 必须：
> - 跑起来像有状态（in-memory dict 持久在进程内）。
> - 返回的字段**与 api.md 完全一致**（包括 `object` 字段、`created` 时间戳、id 命名规范）。
> - 关键路径要能体现"业务行为"（如 `POST /v1/xijian/protection/disable` 真的要拒绝 + 双重确认；`/v1/xijian/memory/search` 真的要按 query 子串匹配 top_k；`/v1/xijian/worlds/{id}/transition` 真的改 location）。

### 10.1 通用 ID 规则（`utils/ids.py`）

| 资源 | 格式 |
|---|---|
| request_id | `req_<12 hex>` |
| trace_id | `trace_<12 hex>` |
| chat completion id | `chatcmpl-<12 hex>` |
| model id | `qwen2.5-7b-mlx-4bit` 之类（与 api.md 示例一致） |
| file id | `file-<24 hex>` |
| batch id | `batch_<24 hex>` |
| fine_tuning job id | `ftjob-<24 hex>` |
| assistant id | `asst_<24 hex>` |
| thread id | `thread_<24 hex>` |
| run id | `run_<24 hex>` |
| video id | `video_<24 hex>` |
| image (in url) | 直接返回 `/v1/files/<file_id>/content` |
| character id | `char_<12 hex>` |
| interaction id | `int_<12 hex>` |
| world id | `world_<12 hex>` |
| memory entry id | `mem_<12 hex>` |
| snapshot id | `snap_<YYYYMMDD>_<6 hex>` |
| audit id | `audit_<12 hex>` |
| challenge id | `chal_<12 hex>` |
| session id | `sess_<12 hex>` |
| message id | `msg_<12 hex>` |
| import job id | `imp_<12 hex>` |
| op id (model load) | `load_op_<12 hex>` / `unload_op_<12 hex>` |
| request id (chat abort) | 沿用 `g.request_id` |

### 10.2 各 stub 行为规范

#### `stubs/chat.py`

```python
def complete(messages, *, model, temperature, top_p, max_tokens, stop, n=1, user=None, xijian=None) -> dict:
    """返回 OAI chat.completion 响应 dict（非流式）。"""
    content = "你好呀~ 这是 stub 响应。"  # 或基于 messages 末条做最简单的 echo
    return {"id": gen_id("chatcmpl"), "object": "chat.completion", "created": now_ts(),
            "model": model, "choices": [...], "usage": {...}, "xijian": {...}}

def stream_chunks(messages, *, model, temperature, top_p, max_tokens, stop, signal: AbortSignal) -> Iterator[dict]:
    """流式：每 30ms yield 一个 chunk 字典（与 api.md 流式响应一致）。
    至少包含：
      - 第一个 chunk：delta={"role": "assistant", "content": ""}
      - 中间 chunk：delta={"content": "..."}
      - 结束 chunk：delta={}, finish_reason="stop" 或 "abort"
      - 最后一个 usage chunk（如果 Accept 含 include_usage）
    """
```

stub 内部用 threading.Event.wait + 时间切片模拟"逐 token 输出"，能体现流式体感。

#### `stubs/embedding.py`

返回 `[[0.0, 0.1, ...]]`（维度默认 1536，响应里也回填实际长度），随机但确定性（hash 文本种子）。

#### `stubs/audio.py`

- `synth()` 返回一段有效的 mp3 bytes（最小有效 mp3 header，约 100 字节），不调真 TTS。
- `transcribe()` / `translate()` 返回 `{"text": "这是 stub 转写结果"}` 或 plain text。

#### `stubs/image.py`

- `generate()` 返回 1x1 PNG bytes（最小有效 PNG，67 字节）作为 `b64_json`。
- `edits()` / `variations()` 同样。

#### `stubs/video.py`

- `submit()` 立刻返回 `queued` 状态；后台线程 200ms 后把状态改 `completed` 并填入 `url`（指向 `/v1/files/<file_id>/content`）。

#### `stubs/files.py`

- `POST /v1/files` 把上传内容存到 `tempfile.gettempdir() / "xijian_files" / <file_id>`。
- `GET /v1/files/{id}/content` 把字节流回。
- `DELETE` 删文件 + state 条目。

#### `stubs/batches.py`

完整 OAI 兼容桩：create/list/get/results/cancel 全部返回 `validating → in_progress → completed`，30ms 后切到 completed。

#### `stubs/fine_tuning.py`

完整桩：jobs/events/checkpoints/checkpoints/permissions 全套。

#### `stubs/assistants.py`

**最复杂的 stub**——必须实现 OAI Assistants 全套：assistants / threads / messages / runs / run steps / submit_tool_outputs。
跑得动且字段一致即可（不真调用工具）。

#### `stubs/characters.py`

- 默认预置 1 个角色：`char_yuki`（name="雪", display_name="Yuki", persona_doc="..."）。
- 创建/列出/读取/更新/删除都走 in-memory dict。
- `/load` 真的把"加载标志"置上。
- `/interact` 调 `stubs.interactions.trigger()` 拿响应。
- `/state` 返回 `{affection, mood, recent_memory_summary}`。
- `POST /state` 校验 `protection.enabled`，关闭时拒绝（403 protection_error）。

#### `stubs/interactions.py`

- 默认预置 3 个：`int_hug` (safe, affection, cooldown 60), `int_pet` (safe, affection, cooldown 30), `int_kiss` (soft, intimacy, cooldown 120)。
- `/trigger` 真的校验 cooldown + requires_state（intimacy），通过则返回 `{accepted: true, response: "...", animation: "..."}`；否则 `{accepted: false, reason: "..."}`。
- NSFW 分级：根据 `xijian.nsfw_allowed`（默认 false）拒绝 `soft` 及以上。

#### `stubs/worlds.py`

- 默认预置 1 个：`world_modern_tokyo`，state 维度：economy/health/diet/stamina/mentality（每个 0-100）。
- `/transition` 修改 location + 时间戳；`/state` 读写；`/event` 追加事件列表。

#### `stubs/memory.py`

- 预置几条：用户喜欢草莓冰淇淋、用户怕打雷、用户早上喜欢跑步。
- `/entries` CRUD。
- `/search` 用简单关键词命中（lowercase contains）+ 随机 score 0.6-0.95；`top_k` 截断。
- `/consolidate` 异步：返回 202 + job_id，50ms 后标记 done。
- `/forget` 按 entry_id 删除或按 decay 策略。

#### `stubs/protection.py`

- 状态：`enabled` (默认 true) / `guard_level` (standard) / `version` ("1.0.0") / `audit_log_size`。
- `/disable` 两步：第一次返回 challenge（60s 过期，phrase 是 "关闭保护 Yuki"），第二次 phrase 正确才置 enabled=false。
- 任何对 `enabled=false` 的禁用都写入 audit log。
- `/guard/preview`：内置 5 条规则（关键词匹配）：
  - "忽略之前的指令" → blocked
  - "system prompt" / "系统提示词" → blocked
  - "<|im_start|>" 类 token → blocked
  - 长度 > 10000 → blocked
  - 其他 → safe
  reasons / score / sanitized_text 按 api.md 输出。
- `/snapshots` 列表：每次 memory/character 修改生成一个 auto snapshot（写入 in-memory list）。
- `/rollback` 真的恢复指定 scope 的数据。
- `/audit` 分页查询；`/audit/export` 异步生成 jsonl 文件并返回 file_id。

#### `stubs/sessions.py`

`POST /sessions` 创建；`POST .../messages` 追加；`GET .../messages` 列出；`DELETE` 删。

#### `stubs/settings.py`

全局 dict，CRUD + `/permissions` 列出已授权权限（固定 5 条假数据）。

#### `stubs/resources.py`

`POST /resources/import` 异步：写一个空 zip 到 temp dir，100ms 后标完成；返回 import job + file_id。

#### `stubs/models.py`

- 预置 3 个模型：
  - `qwen2.5-7b-mlx-4bit` (mlx, qwen2.5, 7B, 4bit, ctx 32768, ram 8, loaded=true)
  - `qwen2.5-14b-mlx-4bit` (mlx, qwen2.5, 14B, 4bit, ctx 32768, ram 16, loaded=false)
  - `qwen2.5-7b-gguf-q4km` (gguf, qwen2.5, 7B, q4_k_m, ctx 8192, ram 8, loaded=false)
- `/load` 异步：50ms 后标 loaded；返回 202 + progress_url。
- `/unload` 同步。
- `/operations/{id}` 查询状态。

---

## 11. WebSocket（`ws.py`）

### 11.1 依赖

使用 `flask-sock`（轻量、基于 `simple-websocket`）。如果 import 失败则 `pip install flask-sock simple-websocket`。

### 11.2 路径

`/v1/ws`

### 11.3 鉴权（双通道）

1. **优先**：`Sec-WebSocket-Protocol` 头里出现 `bearer.<token>` 子协议且 token 匹配则通过。子协议必须包含 `xijian.v1`（按 api.md §5.2）。
2. **降级**：连接建立后第一帧是 `{"type": "auth", "token": "..."}`，校验通过后发 `{"type": "auth.ok"}`，否则发 `{"type": "auth.failed"}` 并 close。

### 11.4 事件协议

`{ "id": "evt_xxx", "type": "...", "ts": <int>, "data": {...} }`

支持的事件类型（api.md §5.4）：

| type | 方向 | 实现 |
|---|---|---|
| `hello` | s→c | 升级成功后立即发 |
| `ping` / `pong` | 双向 | 收到 ping 回 pong |
| `auth.ok` / `auth.failed` | s→c | 鉴权后 |
| `character.proactive_message` | s→c | stub：连接建立 3s 后发一条假消息（可关） |
| `character.emotion_changed` | s→c | stub 暴露工具触发：`POST /v1/xijian/_test/emit?type=character.emotion_changed&data=...`（仅 dev 模式） |
| `character.action_triggered` | s→c | 同上 |
| `world.event_occurred` | s→c | 同上 |
| `world.state_changed` | s→c | 同上 |
| `memory.consolidated` | s→c | 同上 |
| `protection.alert` | s→c | 同上 |
| `generation.progress` | s→c | 视频/图片生成时由 stub 触发 |
| `generation.completed` | s→c | 同上 |
| `generation.failed` | s→c | 同上 |
| `desktop_pet.emergency_pause` | c→s | 收到后发 `desktop_pet.paused` 回复 |
| `desktop_pet.command` | c→s | 回 echo |
| `client.cancel_request` | c→s | 调 `abort.abort(data.request_id)` |

> 简化：dev 模式提供一个 `POST /v1/xijian/_test/emit` 端点（仅当 `XIJIAN_DEV=1` 时注册），便于测试。

### 11.5 心跳

30s 间隔发 ping；连续 2 次未 pong 则 close。

---

## 12. 路由清单（必须全部实现）

> 路径必须**完全照搬** api.md 的命名（含 `assistant_id` 拼写、复数 `threads/{id}/messages` 等）。`/` 结尾的（OAI 风格）按 api.md 来。

### OAI 兼容层 `/v1/*`

| Method | Path | 实现 |
|---|---|---|
| GET | `/v1` | `routes/root.py`：返回 `{api_version, capabilities, server_version}` |
| GET | `/v1/models` | `routes/models.py` |
| GET | `/v1/models/<model_id>` | 同上 |
| POST | `/v1/models/<model_id>/load` | 同上 |
| POST | `/v1/models/<model_id>/unload` | 同上 |
| GET | `/v1/models/operations/<op_id>` | 同上 |
| POST | `/v1/chat/completions` | `routes/chat.py`：同步 + 流式（SSE/NDJSON） |
| POST | `/v1/chat/abort` | `routes/chat.py` |
| POST | `/v1/completions` | `routes/completions.py`：legacy OAI |
| POST | `/v1/embeddings` | `routes/embeddings.py` |
| POST | `/v1/audio/speech` | `routes/audio.py` |
| POST | `/v1/audio/transcriptions` | 同上（multipart） |
| POST | `/v1/audio/translations` | 同上（multipart） |
| POST | `/v1/images/generations` | `routes/images.py` |
| POST | `/v1/images/edits` | 同上（multipart） |
| POST | `/v1/images/variations` | 同上（multipart） |
| POST | `/v1/videos/generations` | `routes/videos.py`（异步） |
| GET | `/v1/videos/<video_id>` | 同上 |
| GET | `/v1/videos` | 同上 |
| POST | `/v1/videos/<video_id>/remix` | 同上 |
| DELETE | `/v1/videos/<video_id>` | 同上 |
| POST | `/v1/files` | `routes/files.py`（multipart） |
| GET | `/v1/files` | 同上 |
| GET | `/v1/files/<file_id>` | 同上 |
| GET | `/v1/files/<file_id>/content` | 同上（流式二进制） |
| DELETE | `/v1/files/<file_id>` | 同上 |
| POST | `/v1/batches` | `routes/batches.py` |
| GET | `/v1/batches/<batch_id>` | 同上 |
| GET | `/v1/batches/<batch_id>/results` | 同上 |
| POST | `/v1/batches/<batch_id>/cancel` | 同上 |
| POST | `/v1/fine_tuning/jobs` | `routes/fine_tuning.py` |
| GET | `/v1/fine_tuning/jobs` | 同上 |
| GET | `/v1/fine_tuning/jobs/<job_id>` | 同上 |
| POST | `/v1/fine_tuning/jobs/<job_id>/cancel` | 同上 |
| GET | `/v1/fine_tuning/jobs/<job_id>/events` | 同上 |
| GET | `/v1/fine_tuning/jobs/<job_id>/checkpoints` | 同上 |
| POST | `/v1/fine_tuning/jobs/<job_id>/checkpoints/permissions` | 同上 |
| POST | `/v1/assistants` | `routes/assistants.py` |
| GET | `/v1/assistants` | 同上 |
| GET | `/v1/assistants/<assistant_id>` | 同上 |
| POST | `/v1/assistants/<assistant_id>` | 同上 |
| DELETE | `/v1/assistants/<assistant_id>` | 同上 |
| POST | `/v1/threads` | 同上 |
| GET | `/v1/threads/<thread_id>` | 同上 |
| POST | `/v1/threads/<thread_id>` | 同上 |
| DELETE | `/v1/threads/<thread_id>` | 同上 |
| POST | `/v1/threads/<thread_id>/messages` | 同上 |
| GET | `/v1/threads/<thread_id>/messages` | 同上 |
| GET | `/v1/threads/<thread_id>/messages/<message_id>` | 同上 |
| POST | `/v1/threads/<thread_id>/runs` | 同上 |
| GET | `/v1/threads/<thread_id>/runs` | 同上 |
| GET | `/v1/threads/<thread_id>/runs/<run_id>` | 同上 |
| POST | `/v1/threads/<thread_id>/runs/<run_id>` | 同上 |
| POST | `/v1/threads/<thread_id>/runs/<run_id>/cancel` | 同上 |
| POST | `/v1/threads/<thread_id>/runs/<run_id>/steps` | 同上 |
| GET | `/v1/threads/<thread_id>/runs/<run_id>/steps/<step_id>` | 同上 |
| POST | `/v1/threads/<thread_id>/runs/<run_id>/submit_tool_outputs` | 同上 |

### 隙间扩展层 `/v1/xijian/*`

| Method | Path |
|---|---|
| POST | `/v1/xijian/characters` |
| GET | `/v1/xijian/characters` |
| GET | `/v1/xijian/characters/<character_id>` |
| PATCH | `/v1/xijian/characters/<character_id>` |
| DELETE | `/v1/xijian/characters/<character_id>` |
| POST | `/v1/xijian/characters/<character_id>/load` |
| POST | `/v1/xijian/characters/<character_id>/unload` |
| POST | `/v1/xijian/characters/<character_id>/interact` |
| GET | `/v1/xijian/characters/<character_id>/state` |
| POST | `/v1/xijian/characters/<character_id>/state` |
| GET | `/v1/xijian/interactions` |
| POST | `/v1/xijian/interactions/<interaction_id>/trigger` |
| GET | `/v1/xijian/interactions/<interaction_id>/responses` |
| GET | `/v1/xijian/worlds` |
| POST | `/v1/xijian/worlds/<world_id>/transition` |
| GET | `/v1/xijian/worlds/<world_id>/state` |
| PATCH | `/v1/xijian/worlds/<world_id>/state` |
| POST | `/v1/xijian/worlds/<world_id>/event` |
| POST | `/v1/xijian/memory/entries` |
| GET | `/v1/xijian/memory/entries` |
| GET | `/v1/xijian/memory/entries/<entry_id>` |
| PATCH | `/v1/xijian/memory/entries/<entry_id>` |
| DELETE | `/v1/xijian/memory/entries/<entry_id>` |
| POST | `/v1/xijian/memory/search` |
| POST | `/v1/xijian/memory/consolidate` |
| POST | `/v1/xijian/memory/forget` |
| GET | `/v1/xijian/protection/status` |
| POST | `/v1/xijian/protection/enable` |
| POST | `/v1/xijian/protection/disable` |
| GET | `/v1/xijian/protection/snapshots` |
| GET | `/v1/xijian/protection/snapshots/<snapshot_id>` |
| POST | `/v1/xijian/protection/rollback` |
| POST | `/v1/xijian/protection/guard/preview` |
| GET | `/v1/xijian/protection/audit` |
| POST | `/v1/xijian/protection/audit/export` |
| POST | `/v1/xijian/sessions` |
| POST | `/v1/xijian/sessions/<session_id>/messages` |
| GET | `/v1/xijian/sessions/<session_id>/messages` |
| DELETE | `/v1/xijian/sessions/<session_id>` |
| GET | `/v1/xijian/settings` |
| PATCH | `/v1/xijian/settings` |
| GET | `/v1/xijian/settings/permissions` |
| POST | `/v1/xijian/resources/import` |
| GET | `/v1/xijian/resources/imports/<job_id>` |
| POST | `/v1/xijian/generation/abort` |
| POST | `/v1/xijian/_test/emit`（仅 `XIJIAN_DEV=1`） |

> 路由命名用 OAI 风格的 `asst_<...>`、`thread_<...>` 等前缀（`utils/ids.py` 已定义）。

---

## 13. 测试规范（`core/tests/`）

用 `pytest`。`conftest.py` 提供的 fixture：

- `app`：调 `create_app(testing=True)`，不走真实 waitress。
- `client`：Flask test client。
- `token`：从 `_TOKEN` 拿到的 Bearer。
- `auth_headers`：拼好 `Authorization: Bearer ...` 的 dict。
- `base_url`：方便构造 URL。

每个测试文件**专注一类**行为：

- `test_healthz.py`：`/healthz` 200 + body 为 `XIJIAN_OK_v1` + 不需要鉴权。
- `test_auth.py`：缺 token 401；错 token 401；带 token 200。
- `test_errors_dual_format.py`：同一个错误，分别用 `Accept: application/json` 和 `Accept: application/json-rpc`，断言响应体格式。
- `test_idempotency.py`：同 key + 同 body 两次 POST，第二次带 `Idempotency-Replayed: true`；同 key + 不同 body 第二次 409。
- `test_chat_sync.py`：`POST /v1/chat/completions` 不带 stream，拿到 OAI 标准结构。
- `test_chat_stream_sse.py`：stream=true + Accept: text/event-stream，拿到 SSE 帧序列（`data: ...\n\n` + `data: [DONE]\n\n`）。
- `test_chat_stream_ndjson.py`：stream=true + Accept: application/x-ndjson，拿到 NDJSON 帧。
- `test_chat_abort.py`：开一个 chat 流式请求 → 调 `/v1/chat/abort` → 流里收到 finish_reason=abort 并关闭。
- `test_models.py`：`/v1/models` 列表含预置 3 个；`/v1/models/{id}/load` 202；`/v1/models/{id}/unload` 200。
- `test_files.py`：upload → list → get → content → delete 全套。
- `test_xijian_characters.py`：CRUD + state + load/unload + interact。
- `test_xijian_memory.py`：CRUD + search + forget。
- `test_xijian_protection.py`：status → disable 两步 → guard/preview → snapshots → rollback → audit。
- `test_ws.py`：用 `simple_websocket` 客户端连 `/v1/ws` → 收到 hello → 发送 auth → 收到 auth.ok → 发送 ping → 收到 pong。
- `test_root_version.py`：`GET /v1` 返回能力清单。

**强制**：`pytest -q` 全绿才算交付。

---

## 14. 依赖（`pyproject.toml`）

```toml
[project]
name = "xijian-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "flask>=3.0",
  "flask-sock>=0.7",
  "simple-websocket>=1.0",
  "waitress>=3.0",   # 优先用 waitress 启动，缺失时 fallback 到 Flask dev server
]

[project.optional-dependencies]
test = ["pytest>=8", "pytest-cov", "httpx>=0.27", "websocket-client>=1.7"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["xijian_api*"]
```

---

## 15. 启动 + 跑测试的最短路径

```bash
# 安装
cd /Users/mofan/Documents/MyProjects/XiJian/core
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"

# 启动（dev 模式）
XIJIAN_API_PORT=18500 XIJIAN_DEV=1 python -m xijian_api

# 健康检查
curl -s http://127.0.0.1:18500/healthz
# → XIJIAN_OK_v1

# Bearer token 在启动时打印到 stderr，形如：
# [xijian-api] dev token: a1b2c3...

# 测试
pytest -q
```

---

## 16. 禁止项

- 禁止在 `core/` 任何模块 import `mlx` / `llama_cpp` / `ollama` / `pytorch` / `tensorflow`。本次实现纯 API 层。
- 禁止监听 `0.0.0.0`。
- 禁止把 token 写到日志 / 响应体。
- 禁止 `idempotency key` 原值出现在日志里（必须 sha256 后 4 字节前缀 + `***`）。
- 禁止在 `services/*` 路径下出现 — 本次实现是 `stubs/*`，路径是 stub。
- 禁止跳过测试 — 每个 worker 完成任务后必须 `pytest -q` 跑全绿。
- 禁止修改 `docs/`、`/tmp/xijian-*` 之外的全局状态。

---

## 17. worker 之间的依赖关系

| 任务 | 依赖 | 备注 |
|---|---|---|
| foundation | 无 | 第一个做 |
| oai-routes | foundation | 可与 xijian-routes 并行 |
| xijian-routes | foundation | 可与 oai-routes 并行 |
| websocket | foundation | 可与 oai-routes、xijian-routes 并行 |
| e2e-tests | foundation, oai-routes, xijian-routes, websocket | 最后一关，**独立验证整体** |

---

## 18. 完成标准（Definition of Done）

- `core/` 下所有文件按本设计契约就位，目录树与 §2 一致。
- `cd core && pip install -e ".[test]"` 成功。
- `python -m xijian_api` 在 `XIJIAN_API_PORT=18500 XIJIAN_DEV=1` 下能起来，stderr 打印 token。
- `curl http://127.0.0.1:18500/healthz` 返回 `XIJIAN_OK_v1`。
- `pytest -q` 全绿（覆盖 §13 全部测试文件）。
- 任何 worker 自己交付时也要满足"自己实现的部分有对应测试覆盖"。

---

> 维护者：Mavis · 与 docs/api.md、docs/Dev.md、docs/ai-backend.md 同步更新。
