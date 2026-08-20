# DevKit UI 开发指南

## 架构概览

```
devkit/ui/
├── src/
│   ├── main.ts              # 入口点
│   ├── App.ts               # 主应用类
│   ├── styles/
│   │   ├── main.css         # 基础样式 + 变量
│   │   └── components.css   # 组件样式
│   ├── components/
│   │   ├── Header.ts        # 头部
│   │   ├── TabManager.ts    # 标签页管理
│   │   ├── Sidebar.ts       # 侧边栏
│   │   └── TabManager.ts    # 标签页
│   ├── tabs/
│   │   ├── SubmitTab.ts     # 创作提交
│   │   ├── CharacterTab.ts  # 角色人设
│   │   ├── MemoryTab.ts     # 记忆条目
│   │   ├── WorldTab.ts      # 世界观
│   │   ├── ModelTab.ts      # 3D 预览
│   │   ├── VoiceTab.ts      # 声音克隆
│   │   ├── PlotTab.ts       # 剧情设计
│   │   ├── DialogTab.ts     # 对话样本
│   │   ├── MotionTab.ts     # 动作编辑
│   │   ├── AITab.ts         # AI 辅助
│   │   └── SettingsTab.ts   # 设置
│   ├── services/
│   │   ├── ApiService.ts    # Python 后端通信
│   │   └── StateManager.ts  # 状态持久化
│   └── types/
│       └── index.ts         # 类型定义
├── public/                  # 静态资源（复制到 dist）
├── vendor/                  # 三方库（three.js 等，复制到 dist）
├── index.html               # HTML 模板
├── package.json
├── tsconfig.json
├── vite.config.ts
└── dist/                    # 构建输出（供 PyInstaller 打包）
```

## 开发命令

```bash
# 安装依赖
npm install

# 开发服务器（热重载）
npm run dev

# 生产构建
npm run build

# 预览构建结果
npm run preview
```

## 关键设计决策

1. **模块化**：每个标签页独立文件，按需动态导入
2. **类型安全**：完整的 TypeScript 类型定义
3. **状态持久化**：localStorage 自动保存/恢复
4. **Python 通信**：通过 `pywebview.api` 调用后端
5. **响应式 CSS**：CSS 变量主题，支持深色模式
6. **无框架**：原生 TypeScript + Vite，无 React/Vue 依赖

## PyInstaller 集成

构建后的 `dist/` 目录包含：
- `index.html` - 入口 HTML
- `assets/` - 打包后的 JS/CSS（带哈希名）
- `vendor/` - three.js 等三方库

`build-devkit.sh` 会将 `dist/` 打包进 PyInstaller 的 onedir 产物中。

## 旧版文件（已弃用）

- `index.html` (根目录) - 单文件 95K 行版本
- `devkit.js` - 单文件 120K 行版本
- `devkit.css` - 单文件 21K 行版本

这些文件保留仅为兼容现有 PyInstaller 构建，**不再维护**。新功能请在 `src/` 目录开发。