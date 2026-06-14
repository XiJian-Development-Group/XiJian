"""Stub audio — synth (TTS) / transcribe (STT) / translate."""

from __future__ import annotations

# A minimal valid MP3 frame header — just enough for clients to think
# the body looks like audio/mpeg without being a real recording.
# 4 bytes MPEG sync + 3 bytes header + 1 byte padding.  We then append
# a few bytes of "silence" so the payload is non-trivially short.
_MP3_HEADER = b"\xff\xfb\x90\x00" + b"\x00\x00\x00\x00"
_MP3_SILENCE = b"\x00" * 96


def synth(text: str, *, voice: str = "default", response_format: str = "mp3") -> bytes:
    """Return stub TTS bytes (a minimal MP3 header + silence)."""
    payload = _MP3_HEADER + _MP3_SILENCE
    # Tiny length variation so the response isn't byte-identical for
    # different inputs — keeps tests from being too trivial.
    payload += str(len(text)).encode("ascii") + b"\x00" * 8
    return payload


def transcribe(audio: bytes | None = None, *, response_format: str = "json") -> str | dict:
    """Return a stub transcription result."""
    text = "这是 stub 转写结果"
    if response_format == "text":
        return text
    return {"text": text}


def translate(audio: bytes | None = None, *, response_format: str = "json") -> str | dict:
    """Return a stub translation result."""
    text = "This is a stub translation result"
    if response_format == "text":
        return text
    return {"text": text}


__all__ = ["synth", "transcribe", "translate"]