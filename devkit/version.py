"""DevKit 版本 + 更新源解析。

DevKit 以独立的 PyInstaller 二进制包发布，但仍需要知道**自身版本**
（用于与最新的 GitHub Release 比较）以及**去哪里寻找更新**
（GitHub owner/repo）。

两者都位于项目的 ``Config/Config.json`` —— 由人工维护者编辑的
单一事实来源。我们在运行时读取它：

* **源码运行** —— ``Config/Config.json`` 位于仓库根目录，
  在 ``devkit`` 包上一级。
* **冻结运行** —— PyInstaller 将捆绑的 ``Config`` 文件夹解压到
  ``sys._MEIPASS/Config``（参见 ``devkit/xijian-devkit.spec`` 中的
  ``datas`` 条目）。

这里的所有操作都是只读且离线的；不涉及任何网络。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

#: 仅在无法读取 ``Config/Config.json`` 时使用的回退版本。与
#: 该文件中的 ``Version.DevKit`` 以及 ``.app`` 应用包的
#: ``CFBundleShortVersionString`` 保持同步。
FALLBACK_VERSION = "v1.6.2"


def config_json_path() -> pathlib.Path:
    """返回项目 ``Config/Config.json`` 的路径。

    同时兼容源码布局和 PyInstaller 冻结布局。
    """
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys._MEIPASS) / "Config" / "Config.json"
    # 路径链：devkit/version.py -> devkit/ -> <仓库根>/ -> <仓库根>/Config/Config.json
    return pathlib.Path(__file__).resolve().parent.parent / "Config" / "Config.json"


def read_project_config() -> dict[str, Any]:
    """加载并返回解析后的 ``Config/Config.json``（或 ``{}``）。"""
    path = config_json_path()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def get_app_version() -> str:
    """返回 DevKit 自身的版本字符串（例如 ``v1.4.3``）。"""
    cfg = read_project_config()
    version = cfg.get("Version", {}).get("DevKit")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return FALLBACK_VERSION


def get_update_source() -> dict[str, str]:
    """返回 GitHub 更新源配置。

    键：``owner``、``repo``、``tag_prefix``（例如 ``DevKit@``）、
    ``api_url``（完全解析后的 list-releases URL，owner/repo
    尚未配置时为空字符串）。
    """
    cfg = read_project_config()
    uc = cfg.get("UpdateConfig", {}) or {}
    owner = str(uc.get("GitHubOwner", "") or "").strip()
    repo = str(uc.get("GitHubRepo", "") or "").strip()
    tag_prefix = str(uc.get("TagPrefix", "") or "").strip()
    template = str(
        uc.get(
            "ReleasesApiTemplate",
            "https://api.github.com/repos/{owner}/{repo}/releases?per_page=100",
        )
    )
    api_url = ""
    if owner and repo:
        api_url = template.format(owner=owner, repo=repo)
    return {
        "owner": owner,
        "repo": repo,
        "tag_prefix": tag_prefix,
        "api_url": api_url,
    }


def get_asset_patterns() -> dict[str, str]:
    """返回各平台的发布资产文件名模式。"""
    cfg = read_project_config()
    uc = cfg.get("UpdateConfig", {}) or {}
    patterns = uc.get("AssetPatterns", {}) or {}
    return {str(k): str(v) for k, v in patterns.items()}


__all__ = [
    "FALLBACK_VERSION",
    "config_json_path",
    "read_project_config",
    "get_app_version",
    "get_update_source",
    "get_asset_patterns",
]
