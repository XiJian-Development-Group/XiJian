# DevKit 开发者工具 — 开发规范与实现方案

> 本文档定义 DevKit（开发者工具）的架构设计、模块划分、UI 规范与实现约定。
> 对应于 Problems.md 中"添加编辑器与预览功能"的需求。

---

## 1. 总体设计

### 1.1 定位

DevKit 是**独立的 Pywebview 桌面应用**，不与主 API（Flask）共享进程或 import。所有功能在本地完成，产出文件可通过内置的邮件提交管道（C5）对外投递。

### 1.2 功能模块

| 模块            | Python 文件                 | UI Tab          | 职责                               |
| --------------- | --------------------------- | --------------- | ---------------------------------- |
| 提交管道 (C5)   | `__init__.py` / `api.py`   | 创作提交         | 文件选择、7Z 打包、SMTP 投递        |
| 角色编辑器      | `character_editor.py`       | 角色人设         | 人设文档、声音配置、标签等 CRUD     |
| 记忆条目编辑器  | `memory_editor.py`          | 记忆条目         | 长期/短期记忆条目编写与管理          |
| 世界观编辑器    | `world_editor.py`           | 世界观           | 世界观文档、世界配置编辑             |
| 3D 模型预览     | `model_viewer.py`           | 3D 预览          | VRM 文件加载、three.js 渲染         |
| 声音克隆        | `voice_cloner.py`           | 声音克隆         | 音频录制/上传、声音样本管理          |

### 1.3 数据存储

所有编辑器产出以 JSON + Markdown 文件存放于用户指定目录（默认 `~/隙间Dev/`）：

```
~/隙间Dev/
├── characters/
│   ├── char_xxx.json           # 角色档案
│   └── char_xxx/
│       └── persona.md          # 人设文档
├── memories/
│   └── char_xxx/
│       ├── entries.json        # 记忆条目列表
│       └── entries_backup.json
├── worlds/
│   ├── world_xxx.json          # 世界观档案
│   └── world_xxx/
│       └── world_doc.md        # 世界观文档
├── models/
│   └── *.vrm                   # 引用的 3D 模型路径记录
└── voices/
    └── char_xxx/
        ├── meta.json           # 声音样本元数据
        └── sample.wav          # 参考音频
```

### 1.4 import 约束

- DevKit 内部各模块互相调用时使用 `from devkit.xxx import ...`
- **不得** import `xijian_api`、`flask` 及其依赖
- 第三方依赖仅限 `pywebview` 和 `py7zr`（见 `requirements.txt`）

---

## 2. 后端模块设计

### 2.1 character_editor.py

角色人设编辑器的纯函数后端。

**数据结构**：
```python
{
    "id": "char_xxx",           # 自动生成
    "name": "Yuki",
    "display_name": "雪",
    "persona_doc": "...",       # Markdown 人设文档
    "voice_profile": "melo_zh_female_warm_v1",
    "default_emotion": "neutral",
    "tags": ["tsundere", "student"],
    "models": [],                # 关联的 3D 模型文件路径
    "created_at": "ISO8601",
    "updated_at": "ISO8601"
}
```

**函数清单**：
- `list_characters() -> list[dict]`
- `get_character(char_id) -> dict | None`
- `save_character(data) -> dict`
- `delete_character(char_id) -> bool`
- `export_character_for_submit(char_id) -> dict`

### 2.2 memory_editor.py

记忆条目编辑器的纯函数后端。

**数据结构**：
```python
{
    "id": "mem_xxx",
    "character_id": "char_yuki",
    "type": "long" | "short",
    "content": "用户喜欢草莓味的冰淇淋",
    "importance": 0.8,          # 0.0 ~ 1.0
    "tags": ["preference", "food"],
    "source": "manual",
    "created_at": "ISO8601",
    "updated_at": "ISO8601"
}
```

**函数清单**：
- `list_entries(character_id) -> list[dict]`
- `get_entry(entry_id) -> dict | None`
- `save_entry(data) -> dict`
- `delete_entry(entry_id) -> bool`
- `export_entries_for_submit(character_id) -> list[dict]`

### 2.3 world_editor.py

世界观编辑器的纯函数后端。

**数据结构**：
```python
{
    "id": "world_xxx",
    "name": "现代东京",
    "world_doc": "...",         # Markdown 世界观文档
    "config": {
        "tags": ["modern", "school"],
        "characters": [],
        "rules": "..."
    },
    "created_at": "ISO8601",
    "updated_at": "ISO8601"
}
```

**函数清单**：
- `list_worlds() -> list[dict]`
- `get_world(world_id) -> dict | None`
- `save_world(data) -> dict`
- `delete_world(world_id) -> bool`
- `export_world_for_submit(world_id) -> dict`

### 2.4 model_viewer.py

3D 模型预览后端。

**函数清单**：
- `list_models() -> list[dict]` — 列出已注册的模型文件
- `register_model(path) -> dict` — 注册一个模型文件
- `get_model_info(path) -> dict` — 读取 VRM 元信息
- `unregister_model(model_id) -> bool`

### 2.5 voice_cloner.py

声音克隆后端。

**数据结构**：
```python
{
    "id": "voice_xxx",
    "character_id": "char_yuki",
    "name": "default",
    "engine": "melo-tts",
    "sample_path": "/path/to/sample.wav",
    "duration_sec": 30.0,
    "params": {},
    "created_at": "ISO8601"
}
```

**函数清单**：
- `list_voices(character_id) -> list[dict]`
- `get_voice(voice_id) -> dict | None`
- `save_voice(data, audio_data) -> dict` — 保存声音样本和元数据
- `delete_voice(voice_id) -> bool`

---

## 3. UI 规范

### 3.1 导航结构

顶部导航栏增设 Tab 切换：**创作提交 · 角色人设 · 记忆条目 · 世界观 · 3D 预览 · 声音克隆**

### 3.2 每 Tab 布局

每个编辑器 Tab 遵循统一布局：

```
┌─────────────────────────────────────────────────┐
│ Tab: 列表 (左)                   编辑区 (右)     │
│ ┌──────────────┐  ┌────────────────────────────┐ │
│ │ 搜索/过滤     │  │ 表单字段                   │ │
│ │ ┌────────────┐ │  │                            │ │
│ │ │ item 1     │ │  │ [保存] [删除] [导出为提交] │ │
│ │ │ item 2     │ │  └────────────────────────────┘ │
│ │ │ item 3     │ │                                │
│ │ └────────────┘ │                                │
│ │ [+ 新建]       │                                │
│ └──────────────┘  └────────────────────────────────┘
└─────────────────────────────────────────────────┘
```

### 3.3 风格约定

- 所有文案使用简体中文
- 不出现任何"开发笔记"、"TODO"、"测试"等开发现场用语
- 错误提示应当友好、可操作
- 按钮文案：`保存` / `删除` / `新建` / `导出为提交` / `预览`

---

## 4. 模块文件结构

```
devkit/
├── __init__.py          # 提交管道核心（已有）
├── __main__.py          # 入口（已有）
├── _vendor.py           # vendored 工具函数（已有）
├── api.py               # js_api 桥接（需扩展）
├── app.py               # PyInstaller shim（已有）
├── main.py              # 窗口启动（已有，需调整 tab 状态）
├── state.py             # 进程内状态（已有）
├── character_editor.py  # 新增
├── memory_editor.py     # 新增
├── world_editor.py      # 新增
├── model_viewer.py      # 新增
├── voice_cloner.py      # 新增
├── ui/
│   ├── index.html       # 重写为分页布局
│   ├── devkit.js        # 增加新功能 JS
│   └── devkit.css       # 增加 Tab 相关样式
├── tests/
│   └── test_devkit.py   # 已有，追加新测试
├── pyproject.toml       # 已有
└── requirements.txt     # 已有
```

---

## 5. 测试策略

- 每个新增模块的纯函数应有单元测试（`tests/test_devkit.py`）
- 测试不依赖 `pywebview`
- `pytest` 命令在 `devkit/` 目录下执行
