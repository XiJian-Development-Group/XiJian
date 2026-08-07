# macapp.md — macOS 桌面客户端

XiJian 的 macOS 原生客户端：SwiftUI 界面 + 内嵌 Core（PyInstaller onedir 打包的
Python Flask API 服务），Core 以子进程方式随 App 启停。

---

## 1. 目录结构

```
macapp/
├── Sources/                  # Swift 源码
│   ├── App.swift             # App 入口（@main）、AppDelegate（菜单栏、Core 生命周期）
│   ├── Info.plist            # App Info.plist
│   ├── Models/               # 数据模型（角色 / 世界 / 资源包 / 记忆等）
│   ├── Services/             # CoreManager（Core 进程管理）、APIClient（HTTP 封装）
│   ├── Theme/                # 主题个性化（AppTheme）
│   ├── ViewModels/           # 各界面视图模型（含 PackViewModel）
│   └── Views/                # SwiftUI 视图（对话 / 角色 / 世界 / 资源包 / 记忆 / 设置等，
│                             #   含 ImportPackSheet / PackListView）
├── Resources/
│   ├── Assets.xcassets       # 图标与颜色资源
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

## 5. 已知限制

- 默认端口 **18500**（与 Core 的 DEFAULT_PORT 一致，可在设置中修改）；配置端口被占用时
  Core 自动换端口，实际生效端口以 `run/xijian-<pid>.port` 为准；
- 发布签名：`project.yml` 中 `DEVELOPMENT_TEAM` 留空，正式分发前需用户填入
  自己的 Team ID 并配置签名证书；Entitlements 为最小非沙盒集合
  （禁库校验、允许未签名可执行内存、JIT、网络客户端）。
