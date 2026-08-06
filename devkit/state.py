"""开发者工具（Pywebview 应用）的磁盘持久化状态。

DevKit **刻意保持独立** —— 它与主 Flask API 服务器不共享状态。
本模块拥有自己的三个存储桶，与主 API 的 ``xijian_api.stubs.state``
无关：

=========== ============================================================
键          形状
=========== ============================================================
submissions     ``{submission_id: dict}`` —— 每次提交的完整记录
last_submit_at  ``{developer_id: iso8601_string}`` —— 用于 1 小时冷却
local_archives  ``{submission_id: archive_path}`` —— 供后续清理使用
session         ``{"developer_id": str|None}`` —— 最后登录的开发者
=========== ============================================================

三个存储桶都持久化到工作目录下的一个 JSON 文件中，
以便提交历史在 DevKit 重启后仍然保留（C5-03）。
该文件在模块导入时加载一次，并在每次变更后保存。
"""

from __future__ import annotations

import json
import os


#: ``{submission_id: dict}`` —— 每条记录携带 developer_id、
#: target_kind、target_id、archive_path、archive_size、archive_format、
#: content_sha256、ai_ratio、smtp_status、smtp_code、smtp_response、
#: submitted_at、email_subject、notes。
submissions: dict = {}

#: ``{developer_id: iso8601 string}`` —— 每个开发者最近一次的提交
#: 时间戳；由 1 小时限流器使用。
last_submit_at: dict = {}

#: ``{submission_id: archive_path}`` —— 每次提交生成的 7Z/zip
#: 归档的文件系统路径，以便清理任务（和测试）能再次找到它。
local_archives: dict = {}

#: ``{"developer_id": str | None}`` —— DevKit 上次关闭时登录的开发者。
#: 持久化保存，以便重启时**不会**静默丢失会话并重置每个开发者的
#: 冷却显示（C5 反滥用：重启绝不能允许开发者绕过 1 小时提交冷却）。
session: dict = {"developer_id": None}


def _state_path(work_dir: str) -> str:
    return os.path.join(work_dir, "devkit_state.json")


def load(work_dir: str) -> None:
    """从 ``work_dir`` 中的 JSON 文件加载 DevKit 状态，替换所有
    内存存储桶。可多次安全调用——会先重置。"""
    reset_for_testing()
    fpath = _state_path(work_dir)
    if not os.path.isfile(fpath):
        return
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    submissions.update(data.get("submissions", {}))
    last_submit_at.update(data.get("last_submit_at", {}))
    local_archives.update(data.get("local_archives", {}))
    session["developer_id"] = data.get("session", {}).get("developer_id")


def save(work_dir: str) -> None:
    """将内存存储桶持久化到 ``work_dir`` 中的 JSON 文件。"""
    os.makedirs(work_dir, exist_ok=True)
    data = {
        "submissions": dict(submissions),
        "last_submit_at": dict(last_submit_at),
        "local_archives": dict(local_archives),
        "session": dict(session),
    }
    with open(_state_path(work_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def reset_for_testing() -> None:
    """清空每个 DevKit 存储桶。仅供测试使用——切勿在应用代码中调用。"""
    submissions.clear()
    last_submit_at.clear()
    local_archives.clear()
    session["developer_id"] = None


__all__ = [
    "submissions",
    "last_submit_at",
    "local_archives",
    "session",
    "load",
    "save",
    "reset_for_testing",
]
