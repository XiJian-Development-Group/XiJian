# 核心模块 Windows 支持情况说明

> 基于《Dev. Function List 功能清单 v2.1》及代码库现状整理  
> 文档版本：v1.0  
> 维护者：隙间开发组  
> 更新时间：2026-07-13

---

## 总览

| 模块 / 功能 | Windows 支持 | 备注 |
|------------|-------------|------|
| **A2. OpenAI 兼容 API 模块** | ✅ 完全支持 | 纯 Python + Flask，跨平台 |
| **A1. 记忆系统** | ✅ 完全支持 | SQLite + 本地文件，跨平台 |
| **A3. 角色与状态系统** | ✅ 完全支持 | 纯逻辑层，跨平台 |
| **A4. 模拟世界系统** | ✅ 完全支持 | 纯逻辑层，跨平台 |
| **A5. 安全模块** | ⚠️ 部分支持 | 过载防护硬件指标采集需适配 |
| **A6. 实时通话** | ⚠️ 部分支持 | 依赖 MLX/GGUF 后端，Windows 需 GGUF |
| **A7. 主动发起聊天/通话** | ❌ **不支持** | 依赖 macOS/iOS 系统级通知与后台保活机制 |
| **A8. 桌宠 / 动态壁纸** | ❌ **不支持** | 依赖 macOS SpriteKit/WindowServer 私有 API |
| **B. Touch Bar & Dynamic Island** | ❌ **不支持** | 仅 Apple 硬件专属 |
| **C. Development Kit (开发者工具)** | ✅ 支持 | 基于 pywebview (WebView2)，已适配 Windows |

---

## 详细逐项说明

### A7. 主动发起聊天或通话 —— **仅支持 macOS 与 iOS/iPadOS**

**功能清单来源**：`Dev. Function List功能清单v2.md` 第 A7 节

**不支持 Windows 的技术原因**：
1. **系统级通知交互**：依赖 `UserNotifications.framework` (macOS) / `UNUserNotificationCenter` (iOS) 实现“通知中心直接回复/接听”，Windows 的 Toast 通知不支持自定义按钮回调到后台进程。
2. **后台保活机制**：
   - macOS：`NSApplication` 可注册为 `LSUIElement=1` 的 Agent，长期驻留后台并接收远程推送 / 定时唤醒。
   - iOS：通过 `Background Modes` (VoIP / Remote Notification) 实现真后台唤醒。
   - Windows：UWP 后台任务有严格资源配额与 30 秒执行上限；Win32 无统一后台保活 API，易被电源管理休眠。
3. **来电界面 (CallKit / CallKit-like)**：iOS/macOS 提供系统级全屏来电 UI，Windows 无对应原生能力。

**代码位置**：`core/xijian_api/stubs/characters.py` 相关 proactive 逻辑、`devkit/` 无对应实现。

**替代方案（Windows）**：
- 仅支持“轮询拉取”模式：客户端定时请求 `/v1/xijian/characters/{id}/proactive`，由前端弹 Toast 提醒。
- 无法实现“系统来电界面直接接听”。

---

### A8. 将角色作为桌宠或动态壁纸 —— **仅支持 macOS**

**功能清单来源**：`Dev. Function List功能清单v2.md` 第 A8 节

**不支持 Windows 的技术原因**：

#### 桌宠
1. **透明点击穿透窗口**：macOS 通过 `NSWindow` 设置 `ignoresMouseEvents` + `backgroundColor = NSColor.clear` 实现“点击穿透桌面级窗口”，Windows 需 `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST` 组合，但：
   - Win32 分层窗口在 DPI 缩放、多显示器、HDR 下渲染异常频发。
   - 无法在“桌面图标层”下方稳定绘制（Windows 桌面层级固定：Wallpaper → Icons → Desktop Window → App Windows）。
2. **全局输入监听与注入**：macOS 使用 `CGEventTap` (Quartz) 监听/注入键鼠，需辅助功能授权；Windows 需 `SetWindowsHookEx(WH_KEYBOARD_LL/WH_MOUSE_LL)` + 管理员权限，且易触发杀毒软件拦截。
3. **三维模型渲染集成**：桌宠使用 three.js + @pixiv/three-vrm (WebGL) 在透明 WebView 中渲染。macOS WKWebView 支持透明背景 (`setDrawsBackground:NO`) 且性能稳定；Windows WebView2 透明背景需 `WS_EX_LAYERED` + `DWMWA_USE_IMMERSIVE_DARK_MODE` 组合，在高 DPI / 多显示器下闪烁、穿透失效。

#### 动态壁纸
1. **Wallpaper Engine SDK / Windows 动态壁纸机制**：Windows 无原生“动态壁纸”API，第三方方案 (Wallpaper Engine, Lively Wallpaper) 均为闭源/付费/需安装额外运行时，无法作为产品内置分发。
2. **场景模拟渲染**：A8.3 提到“壁纸是模拟世界内场景 (时间变化 + 环境模拟)”，需实时 3D 渲染输出到桌面背景层。macOS 可用 `NSVisualEffectView` + `CAOpenGLLayer` 无缝融合；Windows 必须注入 `Explorer.exe` 或使用 `IDesktopWallpaper` COM 接口，极不稳定且触发 UAC。

**代码位置**：`devkit/ui/` 相关三维渲染组件、`core/xijian_api/routes/ws_routes.py` 中 `desktop_pet.*` 事件处理。

**替代方案（Windows）**：
- 仅提供“窗口化桌宠”模式：普通 PyWebView 窗口置顶、去边框、可拖拽，不穿透点击。
- 动态壁纸：建议用户自行配合 Wallpaper Engine 导出视频/GIF 循环播放，核心不提供原生支持。

---

### B. Apple Touch Bar & Dynamic Island Support —— **仅支持 Apple 设备**

**功能清单来源**：`Dev. Function List功能清单v2.md` 第 B 节

**不支持 Windows 的技术原因**：
- Touch Bar：MacBook Pro 专属硬件，通过 `NSTouchBar` API 交互，Windows 无对应硬件。
- Dynamic Island：iPhone 14 Pro+ 专属硬件/系统 UI，通过 `LiveActivity` + `ActivityKit` 实现，Windows 无对应概念。

**代码位置**：`devkit/` 无相关实现，属于客户端 App (XiJian macOS/iOS App) 范畴。

---

### A5.4 系统过载防护 —— **Windows 需适配硬件指标采集**

**功能清单来源**：`Dev. Function List功能清单v2.md` 第 A5.4 节

**现状**：
- 核心逻辑在 `core/xijian_api/stubs/overload.py`，为纯 Python 实现，**逻辑层跨平台**。
- 当前阈值表针对 Apple Silicon (CPU/GPU/ANE/SoC Temperature) 设计，**Windows 需替换采集源**。

**Windows 需适配的指标采集**：
| 指标 | macOS 实现 | Windows 替代方案 |
|------|-----------|-----------------|
| CPU 持续占用 | `psutil.cpu_percent(interval=1)` | 同 `psutil`，跨平台通用 ✅ |
| 内存压力 | `psutil.virtual_memory().percent` | 同 `psutil` ✅ |
| SoC 温度 | `powermetrics --samplers smc` (需 sudo) 或 `istats` | **无统一 API**；可用 `wmi` 查询 `MSAcpi_ThermalZoneTemperature` (需管理员) 或第三方库 `librehardwaremonitor` / `openhardwaremonitor` |
| GPU/ANE 占用 | `powermetrics --samplers gpu_power` | **无统一 API**；NVIDIA 可用 `nvidia-smi` / `pynvml`，AMD/Intel 无标准化用户态接口 |
| Swap | 文档明确“不限制” | 同策略 ✅ |

**建议**：
- Windows 版本暂仅启用 **CPU + 内存** 双指标过载防护，温度/GPU 指标标记为“不可用”，不参与判定。
- 如需完整功能，需集成 `OpenHardwareMonitorLib` (LGPL) 或 `LibreHardwareMonitor` (MIT) 作为可选依赖。

---

### A6. 实时通话 —— **Windows 仅支持 GGUF 后端**

**功能清单来源**：`Dev. Function List功能清单v2.md` 第 A6 节

**现状**：
- STT/TTS/Chat 后端在 `core/xijian_api/ai/backends/` 下分 `mlx` / `gguf` / `mock` 三套。
- **MLX 仅支持 Apple Silicon (Metal)**，**Windows 必须使用 GGUF (llama.cpp) 后端**。
- `config.toml` 中 `[backends.*]` 已配置 `fallbacks = ["gguf"]`，Windows 环境下会自动回落。

**已知限制**：
- MeloTTS (A2 决策) 目前仅有 MLX 实现，Windows 需改用 `piper-tts` / `xtts-v2` (GGUF) 或云端 API。
- DiffSinger (歌声合成) 无 Windows 原生 GGUF 版本，需 Docker/WSL2 或云端。

---

### C. Development Kit (开发者工具) —— **Windows 已支持**

**功能清单来源**：`Dev. Function List功能清单v2.md` 第 C 节

**技术栈**：Python + pywebview (Windows 使用 WebView2 / Edge Runtime)  
**验证状态**：`devkit/main.py` 已在 Windows 10/11 上通过 CI 测试，打包脚本 `build-devkit.sh` 对应 `build-devkit.ps1` (待补充)。

---

## 迁移建议清单（若需在 Windows 运行核心 API 服务）

| 项 | 状态 | 备注 |
|----|------|------|
| 修改 `config.toml` `[server] host = "0.0.0.0"` | ✅ 已完成 (v1.0 默认) | 允许外部访问 |
| 确保 `XIJIAN_API_PORT` 环境变量设置 | ✅ 必须 | 启动入口强制要求 |
| 依赖 `mlx` 的模型条目改为 `backend = "gguf"` | ⚠️ 部署时手动修改 | `config.toml` 中 `[[models]]` 段 |
| 过载防护：仅启用 CPU/内存监控 | ⚠️ 代码需改动 | `stubs/overload.py` 增加平台判断分支 |
| TTS/STT/歌声合成：准备 GGUF 模型或云端 API Key | ⚠️ 部署时准备 | 无 MLX 可用 |
| 开发者工具：运行 `python -m devkit` | ✅ 可用 | 需安装 WebView2 Runtime |

---

## 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-13 | 初版，基于功能清单 v2.1 与代码库现状整理 |