# macapp.md — macOS 桌面客户端

XiJian 的 macOS 原生客户端：SwiftUI 界面 + 内嵌 Core（PyInstaller onedir 打包的
Python Flask API 服务），Core 以子进程方式随 App 启停。

---

## 1. 目录结构

```
macapp/
├── Sources/                  # Swift 源码
│   ├── App.swift             # App 入口（@main）、AppDelegate（菜单栏、Core 生命周期）
│   ├── BundleSupport.swift   # Bundle.xiJian（String Catalog 资源定位）
│   ├── Localization.swift    # loc() / Text(xj:) 本地化封装
│   ├── Info.plist            # App Info.plist
│   ├── Models/               # 数据模型（角色 / 世界 / 资源包 / 记忆等）
│   ├── Services/             # CoreManager（Core 进程管理）、APIClient（HTTP 封装）、
│   │                         #   WebSocketClient（/v1/ws 事件推送）、VoiceCallService（A6 通话 API）
│   ├── Theme/                # 主题个性化（AppTheme）
│   ├── ViewModels/           # 各界面视图模型（含 PackViewModel、VoiceCallViewModel）
│   └── Views/                # SwiftUI 视图（对话 / 角色 / 世界 / 资源包 / 记忆 / 设置等，
│                             #   含 ImportPackSheet / PackListView / VoiceCallView）
├── Resources/
│   ├── Assets.xcassets       # 图标与颜色资源
│   ├── Localizable.xcstrings # String Catalog（zh-Hans / en / ja，UI 文案唯一来源）
│   └── Core/                 # 内嵌 Core 产物（build-core.sh 生成，随 App 分发）
├── Tests/                    # 单元测试（逻辑层框架测试，无宿主）
├── scripts/
│   └── xijian-api.spec       # PyInstaller 打包 spec（macapp 侧专用）
├── project.yml               # XcodeGen 工程定义（含 DEVELOPMENT_TEAM 占位）
├── build-core.sh             # 打包 Core 并嵌入 Resources/Core/
├── build-macapp.sh           # 完整构建脚本（Core + 工程生成 + xcodebuild）
└── Entitlements.entitlements # 代码签名权限（非沙盒最小集合）
```

代码分层：逻辑层与 UI 层位于 `XiJianKit` framework（便于无宿主单元测试），
`XiJian` App target 仅保留 `@main` 入口。

## 2. 构建方式

```bash
./macapp/build-core.sh      # 1. 打包 Core
./macapp/build-macapp.sh    # 2. 完整构建（自动先执行 build-core.sh）
```

- **build-core.sh**：使用 conda 环境 `xijianBase`（`/opt/anaconda3/envs/xijianBase`）
  中的 PyInstaller，按 `macapp/scripts/xijian-api.spec` 以 **onedir** 模式打包
  Core（`build/dist/xijian-api/`），随后整体复制到 `macapp/Resources/Core/`
  （含可执行文件 `xijian-api`、`_internal/` 运行时、`config.toml`、`README.txt`）。
- **build-macapp.sh**：xcodegen 依据 `project.yml` 生成 `XiJian.xcodeproj`，
  再用 xcodebuild 编译。DerivedData 使用系统默认位置
  （`~/Library/Developer/Xcode/DerivedData/`），不落在项目目录内。
  支持 `--skip-core`（跳过 Core 重建快速迭代 UI）、`--release`、`--clean`。

## 3. 运行方式

App 启动时（`CoreManager.startCore`）：

1. 检查 bundle 内 `Resources/Core/xijian-api` 是否存在；
2. 若 `~/Library/Application Support/XiJian/Core/` 不存在则整目录复制；
   已存在时按需合并（`shouldMergeCore` 比较可执行文件与 `_internal` 目录总大小，
   有差异才重新合并；`config.toml` 等用户数据保留）；
3. 以 `<dir>/xijian-api --port <配置端口>` 启动 Core 子进程（默认 18500）；
4. 读取 `run/xijian-<pid>.port` 获取**实际生效端口**——配置端口被占用时 Core 不会退出，
   而是报告占用进程并自动切换到空闲端口（最多向上探测 100 个），真实端口通过该文件下发；
5. 在真实端口上轮询 `GET http://127.0.0.1:<实际端口>/healthz` 直到 200（默认超时 60 秒）；
6. 读取 `run/xijian-<pid>.token` 作为后续请求的 Bearer token。

退出时（菜单栏「退出」或 Cmd+Q）向子进程发送 SIGTERM，超时后依次 SIGINT、
SIGKILL，并同步等待退出。Core 的日志（stdout/stderr）汇入 App 内环形缓冲，
可在设置中查看或打开日志目录。设置中也可切换为自定义服务器
（不启动本机 Core，直接连接用户填写的地址与 token）。

## 4. 功能范围

- **对话**：流式对话，支持 Markdown 渲染（swift-markdown-ui）；
- **通话（A6）**：实时语音通话会话管理 —— 对话页顶部「通话」按钮 → 选角色 → 拨出（响铃）→
  接通后文本发送（服务端 STT→AI→TTS 管线，text 快捷路径）、barge-in 打断开关、歌唱请求、
  对话记录展示；经 WebSocket 订阅 `call.state_changed` / `call.event` 驱动状态刷新；
- **角色**：角色列表 / 详情 / 编辑；**导入资源包**（替换原「新建角色」入口，
  通过 Finder 选择 .7z/.zip 资源包导入，导入后列表刷新）；
- **世界**：世界列表 / 详情 / 创建与编辑；**导入资源包**（与角色页共用同一个导入面板）；
- **资源包**：侧边栏独立 tab —— 已安装资源包列表（名称 / 类型 / 版本 / 描述）、
  卸载（二次确认，删除包目录并移除运行时记录）、重新扫描（手动投放包后同步）、导入；
  从资源包导入的角色 / 世界在列表中带「资源包」来源徽标；
- **记忆**：记忆查看与管理（MemoryView / MemoryViewModel）；
- **备份**：备份与恢复（BackupSettingsView）；
- **设置**：Core 端口、自定义服务器与访问令牌、主题个性化、剧情与安全相关设置
  （SettingsView / PlotSettingsView / SafetySettingsView）。

## 5. A6 实时通话

A6 通话会话管理在 Core 侧为可运行 stub（状态机 idle → ringing → active → ended，
见 `core/xijian_api/routes/xijian_voice_calls.py`），macapp 侧提供完整 UI。

### 入口

对话页（ChatView）顶部工具栏「通话」按钮（Core 未运行时禁用）→ 弹出角色选择器
（VoiceCallCharacterPicker，从 `GET /v1/xijian/characters` 加载）→ 选择角色后以 sheet
弹出 VoiceCallView 并自动拨出。通话页关闭（onDisappear）时断开 WS；若通话仍活跃，
尽力调用 `end` 通知服务端挂断。

### 分层职责

- **VoiceCallService**（`Sources/Services/VoiceCallService.swift`）：`/v1/xijian/voice-calls`
  全部端点的 URLSession 封装（创建 / ring / accept / reject / end / speech / barge-in /
  song / events），请求构造、Bearer 认证、错误信封解析（`APIError`）与 APIClient 同风格；
  baseURL / token 取自 `CoreManager.shared.baseURL` / `CoreManager.shared.token`，
  不依赖 APIClient / CoreManager 改动。`VoiceCallServicing` 协议供 ViewModel 测试注入。
  `sendAudio(callId:audioData:language:)` 上传麦克风录音（body `{"audio_base64": "...",
  "language": "zh"}`，language 可选）；`VoiceCallAudioPayload.audioData(from:)` 为
  speech 事件 payload 的音频解析纯函数。
- **AudioRecorder**（`Sources/Services/AudioRecorder.swift`）：`AVAudioRecorder` 录麦克风到
  临时 WAV（16kHz 单声道 16-bit PCM，STT 标准输入格式），`stop()` 返回音频 Data 并清理临时
  文件；录音前请求权限（macOS 14+ `AVAudioApplication.requestRecordPermission`），
  无权限 / 启动失败 / 读文件失败均返回本地化错误文案。
- **VoiceCallViewModel**（`Sources/ViewModels/VoiceCallViewModel.swift`）：客户端通话状态机
  （idle / ringing / active / ended），组合 VoiceCallService（REST 推进）与 WebSocketClient
  （`call.state_changed` / `call.event` 事件驱动）；暴露 startCall / accept / reject / end /
  sendText / startRecording / stopRecordingAndSend / toggleBargeIn / sing / refresh / close；
  对话记录按 (role, turn) 与服务端 event_id 去重（REST 回显与 WS 推送可能重复）。
  录音中 `isRecording` / 播放中 `isPlayingAudio` 状态供 UI 指示；用户开始录音时停止正在
  播放的 assistant 语音（barge-in 语义）。
- **VoiceCallView**（`Sources/Views/VoiceCallView.swift`）：SwiftUI 通话界面 —— 状态头部
  （响铃 / 通话时长）、对话记录气泡、拨出 / 挂断控制、文本输入发送、麦克风录音按钮
  （mic.fill，点击开始录音、再点停止并发送，录音中变红 + 「录音中…」指示）、播放指示
  （speaker.wave.2.fill）、barge-in 开关、歌唱输入 sheet；样式跟随 ThemeSettings
  （主题色 / 气泡 / 圆角 / 字号 / 深浅色）。

### 真实语音链路（麦克风 → STT → AI → TTS → 扬声器）

1. 用户点击麦克风按钮开始录音（首次请求麦克风权限，`NSMicrophoneUsageDescription` 已在
   `Sources/Info.plist` 声明）；再点一次停止，`AudioRecorder.stop()` 取出 WAV Data。
2. `sendAudio` 以 `audio_base64`（+ `language: "zh"`）POST `.../speech`，服务端跑
   STT→AI→TTS 全流程（默认异步）。
3. AI 回复文字与 TTS 音频经 WS `call.event`（kind=speech，payload 含 `audio_base64`）
   推回；`ingestSpeech` 按 (role, turn) 去重后写入对话记录，assistant 语音解码为 Data
   用 `AVAudioPlayer` 播放（开始 / 结束更新 `isPlayingAudio`）。
4. 错误处理：STT 后端不可用时服务端返回 503 `{"ok":false,"error":"..."}`，UI 弹错误
   提示但通话继续；录音失败（无权限 / 启动失败 / 读文件失败）同样只提示不中断通话。

> 说明：macapp 侧语音链路已完整（录音 → 上传 → 事件消费 → 播放）；端到端出声还需服务端
> 接入真实 STT/TTS 后端（本机 Core 未装 mlx_whisper / mlx_audio 时，`.../speech` 返回
> STT 不可用错误）。文本快捷路径（`sendSpeech`）仍可用作无后端时的替代输入。

### 依赖

- **WebSocketClient**（commit e5b3e0f 基建）：每通通话按需建立 WS 连接（`startCall` 时
  `connect`，`close` 时 `disconnect`），服务端广播的事件经 `call_id` 过滤只消费本通话。
- **AVFoundation**：`AudioRecorder`（AVAudioRecorder / 权限请求）与 `VoiceCallViewModel`
  （AVAudioPlayer 播放）使用；App 需在 `Info.plist` 声明 `NSMicrophoneUsageDescription`
  （已在 `Sources/Info.plist` 添加，中英双语描述）。

## 6. 本地化（String Catalog：zh-Hans / en / ja）

macapp 全部用户可见文案（视图、日志、错误消息、状态文本）已迁移到 **String Catalog**
（`macapp/Resources/Localizable.xcstrings`，sourceLanguage = zh-Hans，en / ja 全量翻译），
随 `XiJianKit` framework 打包。系统语言为中文 / 英文 / 日文时自动切换界面语言。

### 资源与封装

- **String Catalog**：`Resources/Localizable.xcstrings`（key 即中文原文，含 `%@` / `%lld` 占位符）。
- **Bundle 定位**：`Sources/BundleSupport.swift` —— `Bundle.xiJian` 指向 XiJianKit 框架 bundle；
  所有本地化查找必须显式使用它（App target 的 main bundle 内没有该资源）。
- **封装**：`Sources/Localization.swift`
  - `loc("设置")` / `loc("网络错误：%@", detail)` / `loc("Core 运行中 · 端口 %lld", port)` ——
    非 UI 文案（错误消息、日志、状态文本、菜单标题等），返回已本地化 `String`；
    带参数时 key 必须用占位符形式（**不要**用 Swift 字符串插值拼接）。
  - `Text(xj: "设置")` / `Text(xj: "Core 运行中 · 端口 \(port)")` —— SwiftUI 文本
    （LocalizedStringKey 插值自动匹配 catalog 的 `%lld` / `%@`）。
- SwiftUI 控件的标题（Button / Label / Section / Picker / Toggle / TextField / navigationTitle /
  alert / confirmationDialog / help 等）直接传 `loc(...)` 的 `String`（利用 StringProtocol 重载），
  避免 LocalizedStringKey 默认走 main bundle 导致查找不到。

### 新增文案的步骤

1. 在源码中用 `loc("新文案")` 或 `Text(xj: "新文案")` 书写；带参数用占位符 key（如 `"共 %lld 条"`）。
2. 打开 `Resources/Localizable.xcstrings`（Xcode 内）或直接编辑 JSON，
   补上新 key 与 en / ja 翻译（质量与既有条目一致；路径、代码符号等保留原文）。
3. 若源码文案与 catalog key 不一致（换行 / 空格 / 标点差异），优先改源码匹配 catalog。
4. 构建并全量测试（`xcodebuild -scheme XiJian build` + `test`）。

> 说明：枚举的持久化 rawValue（如主题外观模式「跟随系统 / 浅色 / 深色」、气泡样式、
> 侧边栏 Tab 名）与服务器返回的数据字段（role、status 等）**不做**本地化改写，
> 展示时经 `displayName` / `loc()` 转换；`AppTheme` 预设色名同样以 `loc()` 展示。

## 7. 已知限制

- 默认端口 **18500**（与 Core 的 DEFAULT_PORT 一致，可在设置中修改）；配置端口被占用时
  Core 自动换端口，实际生效端口以 `run/xijian-<pid>.port` 为准；
- 发布签名：`project.yml` 中 `DEVELOPMENT_TEAM` 留空，正式分发前需用户填入
  自己的 Team ID 并配置签名证书；Entitlements 为最小非沙盒集合
  （禁库校验、允许未签名可执行内存、JIT、网络客户端）。
