"""DevKit version + update-source resolution.

The DevKit ships as a standalone PyInstaller binary, but it still needs
to know **its own version** (to compare against the latest GitHub
Release) and **where to look for updates** (the GitHub owner/repo).

Both live in the project's ``Config/Config.json`` — a single source of
truth the human maintainer edits.  We read it at runtime:

* **Source runs** — ``Config/Config.json`` sits at the repo root, one
  level above the ``devkit`` package.
* **Frozen runs** — PyInstaller extracts the bundled ``Config`` folder
  to ``sys._MEIPASS/Config`` (see the ``datas`` entry in
  ``devkit/xijian-devkit.spec``).

Everything here is read-only and offline; no network is touched.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

#: Fallback used only if ``Config/Config.json`` cannot be read.  Kept in
#: sync with ``Version.DevKit`` in that file and the ``.app`` bundle's
#: ``CFBundleShortVersionString``.
FALLBACK_VERSION = "v1.6.0"


def config_json_path() -> pathlib.Path:
    """Return the path to the project's ``Config/Config.json``.

    Resolves for both source and PyInstaller-frozen layouts.
    """
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys._MEIPASS) / "Config" / "Config.json"
    # devkit/version.py -> devkit/ -> <repo>/ -> <repo>/Config/Config.json
    return pathlib.Path(__file__).resolve().parent.parent / "Config" / "Config.json"


def read_project_config() -> dict[str, Any]:
    """Load and return the parsed ``Config/Config.json`` (or ``{}``)."""
    path = config_json_path()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def get_app_version() -> str:
    """Return the DevKit's own version string (e.g. ``v1.4.3``)."""
    cfg = read_project_config()
    version = cfg.get("Version", {}).get("DevKit")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return FALLBACK_VERSION


def get_update_source() -> dict[str, str]:
    """Return the GitHub update source configuration.

    Keys: ``owner``, ``repo``, ``tag_prefix`` (e.g. ``DevKit@``),
    ``api_url`` (fully resolved list-releases URL, or '' when owner/repo
    are not yet configured).
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
    """Return the per-platform release-asset filename patterns."""
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
