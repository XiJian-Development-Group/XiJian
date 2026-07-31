"""Tests for the multimodal understanding stack.

(全模态理解技术栈的测试。)

Covers:

* ``POST /v1/multimodal/completions`` — sync, streaming, validation
  error, abort integration.
  (``POST /v1/multimodal/completions`` — 同步、流式、校验错误、中止集成。)
* ``GET /v1/multimodal/models`` and ``POST /v1/multimodal/abort``.
  (``GET /v1/multimodal/models`` 和 ``POST /v1/multimodal/abort``。)
* ``POST /v1/images/understanding`` — multipart and JSON inputs, plus
  the missing-image 400.
  (``POST /v1/images/understanding`` — multipart 与 JSON 两种入参，以及缺图 400。)
* ``POST /v1/audio/speech`` — the ``emotion`` parameter passthrough.
  (``POST /v1/audio/speech`` — ``emotion`` 参数透传。)
* The dispatch logic in :mod:`xijian_api.stubs.multimodal`
  (:func:`understand` / :func:`understand_stream`).
  (:mod:`xijian_api.stubs.multimodal` 的调度逻辑
  (:func:`understand` / :func:`understand_stream`)。)
* The deterministic mock backends
  (:class:`~xijian_api.ai.backends.mock.multimodal.MockMultimodalBackend`
  and
  :class:`~xijian_api.ai.backends.mock.multimodal.MockVideoUnderstandingBackend`).

Route tests go through the registered ``stub-multimodal`` /
``stub-video-understanding`` mock entries in ``config.toml``, so no
real model weights are needed.
(路由测试通过 ``config.toml`` 中注册的 ``stub-multimodal`` /
``stub-video-understanding`` mock 条目进行，因此无需真实模型权重。)
"""

from __future__ import annotations

import io
import json

import pytest

from xijian_api import abort as abort_registry
from xijian_api.ai.backends.mock.multimodal import (
    MockMultimodalBackend,
    MockVideoUnderstandingBackend,
)
from xijian_api.ai.types import (
    ChatChunk,
    ChatChoice,
    ChatUsage,
    GenerationParams,
    MultimodalBackend,
)
from xijian_api.errors import GenerationAborted as ApiGenerationAborted


# ---------------------------------------------------------------------------
# Helpers / 辅助函数
# ---------------------------------------------------------------------------


def _multimodal_messages(text: str = "What is in this image?") -> list[dict]:
    """Build a user message with text + image content parts.
    (构建包含文本 + 图像内容片段的用户消息。)
    """
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        }
    ]


def _collect_sse_events(body: str) -> list[dict]:
    """Parse the ``data:`` lines of an SSE body into JSON dicts.
    (将 SSE 响应体的 ``data:`` 行解析为 JSON 字典列表。)
    """
    events: list[dict] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = line[len("data: "):]
            if payload == "[DONE]":
                continue
            events.append(json.loads(payload))
    return events


# ---------------------------------------------------------------------------
# POST /v1/multimodal/completions — sync
# ---------------------------------------------------------------------------
# (同步)


def test_multimodal_completions_sync(client, auth_headers):
    """Non-streaming multimodal completion returns an OAI-like body.
    (非流式全模态补全返回类 OAI 响应体。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers=auth_headers,
        json={"model": "stub-multimodal", "messages": _multimodal_messages()},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "multimodal.completion"
    assert body["model"] == "stub-multimodal"
    assert body["choices"][0]["message"]["role"] == "assistant"
    content = body["choices"][0]["message"]["content"]
    assert "[echo: What is in this image?]" in content
    assert "Mock multimodal understanding" in content
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] > 0
    assert body["xijian"]["backend"] == "mock"
    # The route stamps the model id on the response.
    # (路由在响应上标记模型 id。)
    assert response.headers.get("X-XiJian-Model-Id") == "stub-multimodal"


def test_multimodal_completions_sync_honours_params(client, auth_headers):
    """temperature / top_p / max_tokens / stop flow through to the backend.
    (temperature / top_p / max_tokens / stop 透传到后端。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers=auth_headers,
        json={
            "model": "stub-multimodal",
            "messages": _multimodal_messages("short"),
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 5,
            "stop": ["END"],
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    content = body["choices"][0]["message"]["content"]
    # max_tokens=5 truncates the mock token sequence.
    # (max_tokens=5 截断模拟 token 序列。)
    assert len(content.split()) <= 8
    assert body["usage"]["completion_tokens"] <= 8


def test_multimodal_completions_missing_messages_400(client, auth_headers):
    """A request without ``messages`` is rejected with 400.
    (没有 ``messages`` 的请求被拒绝，返回 400。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers=auth_headers,
        json={"model": "stub-multimodal"},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["code"] == "missing_messages"
    assert body["error"]["param"] == "messages"


# ---------------------------------------------------------------------------
# POST /v1/multimodal/completions — streaming
# ---------------------------------------------------------------------------
# (流式)


def test_multimodal_completions_stream(client, auth_headers):
    """Streaming multimodal completion yields SSE chunks + [DONE].
    (流式全模态补全产生 SSE 数据块 + [DONE]。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers={**auth_headers, "Accept": "text/event-stream"},
        json={
            "model": "stub-multimodal",
            "messages": _multimodal_messages(),
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.content_type
    body = response.get_data(as_text=True)
    assert "data: [DONE]" in body
    events = _collect_sse_events(body)
    assert events, "expected at least one SSE chunk"
    assert events[0]["object"] == "multimodal.completion.chunk"
    # Content is streamed incrementally; the tail chunk carries finish.
    # (内容增量流出；结尾块携带 finish。)
    content = "".join(
        e.get("choices", [{}])[0].get("delta", {}).get("content", "")
        for e in events
    )
    assert "Mock multimodal understanding" in content
    last = events[-1]
    assert last["choices"][0]["finish_reason"] == "stop"


def test_multimodal_completions_stream_include_usage(client, auth_headers):
    """stream_options.include_usage appends a usage-only chunk.
    (stream_options.include_usage 追加一个仅含 usage 的块。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers={**auth_headers, "Accept": "text/event-stream"},
        json={
            "model": "stub-multimodal",
            "messages": _multimodal_messages(),
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )
    assert response.status_code == 200
    events = _collect_sse_events(response.get_data(as_text=True))
    usage_events = [e for e in events if "usage" in e and e["usage"] is not None]
    assert usage_events, "expected usage-bearing chunks"
    # The backend's finish chunk carries the real usage; the stub then
    # appends a zeroed include_usage sentinel.
    # (后端的结束块携带真实 usage；存根随后追加一个清零的 include_usage 哨兵块。)
    assert any(e["usage"]["total_tokens"] > 0 for e in usage_events)
    assert usage_events[-1]["usage"]["total_tokens"] == 0


# ---------------------------------------------------------------------------
# POST /v1/multimodal/abort
# ---------------------------------------------------------------------------
# (中止)


def test_multimodal_abort_unknown_request_id_returns_200(client, auth_headers):
    """Aborting an unknown request is a 200 with ``aborted: false``.
    (中止未知请求返回 200 且 ``aborted: false``。)
    """
    response = client.post(
        "/v1/multimodal/abort",
        headers=auth_headers,
        json={"request_id": "req_does_not_exist"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["aborted"] is False
    assert body["request_id"] == "req_does_not_exist"


def test_multimodal_abort_missing_request_id_returns_400(client, auth_headers):
    """A request without ``request_id`` is rejected with 400.
    (没有 ``request_id`` 的请求被拒绝，返回 400。)
    """
    response = client.post(
        "/v1/multimodal/abort",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["code"] == "missing_request_id"


def test_multimodal_abort_signals_active_stream(client, auth_headers):
    """Aborting a registered request_id flips the AbortSignal.
    (中止已注册的 request_id 会翻转 AbortSignal。)
    """
    request_id = "req_multimodal_abort_1234"
    signal = abort_registry.register(request_id)
    assert not signal.is_set()
    try:
        response = client.post(
            "/v1/multimodal/abort",
            headers=auth_headers,
            json={"request_id": request_id},
        )
        assert response.status_code == 204
        assert signal.is_set()
    finally:
        abort_registry.cleanup(request_id)


def test_multimodal_stream_completes_after_abort_burn(client, auth_headers):
    """After burning an abort on a non-existent id, a fresh stream completes.
    (对一个不存在的 id 执行中止后，新的流正常完成。)
    """
    client.post(
        "/v1/multimodal/abort",
        headers=auth_headers,
        json={"request_id": "req_multimodal_burn_1"},
    )
    response = client.post(
        "/v1/multimodal/completions",
        headers={**auth_headers, "Accept": "text/event-stream"},
        json={
            "model": "stub-multimodal",
            "messages": _multimodal_messages(),
            "stream": True,
        },
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "data: [DONE]" in body
    events = _collect_sse_events(body)
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# GET /v1/multimodal/models
# ---------------------------------------------------------------------------
# (模型列表)


def test_multimodal_models_lists_only_multimodal(client, auth_headers):
    """Lists registered multimodal models (and not chat models).
    (列出已注册的全模态模型，且不包含聊天模型。)
    """
    response = client.get("/v1/multimodal/models", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert "stub-multimodal" in ids
    # Chat-only entries must not leak into the multimodal listing.
    # (仅聊天条目不得泄漏到全模态列表中。)
    assert "qwen2.5-7b-mlx-4bit" not in ids


# ---------------------------------------------------------------------------
# POST /v1/images/understanding
# ---------------------------------------------------------------------------
# (图像理解)


def test_images_understanding_multipart(client, auth_headers):
    """Multipart upload with an image file returns understanding text.
    (带图像文件的 multipart 上传返回理解文本。)
    """
    response = client.post(
        "/v1/images/understanding",
        headers=auth_headers,
        data={
            "prompt": "Describe this image.",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\nfakepng"), "test.png"),
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "multimodal.completion"
    content = body["choices"][0]["message"]["content"]
    assert "[echo: Describe this image.]" in content
    assert "Mock multimodal understanding" in content


def test_images_understanding_json(client, auth_headers):
    """JSON body with a base64 image URL returns understanding text.
    (带 base64 图像 URL 的 JSON 请求体返回理解文本。)
    """
    response = client.post(
        "/v1/images/understanding",
        headers=auth_headers,
        json={
            "image": "data:image/png;base64,aGVsbG8=",
            "prompt": "What do you see?",
            "model": "stub-multimodal",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    content = body["choices"][0]["message"]["content"]
    assert "[echo: What do you see?]" in content


def test_images_understanding_missing_image_400(client, auth_headers):
    """A request without any image is rejected with 400.
    (没有任何图像的请求被拒绝，返回 400。)
    """
    response = client.post(
        "/v1/images/understanding",
        headers=auth_headers,
        json={"prompt": "Describe this image."},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["code"] == "missing_image"


# ---------------------------------------------------------------------------
# POST /v1/audio/speech — emotion passthrough
# ---------------------------------------------------------------------------
# (emotion 参数透传)


class _FakeTTSBackend:
    """Records synthesis kwargs; returns deterministic bytes.
    (记录合成参数；返回确定性字节。)
    """

    name = "fake"
    captured: dict = {}

    def is_available(self) -> bool:
        return True

    def synth(
        self,
        text,
        *,
        voice,
        response_format,
        speed: float = 1.0,
        emotion=None,
        voice_clone_ref=None,
        abort_signal=None,
    ) -> bytes:
        self.captured = {
            "text": text,
            "voice": voice,
            "response_format": response_format,
            "speed": speed,
            "emotion": emotion,
            "voice_clone_ref": voice_clone_ref,
            "abort_signal": abort_signal,
        }
        return b"fake-audio-bytes"


def test_audio_speech_emotion_passthrough(client, auth_headers, monkeypatch):
    """The ``emotion`` request field reaches the TTS backend.
    (``emotion`` 请求字段到达 TTS 后端。)
    """
    fake = _FakeTTSBackend()
    monkeypatch.setattr(
        "xijian_api.stubs.audio.get_tts_backend",
        lambda *args, **kwargs: fake,
    )
    response = client.post(
        "/v1/audio/speech",
        headers=auth_headers,
        json={
            "input": "Hello there",
            "voice": "yuki",
            "response_format": "mp3",
            "emotion": "happy",
        },
    )
    assert response.status_code == 200
    assert response.data == b"fake-audio-bytes"
    assert response.content_type.startswith("audio/mpeg")
    assert fake.captured["emotion"] == "happy"
    assert fake.captured["text"] == "Hello there"
    assert fake.captured["voice"] == "yuki"
    assert fake.captured["response_format"] == "mp3"


def test_audio_speech_without_emotion_passes_none(client, auth_headers, monkeypatch):
    """Without ``emotion`` the backend receives ``None``.
    (没有 ``emotion`` 时后端收到 ``None``。)
    """
    fake = _FakeTTSBackend()
    monkeypatch.setattr(
        "xijian_api.stubs.audio.get_tts_backend",
        lambda *args, **kwargs: fake,
    )
    response = client.post(
        "/v1/audio/speech",
        headers=auth_headers,
        json={"input": "Hello", "voice": "default"},
    )
    assert response.status_code == 200
    assert fake.captured["emotion"] is None


# ---------------------------------------------------------------------------
# stubs/multimodal.py — dispatch logic
# ---------------------------------------------------------------------------
# (调度逻辑)


class _FakeMultimodalBackend(MultimodalBackend):
    """Records call args; yields deterministic chunks.
    (记录调用参数；产生确定性块。)
    """

    def __init__(self) -> None:
        self.captured: dict = {}
        self.fail_with: Exception | None = None

    def is_available(self) -> bool:
        return True

    def understand(
        self,
        messages,
        params,
        *,
        stream: bool = False,
        abort_signal=None,
    ):
        self.captured["messages"] = messages
        self.captured["params"] = params
        self.captured["stream"] = stream
        self.captured["abort_signal"] = abort_signal
        if self.fail_with is not None:
            # Raise at call time (like the real backends do for
            # ModelNotLoaded) rather than at iteration time.
            # (在调用时抛出（与真实后端对 ModelNotLoaded 的做法一致）
            # 而不是在迭代时。)
            raise self.fail_with

        def _gen():
            if stream:
                yield ChatChunk(
                    id="chunk-1", model="fake", created=123,
                    choices=[ChatChoice(index=0, delta={"content": "Hel"}, finish_reason=None)],
                )
                yield ChatChunk(
                    id="chunk-2", model="fake", created=123,
                    choices=[ChatChoice(index=0, delta={"content": "lo"}, finish_reason="stop")],
                    usage=ChatUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                )
            else:
                yield ChatChunk(
                    id="chunk-1", model="fake", created=123,
                    choices=[
                        ChatChoice(
                            index=0,
                            delta={"role": "assistant", "content": "Hello multimodal"},
                            finish_reason="stop",
                        )
                    ],
                    usage=ChatUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                    backend="fake",
                )

        return _gen()


def test_stub_understand_response_shape(monkeypatch):
    """understand() renders an OAI completion dict from backend chunks.
    (understand() 从后端块渲染 OAI 补全字典。)
    """
    from xijian_api.stubs import multimodal as multimodal_stub

    fake = _FakeMultimodalBackend()
    monkeypatch.setattr(multimodal_stub, "_resolve_backend_for", lambda model_id: fake)

    result = multimodal_stub.understand(
        _multimodal_messages(),
        model="stub-multimodal",
        temperature=0.3,
        top_p=0.8,
        max_tokens=50,
        stop=["END"],
    )
    assert result["object"] == "multimodal.completion"
    assert result["model"] == "stub-multimodal"
    assert result["choices"][0]["message"]["content"] == "Hello multimodal"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 3
    assert result["xijian"]["backend"] == "fake"
    # GenerationParams propagated to the backend call.
    # (GenerationParams 透传到后端调用。)
    params = fake.captured["params"]
    assert isinstance(params, GenerationParams)
    assert params.temperature == 0.3
    assert params.top_p == 0.8
    assert params.max_tokens == 50
    assert list(params.stop) == ["END"]
    assert fake.captured["stream"] is False


def test_stub_understand_stream_chunks(monkeypatch):
    """understand_stream() yields OAI streaming chunk dicts.
    (understand_stream() 产生 OAI 流式块字典。)
    """
    from xijian_api.stubs import multimodal as multimodal_stub

    fake = _FakeMultimodalBackend()
    monkeypatch.setattr(multimodal_stub, "_resolve_backend_for", lambda model_id: fake)

    chunks = list(multimodal_stub.understand_stream(
        _multimodal_messages(),
        model="stub-multimodal",
        include_usage=True,
    ))
    assert chunks[0]["object"] == "multimodal.completion.chunk"
    assert chunks[0]["choices"][0]["delta"]["content"] == "Hel"
    assert chunks[1]["choices"][0]["delta"]["content"] == "lo"
    assert chunks[1]["choices"][0]["finish_reason"] == "stop"
    # The finish chunk carries the backend usage.
    # (结束块携带后端 usage。)
    assert chunks[1]["usage"]["total_tokens"] == 3
    # include_usage appends a usage-only sentinel chunk (zeroed by the stub).
    # (include_usage 追加一个仅含 usage 的哨兵块（存根将其清零）。)
    assert chunks[-1]["usage"]["total_tokens"] == 0
    assert chunks[-1]["choices"] == []
    assert fake.captured["stream"] is True


def test_stub_understand_translates_backend_error(monkeypatch):
    """AI-layer errors become API 503 envelopes.
    (AI 层错误转换为 API 503 信封。)
    """
    from xijian_api.ai.base import BackendError as AIBackendError
    from xijian_api.errors import BackendError as ApiBackendError
    from xijian_api.stubs import multimodal as multimodal_stub

    fake = _FakeMultimodalBackend()
    fake.fail_with = AIBackendError("model exploded", code="backend_error")
    monkeypatch.setattr(multimodal_stub, "_resolve_backend_for", lambda model_id: fake)

    with pytest.raises(ApiBackendError) as excinfo:
        multimodal_stub.understand(_multimodal_messages(), model="stub-multimodal")
    assert excinfo.value.status == 503
    assert "model exploded" in str(excinfo.value)


def test_stub_resolve_backend_for_registered_model(app):
    """_resolve_backend_for loads a registered multimodal model via the registry.
    (_resolve_backend_for 通过注册表加载已注册的全模态模型。)
    """
    from xijian_api.stubs import multimodal as multimodal_stub

    with app.app_context():
        backend = multimodal_stub._resolve_backend_for("stub-multimodal")
    assert isinstance(backend, MockMultimodalBackend)
    assert backend.is_loaded()


# ---------------------------------------------------------------------------
# Mock backends — contract
# ---------------------------------------------------------------------------
# (模拟后端契约)


def test_mock_multimodal_backend_blocking():
    """Blocking understand yields a single chunk with content + usage.
    (阻塞 understand 产生包含内容和用量的单个块。)
    """
    backend = MockMultimodalBackend()
    backend.load("/fake/path")
    chunks = list(backend.understand(
        _multimodal_messages("Hi there"),
        GenerationParams(),
        stream=False,
    ))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.choices[0].delta["role"] == "assistant"
    content = chunk.choices[0].delta["content"]
    assert "[echo: Hi there]" in content
    assert "Mock multimodal understanding" in content
    assert chunk.choices[0].finish_reason == "stop"
    assert chunk.usage.total_tokens > 0
    assert chunk.backend == "mock"


def test_mock_multimodal_backend_streaming():
    """Streaming understand yields role-first, then deltas, then finish.
    (流式 understand 先产生角色，然后是增量，最后是完成。)
    """
    backend = MockMultimodalBackend()
    backend.load("/fake/path")
    chunks = list(backend.understand(
        _multimodal_messages("stream me"),
        GenerationParams(),
        stream=True,
    ))
    assert chunks[0].choices[0].delta.get("role") == "assistant"
    content = "".join(
        c.choices[0].delta.get("content", "")
        for c in chunks
        if c.choices[0].delta.get("content")
    )
    assert "[echo: stream me]" in content
    assert chunks[-1].choices[0].finish_reason == "stop"


def test_mock_multimodal_backend_abort():
    """A set AbortSignal ends the stream with finish_reason=abort.
    (已设置的 AbortSignal 以 finish_reason=abort 结束流。)
    """
    from xijian_api import abort as abort_registry

    backend = MockMultimodalBackend()
    backend.load("/fake/path")
    signal = abort_registry.AbortSignal()
    gen = backend.understand(
        _multimodal_messages("abort me"),
        GenerationParams(),
        stream=True,
        abort_signal=signal,
    )
    # Role chunk first.
    # (先是角色块。)
    first = next(gen)
    assert first.choices[0].delta.get("role") == "assistant"
    # Consume a few content chunks, then abort.
    # (消费几个内容块，然后中止。)
    for _ in range(5):
        next(gen)
    signal.set()
    # The mock swallows the abort exception internally and signals it
    # via the final chunk's finish_reason — mirroring mock/chat.py.
    # (模拟器在内部吞掉中止异常，并通过最终块的 finish_reason 发出信号
    # — 与 mock/chat.py 一致。)
    final = next(gen)
    assert final.choices[0].finish_reason == "abort"
    with pytest.raises(StopIteration):
        next(gen)


def test_mock_multimodal_backend_not_loaded():
    """understand() raises ModelNotLoaded before load().
    (load() 之前 understand() 抛出 ModelNotLoaded。)
    """
    from xijian_api.ai.base import ModelNotLoaded

    backend = MockMultimodalBackend()
    with pytest.raises(ModelNotLoaded):
        list(backend.understand(_multimodal_messages(), GenerationParams()))


def test_mock_video_understanding_backend():
    """Mock video understanding returns deterministic text.
    (模拟视频理解返回确定性文本。)
    """
    backend = MockVideoUnderstandingBackend()
    backend.load("/fake/video")
    result = backend.understand(
        {"video_url": {"url": "file:///tmp/clip.mp4"}},
        prompt="Who is in the video?",
        fps=2,
        max_frames=6,
    )
    assert "prompt: Who is in the video?" in result
    assert "clip.mp4" in result
    assert "fps=2" in result
    assert "max_frames=6" in result


def test_mock_video_understanding_backend_not_loaded():
    """understand() raises ModelNotLoaded before load().
    (load() 之前 understand() 抛出 ModelNotLoaded。)
    """
    from xijian_api.ai.base import ModelNotLoaded

    backend = MockVideoUnderstandingBackend()
    with pytest.raises(ModelNotLoaded):
        backend.understand({"url": "file:///tmp/clip.mp4"})


# ---------------------------------------------------------------------------
# Regression tests — QA findings (S1/M2/M6 + content-part helpers)
# (回归测试 — QA 发现的问题（S1/M2/M6 + 内容片段辅助函数）)
# ---------------------------------------------------------------------------


def test_multimodal_stream_unknown_model_returns_503(client, auth_headers):
    """Streaming with an unknown model must return 503, not 500.

    (流式请求使用未知模型必须返回 503 而不是 500。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers=auth_headers,
        json={
            "model": "no-such-model",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 503
    body = response.get_json()
    assert body["error"]["code"] == "backend_unavailable"


def test_multimodal_sync_unknown_model_returns_503(client, auth_headers):
    """Non-streaming with an unknown model also returns 503.

    (非流式请求使用未知模型同样返回 503。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers=auth_headers,
        json={
            "model": "no-such-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "backend_unavailable"


def test_multimodal_max_tokens_string_coerced(client, auth_headers):
    """A string ``max_tokens`` must be coerced instead of 500ing.

    (字符串形式的 ``max_tokens`` 应被转换而不是返回 500。)
    """
    response = client.post(
        "/v1/multimodal/completions",
        headers=auth_headers,
        json={
            "model": "stub-multimodal",
            "max_tokens": "50",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert response.get_json()["choices"][0]["finish_reason"] == "stop"


def test_video_understanding_endpoint_default_model(client, auth_headers):
    """POST /v1/videos/understanding with a JSON video URL works.

    (POST /v1/videos/understanding 使用 JSON video URL 正常工作。)
    """
    response = client.post(
        "/v1/videos/understanding",
        headers=auth_headers,
        json={"video": "file:///tmp/clip.mp4", "prompt": "Who is in the video?"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "video.understanding"
    assert "Who is in the video?" in body["text"]


def test_video_understanding_endpoint_explicit_model(client, auth_headers):
    """The explicit stub-video-understanding model is honoured.

    (显式指定 stub-video-understanding 模型生效。)
    """
    response = client.post(
        "/v1/videos/understanding",
        headers=auth_headers,
        json={
            "video": "file:///tmp/clip.mp4",
            "model": "stub-video-understanding",
            "fps": 2,
            "max_frames": 6,
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["model"] == "stub-video-understanding"
    assert "fps=2" in body["text"]
    assert "max_frames=6" in body["text"]


def test_video_understanding_endpoint_missing_video_400(client, auth_headers):
    """Missing video input returns 400.

    (缺少视频输入返回 400。)
    """
    response = client.post(
        "/v1/videos/understanding",
        headers=auth_headers,
        json={"prompt": "hi"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_video"


def test_video_understanding_endpoint_unknown_model_503(client, auth_headers):
    """Unknown model on the video understanding endpoint returns 503.

    (视频理解端点使用未知模型返回 503。)
    """
    response = client.post(
        "/v1/videos/understanding",
        headers=auth_headers,
        json={"video": "file:///tmp/clip.mp4", "model": "no-such-model"},
    )
    assert response.status_code == 503


def test_content_part_helpers_roundtrip():
    """resolve_part_to_path / resolve_part_content handle data URIs.

    (resolve_part_to_path / resolve_part_content 处理 data URI。)
    """
    from xijian_api.ai.types import (
        detect_part_mime,
        make_image_part,
        resolve_part_content,
    )

    part = make_image_part("data:image/png;base64,aGVsbG8=")
    assert part["type"] == "image_url"
    assert detect_part_mime(part) == "image/png"
    content = resolve_part_content(part)
    assert content == b"hello"


def test_content_part_make_audio_video_file_parts():
    """make_audio_part / make_video_part / make_file_part shapes.

    (make_audio_part / make_video_part / make_file_part 的形状。)
    """
    from xijian_api.ai.types import (
        make_audio_part,
        make_file_part,
        make_text_part,
        make_video_part,
    )

    assert make_text_part("hi") == {"type": "text", "text": "hi"}
    audio = make_audio_part("file:///tmp/a.wav", format="wav")
    assert audio["audio_url"]["format"] == "wav"
    video = make_video_part("file:///tmp/v.mp4")
    assert video["video_url"]["url"] == "file:///tmp/v.mp4"
    file_part = make_file_part("file:///tmp/f.pdf", mime_type="application/pdf")
    assert file_part["file_url"]["mime_type"] == "application/pdf"


def test_gguf_multimodal_accepts_file_url_type():
    """GGUF multimodal preprocessing must accept ``file_url`` parts.

    (GGUF 全模态预处理必须接受 ``file_url`` 片段。)
    """
    from xijian_api.ai.backends.gguf.multimodal import _preprocess_multimodal_messages

    processed = _preprocess_multimodal_messages([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "read this"},
                {"type": "file_url", "file_url": {"url": "/nonexistent/x.txt"}},
            ],
        }
    ])
    content = processed[0].content
    # The file cannot be read (no such path), so a readable fallback text
    # is produced instead of a bare ``[file_url]`` placeholder.
    # (文件不存在，因此生成可读的回退文本而不是裸 ``[file_url]`` 占位符。)
    assert any(isinstance(p, dict) and p.get("type") == "text" for p in content)
    assert not any(isinstance(p, dict) and p.get("type") == "file_url" for p in content)


def test_stub_resolve_backend_eager_raises_503(monkeypatch):
    """resolve_backend() translates BackendUnavailable into ApiBackendError 503.

    (resolve_backend() 将 BackendUnavailable 转换为 ApiBackendError 503。)
    """
    from xijian_api.ai.base import BackendUnavailable as AIBackendUnavailable
    from xijian_api.errors import BackendError as ApiBackendError
    from xijian_api.stubs import multimodal as mm

    def _boom(name=None, fallbacks=()):
        raise AIBackendUnavailable("no usable backend", code="backend_unavailable")

    monkeypatch.setattr(mm, "_resolve_config", lambda: None)
    monkeypatch.setattr(mm, "get_multimodal_backend", _boom)
    with pytest.raises(ApiBackendError) as exc_info:
        mm.resolve_backend("no-such-model")
    assert exc_info.value.status == 503
    assert exc_info.value.code == "backend_unavailable"
