"""Shared HTTP client for OpenAI-compatible remote backends.

Provides a thin transport layer that both ``httpx`` (default, always
available) and the optional ``openai`` SDK can plug into.  The
backends in this package call :func:`remote_chat_completion`,
:func:`remote_embeddings`, etc. and never touch HTTP directly —
that way switching transports is a config-level decision.

Config resolution order (per-model):

1. ``[[models]].extra`` fields (``base_url``, ``api_key``,
   ``model_name``, ``transport``) — highest priority, per-model.
2. ``[backends.openai]`` section in ``config.toml`` — global defaults
   shared by every ``backend = "openai"`` model.
3. Environment variables (``OPENAI_API_KEY``, ``OPENAI_BASE_URL``) —
   fallback so operators don't need to put secrets in config files.
4. Hardcoded defaults (``https://api.openai.com/v1``, empty key).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterator

from xijian_api.ai.base import BackendError


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


_DEFAULT_BASE_URL = "https://api.openai.com/v1"


@dataclass
class RemoteConfig:
    """Resolved connection settings for a single remote call."""

    base_url: str
    api_key: str
    model_name: str
    transport: str  # "httpx" | "openai_sdk"
    extra_headers: dict[str, str]

    @property
    def auth_header(self) -> dict[str, str]:
        h = dict(self.extra_headers)
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h


def resolve_config(
    model_extra: dict[str, Any] | None = None,
    *,
    section: dict[str, Any] | None = None,
    default_model: str = "",
) -> RemoteConfig:
    """Merge per-model ``extra`` with the global ``[backends.openai]`` section.

    ``model_extra`` wins, then ``section``, then env, then defaults.
    """
    extra = model_extra or {}
    sec = section or {}

    base_url = (
        extra.get("base_url")
        or sec.get("base_url")
        or os.environ.get("OPENAI_BASE_URL")
        or _DEFAULT_BASE_URL
    )
    api_key = (
        extra.get("api_key")
        or sec.get("api_key")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    model_name = (
        extra.get("model_name")
        or extra.get("model")
        or sec.get("default_model")
        or default_model
    )
    transport = (
        extra.get("transport")
        or sec.get("transport")
        or "httpx"
    )
    # Strip trailing slash so URL join is predictable.
    base_url = base_url.rstrip("/")
    return RemoteConfig(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        transport=transport,
        extra_headers=dict(extra.get("headers") or sec.get("headers") or {}),
    )


# ---------------------------------------------------------------------------
# Transport: httpx (default)
# ---------------------------------------------------------------------------


def _httpx_post_json(
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict,
    timeout: float = 120.0,
) -> dict:
    import httpx

    try:
        resp = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
    except Exception as exc:
        raise BackendError(
            f"remote request failed: {exc}", code="backend_error"
        ) from exc
    _raise_for_status(resp)
    return resp.json()


def _httpx_post_stream(
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict,
    timeout: float = 300.0,
) -> Iterator[dict]:
    """Yield parsed SSE ``data:`` lines as dicts.

    httpx's ``stream()`` context gives us raw lines; we parse the OAI
    SSE format (``data: {json}\n\n``, terminated by ``data: [DONE]``).
    """
    import httpx

    try:
        with httpx.stream(
            "POST", url, headers=headers, json=json_body, timeout=timeout
        ) as resp:
            _raise_for_status(resp)
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    payload = line[6:]
                elif line.startswith("data:"):
                    payload = line[5:]
                else:
                    continue
                payload = payload.strip()
                if payload == "[DONE]":
                    return
                import json
                try:
                    yield json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    continue
    except BackendError:
        raise
    except Exception as exc:
        raise BackendError(
            f"remote stream failed: {exc}", code="backend_error"
        ) from exc


def _httpx_post_multipart(
    url: str,
    *,
    headers: dict[str, str],
    files: dict,
    data: dict,
    timeout: float = 120.0,
) -> dict:
    import httpx

    try:
        # httpx wants headers WITHOUT content-type for multipart
        # (it sets the boundary itself).  Drop any manually-set
        # content-type so the boundary is correct.
        clean_headers = {k: v for k, v in headers.items()
                         if k.lower() != "content-type"}
        resp = httpx.post(
            url, headers=clean_headers, files=files, data=data, timeout=timeout
        )
    except Exception as exc:
        raise BackendError(
            f"remote multipart request failed: {exc}", code="backend_error"
        ) from exc
    _raise_for_status(resp)
    return resp.json()


def _httpx_get_bytes(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 120.0,
) -> bytes:
    import httpx

    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        raise BackendError(
            f"remote GET failed: {exc}", code="backend_error"
        ) from exc
    _raise_for_status(resp)
    return resp.content


def _httpx_post_raw(
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict,
    timeout: float = 120.0,
) -> bytes:
    """POST and return raw response bytes (for audio/image downloads)."""
    import httpx

    try:
        resp = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
    except Exception as exc:
        raise BackendError(
            f"remote request failed: {exc}", code="backend_error"
        ) from exc
    _raise_for_status(resp)
    return resp.content


def _raise_for_status(resp) -> None:
    """Translate HTTP errors into :class:`BackendError`."""
    status = resp.status_code
    if status < 400:
        return
    try:
        body = resp.json()
        msg = (body.get("error") or {}).get("message", str(body))
    except Exception:
        msg = resp.text[:500]
    raise BackendError(
        f"remote API error {status}: {msg}", code="backend_error"
    )


# ---------------------------------------------------------------------------
# Transport: openai SDK (optional)
# ---------------------------------------------------------------------------


def _openai_client(cfg: RemoteConfig):
    """Construct an ``openai.OpenAI`` client (or raise if not installed)."""
    try:
        from openai import OpenAI
    except Exception as exc:
        raise BackendError(
            f"openai SDK not installed: {exc}", code="backend_unavailable"
        ) from exc
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


# ---------------------------------------------------------------------------
# High-level API used by the backends
# ---------------------------------------------------------------------------


def remote_chat_completion(
    cfg: RemoteConfig,
    *,
    messages: list[dict],
    stream: bool = False,
    **kwargs,
) -> dict | Iterator[dict]:
    """Call ``POST /chat/completions`` on the remote endpoint.

    Returns the parsed JSON dict (non-stream) or an iterator of SSE
    chunk dicts (stream).
    """
    url = f"{cfg.base_url}/chat/completions"
    body: dict[str, Any] = {"model": cfg.model_name, "messages": messages, **kwargs}
    if stream:
        body["stream"] = True
        return _httpx_post_stream(
            url, headers=cfg.auth_header, json_body=body,
        )
    return _httpx_post_json(url, headers=cfg.auth_header, json_body=body)


def remote_embeddings(
    cfg: RemoteConfig,
    *,
    input: list[str],
    **kwargs,
) -> dict:
    url = f"{cfg.base_url}/embeddings"
    body: dict[str, Any] = {"model": cfg.model_name, "input": input, **kwargs}
    return _httpx_post_json(url, headers=cfg.auth_header, json_body=body)


def remote_tts(
    cfg: RemoteConfig,
    *,
    text: str,
    voice: str,
    response_format: str,
    speed: float,
) -> bytes:
    url = f"{cfg.base_url}/audio/speech"
    body = {
        "model": cfg.model_name,
        "input": text,
        "voice": voice,
        "response_format": response_format,
        "speed": speed,
    }
    return _httpx_post_raw(url, headers=cfg.auth_header, json_body=body)


def remote_stt(
    cfg: RemoteConfig,
    *,
    audio_bytes: bytes,
    filename: str,
    language: str | None,
    prompt: str | None,
    response_format: str,
) -> dict:
    url = f"{cfg.base_url}/audio/transcriptions"
    files = {"file": (filename, audio_bytes)}
    data: dict[str, Any] = {"model": cfg.model_name, "response_format": response_format}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    # Multipart uploads must NOT carry an Authorization header with
    # content-type — httpx sets the boundary.  The auth bearer token
    # is still sent.
    return _httpx_post_multipart(
        url, headers=cfg.auth_header, files=files, data=data,
    )


def remote_image_generate(
    cfg: RemoteConfig,
    *,
    prompt: str,
    n: int,
    size: str,
    response_format: str = "b64_json",
) -> dict:
    url = f"{cfg.base_url}/images/generations"
    body = {
        "model": cfg.model_name,
        "prompt": prompt,
        "n": n,
        "size": size,
        "response_format": response_format,
    }
    return _httpx_post_json(url, headers=cfg.auth_header, json_body=body)


__all__ = [
    "RemoteConfig",
    "resolve_config",
    "remote_chat_completion",
    "remote_embeddings",
    "remote_tts",
    "remote_stt",
    "remote_image_generate",
]
