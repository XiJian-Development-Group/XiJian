# 隙间 · XiJian Manager

Hermes Agent 的 macOS 桌面管理器。全 Swift 实现，单 macOS 平台。

| 项目 | 说明 |
|------|------|
| **名称** | 隙间 (XiJian Manager) |
| **类型** | Hermes Agent 桌面 GUI |
| **语言** | 简体中文 / English / 日本語 |
| **代码仓库** | [github.com/Mofan2020/XiJian](https://github.com/Mofan2020/XiJian) |
| **目标平台** | macOS 26.0+ (Apple Silicon) |

## 是什么

隙间是 Hermes Agent 的桌面前端：把 Hermes 那些需要改配置文件或命令行的操作（安装 / 启停 / 切换人设 / 管理记忆 / MCP / Skill 等）变成点点点的桌面应用。

- **后台常驻**：菜单栏图标（点开主窗口）
- **人设切换**：通过修改 Hermes SOUL.md / MEMORY.md 实现（二次元聊天作为附属功能）
- **窗口三栏**：状态概览 / 内嵌终端 / Dashboard 快捷入口 / 设置

## 当前状态

macOS MVP（Phase 1-5 全部完成）：
- 装/卸/备/更 Hermes（GitHub release 下载 + ditto 解压）
- 启动/停止/重启 Hermes gateway
- Dashboard 一键开关
- 内嵌终端（PTY → Hermes CLI 子进程）
- 多 workspace profile 切换（SOUL.md / MEMORY.md / USER.md 编辑）
- MCP server 管理 / SkillHub / Token 监控 / CN 网络适配 + 测速
- 主题系统 / 模型管理 / cmd+滚轮调字号 / Dashboard 子页管理 / Web Search 切换
- Agent 群聊（多 profile 并行）
- 三语 i18n（zh-Hans / en / ja）
- **77 个单元测试全过，Debug macOS BUILD SUCCEEDED**

## 快速开始

```bash
git clone https://github.com/Mofan2020/XiJian.git
cd XiJian/macapp
xcodegen generate
xcodebuild -project XiJianManager.xcodeproj -scheme XiJianManager -configuration Debug build
open build/Debug/XiJianManager.app
```

## 开发命令

```bash
# 跑全部单元测试
xcodebuild -project XiJianManager.xcodeproj -scheme XiJianManager test

# 仅 build（不跑测试）
xcodebuild -project XiJianManager.xcodeproj -scheme XiJianManager build
```

## 架构

```
macapp/Sources/
  ├── Services/        # HermesInstaller / Process / LogTailer / PTYTerminal / ANSIParser / HermesCommandRunner / AppLifecycleManager
  ├── Models/          # HermesInstall / InstallError / ThemeSettings / UserProfileSettings / BackgroundSettings
  ├── Views/           # ContentView + StatusView / TerminalView / DashboardView / SettingsView
  ├── Theme/           # AppTheme / DesignSystem
  ├── App.swift        # @main + AppDelegate（菜单栏）
  └── Localization.swift
macapp/Tests/         # XCTest（22 个测试，覆盖 installer/process/tailer/pty/ansi/commands）
```

## 设计原则

1. **macOS only**：单一目标平台，最佳 macOS 体验（按用户偏好牺牲跨平台维护成本）
2. **全 Swift**：Core / DevKit Python 已删除（备份在 `archive/plot-system-P0P4-2026-10-11/`）
3. **人设 = 文件**：角色 / 世界 / 记忆全部走纯 markdown / YAML，手改即时生效
4. **Hermes 进程由 macOS 管理**：不内嵌 Python runtime，PTY → Hermes CLI 子进程

## 不做什么

- Windows / Linux 版本（明确范围外）
- 3D 模型、桌宠、壁纸模式（之前的承诺已删除）
- 桌面宠物 / 主动聊天收件箱（未实现功能已从 README 删除）

## 剧情系统

隙间之前包含一套剧情系统（Galgame 体验），代码已备份在 `archive/plot-system-P0P4-2026-10-11/` 分支。**未来可能复活**，但当前不做。

## License

MIT
