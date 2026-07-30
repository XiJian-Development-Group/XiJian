"""OpenAI 兼容远程后端包。

该子包提供通过 OpenAI 兼容 HTTP API 远程连接的实现，涵盖所有支持的任务类型：
聊天、嵌入、语音合成/识别、图像生成和视频生成。

共享的 HTTP 客户端层在 :mod:`_client` 中 —— 后端不直接处理 HTTP 调用。

OpenAI-compatible remote backends package.

This sub-package provides implementations that connect to
OpenAI-compatible HTTP APIs remotely, covering all supported task
types: chat, embeddings, TTS, STT, image generation, and video
generation.

The shared HTTP client layer lives in :mod:`_client` — backends never
touch HTTP directly.
"""
