# 依赖清单（Deps）

> 隙间（XiJian）开发组 · 本文档列出项目当前使用与计划使用的第三方依赖、开源项目及其用途。
>
> 文档版本：v1.0（2026-08-06）
> 维护者：隙间开发组
> 说明：许可证以各项目官方仓库 / PyPI 为准，本清单仅供快速参考。

---

## 目录

- [1. Core API 服务器（core/）](#1-core-api-服务器-core)
  - [1.1 运行时依赖](#11-运行时依赖)
  - [1.2 可选依赖](#12-可选依赖)
- [2. 开发者工具 DevKit（devkit/）](#2-开发者工具-devkit-devkit)
  - [2.1 运行时依赖](#21-运行时依赖)
  - [2.2 可选 ML 依赖](#22-可选-ml-依赖)
  - [2.3 AI 后端扩展包（全模态 Backend）](#23-ai-后端扩展包全模态-backend)
- [3. macOS 客户端（macapp/）](#3-macos-客户端-macapp)
- [4. AI 模型与推理引擎（技术选型）](#4-ai-模型与推理引擎技术选型)
- [5. 系统级工具](#5-系统级工具)
- [6. 说明](#6-说明)

---

## 1. Core API 服务器（core/）

Core 是隙间的本地 API 服务器（Python ≥ 3.11），声明于 `core/pyproject.toml`。

### 1.1 运行时依赖

| 依赖 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| Flask | >=3.0 | BSD-3-Clause | Web API 框架 |
| Flask-Sock | >=0.7 | Apache-2.0 | WebSocket 支持（实时对话、流式响应） |
| simple-websocket | >=1.0 | MIT | WebSocket 底层实现 |
| waitress | >=3.0 | ZPL-2.1 | 生产级 WSGI 服务器 |
| psutil | >=5.9 | BSD-3-Clause | 系统资源监控（内存/CPU，过载保护） |
| py7zr | >=0.21 | LGPL-2.1-or-later | 资源包系统：7z 固实归档读写（zip 由标准库 zipfile 兜底） |

### 1.2 可选依赖

| 依赖 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| pywebview | >=5.0 | BSD-3-Clause | DevKit 独立窗口 GUI（macOS 依赖 PyObjC，Windows 依赖 pythonnet，Linux 依赖 webkitgtk） |
| pytest | >=8 | MIT | 测试框架 |
| pytest-cov | — | MIT | 测试覆盖率 |
| httpx | >=0.27 | BSD-3-Clause | API 测试客户端 |
| websocket-client | >=1.7 | Apache-2.0 | WebSocket 测试客户端 |
| PyInstaller | >=6.0 | GPL-2.0-or-later（含 bootloader 例外） | 打包为独立可执行文件 |

---

## 2. 开发者工具 DevKit（devkit/）

DevKit 不通过 wheel 分发，以 PyInstaller 二进制形式发布。运行时依赖见 `devkit/requirements.txt`。

### 2.1 运行时依赖

| 依赖 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| pywebview | >=5.0 | BSD-3-Clause | 原生 webview 窗口（macOS WKWebView / Windows WebView2 / Linux webkitgtk） |
| py7zr | >=0.21 | LGPL-2.1-or-later | 7z 固实归档读写（缺失时 zip 兜底） |

### 2.2 可选 ML 依赖

| 依赖 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| mlx-audio | >=0.4 | MIT | Apple Silicon 上的 MLX 音频/TTS（如 MeloTTS 等） |
| llama-cpp-python | >=0.2.70 | MIT | GGUF / llama.cpp 推理（本地 LLM 与 TTS 后端） |

### 2.3 AI 后端扩展包（全模态 Backend）

以下为 Core 的 AI Backend（`docs/AIBackend.md`）实际使用的 MLX / GGUF 扩展包，按需安装，任一缺失时对应模态自动降级。

| 依赖 | 许可证 | 用途 |
| --- | --- | --- |
| mlx-lm | MIT | MLX 纯文本 chat + embedding 回退 |
| mlx-vlm | MIT | MLX 视觉语言模型（图像/视频理解） |
| mlx-embeddings | MIT | MLX 原生 embedding |
| mlx-audio | MIT | MLX TTS（语音合成，含情感参数） |
| mlx-whisper | MIT | MLX STT（语音转文字） |
| diffusers | Apache-2.0 | 图像生成回退（MPS 后端） |
| torch | BSD-3-Clause | diffusers 后端（MPS） |
| pywhispercpp | MIT | GGUF STT 后端（whisper.cpp 绑定） |
| stable-diffusion-cpp-python | MIT | GGUF 图像生成（SD/SDXL/Flux 等，可选） |

---

## 3. macOS 客户端（macapp/）

macOS 客户端使用 SwiftUI 构建（Xcode 工程，`macapp/XiJian.xcodeproj`）。

| 依赖 | 许可证 | 用途 |
| --- | --- | --- |
| swift-markdown-ui（MarkdownUI） | MIT | Markdown 渲染（角色对话 / 文档展示） |
| SwiftUI / Foundation 等系统框架 | Apple | 应用界面与基础能力（非第三方） |

---

## 4. AI 模型与推理引擎（技术选型）

以下来自功能清单 v2 的技术决策。**注意：模型相关的选型后续可能调整（v2.1 决策说明），请以最新功能清单为准。**

### 4.1 3D 角色渲染

| 依赖 | 许可证 | 用途 |
| --- | --- | --- |
| three.js | MIT | 3D 渲染运行时 |
| @pixiv/three-vrm | MIT | VRM 1.0 模型加载与驱动（BlendShape / 骨骼动画） |
| Godot 4 + godot-vrm（备选） | MIT | 桌面端备用引擎 |
| UniVRM（备选） | MIT | Unity 备用方案 |

### 4.2 语音（TTS / 歌声合成）

| 依赖 | 许可证 | 用途 |
| --- | --- | --- |
| MeloTTS（MyShell） | MIT | 对话 TTS，中英混合、情感可控，MLX/CPU 实时 |
| DiffSinger（OpenVPI） | Apache-2.0 | 歌声合成（SVS），OpenUtau 集成 |
| CosyVoice 2（备选，未启用） | Apache-2.0 | 对话 TTS 备选 |

### 4.3 语言模型与嵌入

| 依赖 | 许可证 | 用途 |
| --- | --- | --- |
| Qwen2.5-7B（Q4_K_M） | Apache-2.0 | 主对话模型 |
| bge-m3 | MIT | 文本嵌入（中文 RAG，1024 维） |
| llama.cpp / GGUF | MIT | 本地模型推理后端 |
| vLLM（备选） | Apache-2.0 | 云端/高吞吐推理备选 |

### 4.4 数据与协议

| 依赖 | 许可证 | 用途 |
| --- | --- | --- |
| SQLite | Public Domain | 本地数据持久化（记忆、角色、世界） |
| MCP（Model Context Protocol） | MIT | AI 工具调用与桌面控制通道（协议规范） |

---

## 5. 系统级工具

| 工具 | 许可证 | 用途 |
| --- | --- | --- |
| ffmpeg | LGPL-2.1-or-later（可选 GPL） | 音视频处理（录音、转码、音频合成管线） |
| zstd | BSD-3-Clause | 高压缩比归档压缩（备份、资源包） |

---

## 6. 说明

- 本文档随项目演进持续更新；新引入的第三方依赖应同步补充到此清单。
- 各依赖的完整许可文本请以官方仓库 / PyPI 元数据为准。
- 如有遗漏，请提交 Pull Request，或通过 README 中的联系方式告知我们。项目制作较为辛苦，难免有遗漏，非常抱歉。
