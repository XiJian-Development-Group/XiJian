# 剧情系统 P0-P4 冻结归档（2026-10-11）

## 冻结背景

隙间从「二次元聊天产品」转型为「Hermes Agent 桌面管理器」（参见 `docs/decisions/2026-10-11-pivot-to-hermes-manager.md`）。
二次元聊天改为 Hermes 调用的「附属功能」，通过修改 Hermes SOUL.md 人设实现。
原计划的剧情系统（Galgame）整套功能暂停，未来视需求复活。

## 投入历史

| 阶段 | Commit | 内容 |
|---|---|---|
| P0 | `b8552ed` | 数据模型与求值器地基 |
| P1 | `a6a9d0a` | 播放契约 + 32 档存档系统 |
| P2 | `5b2bb89` | DevKit GUI 内嵌剧本编辑器 + 资产管理 |
| P3 | `f027674` | 导出剧情包自包含打包 |
| P4 | `4d1dede` | 文档同步 |

## 涉及代码路径（冻结前快照，tag: `plot-system-snapshot-before-freeze`）

### 后端（Core，Python）
- `core/xijian_api/stubs/plot_runtime.py`（1459 行，运行时核心）
- `core/xijian_api/stubs/condition_eval.py`（条件求值器）
- `core/xijian_api/routes/xijian_plot.py`（HTTP 端点）
- `core/xijian_api/app.py` / `state.py`（绑定与状态）
- `core/tests/test_xijian_plot_*.py` / `test_xijian_condition_eval.py`（测试 6 个）

### 编辑器（DevKit，Python）
- `devkit/plot_editor.py`
- `devkit/motion_editor.py`
- `devkit/character_editor.py`（同时承担人设编辑器）
- `devkit/tests/test_plot_*.py`

### 桌面（macapp，Swift）
- `macapp/Sources/Views/PlotSettingsView.swift`
- `macapp/Sources/Views/PackListView.swift`
- `macapp/Sources/ViewModels/PackViewModel.swift`
- `macapp/Tests/Plot*.swift`

## 未来复活路径

1. 切到归档分支：
   ```bash
   git checkout archive/plot-system-P0P4-2026-10-11
   # 或：
   git checkout plot-system-snapshot-before-freeze -- core/xijian_api/stubs/plot_runtime.py ...
   ```
2. 评估那时隙间的状态（Swift 主项目 vs Python 主项目）。
3. 如果是 Swift 主项目，需要把 `plot_runtime.py` 用 Swift 重写（约 1500 行）。

## 设计文档位置

- `docs/openapi.yaml` 内 galgame 段落（冻结时仍在 main 分支，可能已被 Phase 1 删除）
- `docs/notes.md` 历次剧情相关条目
- `docs/api.md` 剧情相关端点（冻结时仍在 main 分支，可能已被 Phase 1 删除）

## 维护者须知

- 冻结 ≠ 删除：代码在归档分支和 tag 中完整保留
- 重启开发前先评估「Hermes SOUL.md 是否能表达等价剧情体验」
- 如果重启，需要重新出 design doc 并 user review
