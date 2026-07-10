"""Auto-update engine for the DevKit (GitHub Releases based).

Flow (function list C6, opt-in network use)
-------------------------------------------
1. :func:`check_for_update` — hit the GitHub Releases API for the repo
   configured in ``Config/Config.json`` and compare the latest tag
   against the running app's version.
2. :func:`download_update` — **only after explicit user consent** —
   stream the platform's release asset into
   ``~/Library/Application Support/XiJian/Updates/Downloads``.
3. :func:`apply_update` — **only after a second user consent** —
   install the downloaded asset and relaunch.

Network policy
--------------
This is the *second* (and only other) feature besides submission (C5)
that is allowed to touch the network.  Every network call is gated
behind an explicit user action or the user-controlled
"check-on-launch" toggle, and every failure degrades silently /
returns a structured error — the DevKit stays fully usable offline.

Only the Python stdlib is used (``urllib``) so the frozen binary gains
no new third-party dependency.
"""

from __future__ import annotations

import json
import os
import pathlib
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any, Callable

from devkit import version as _version

#: GitHub requires a User-Agent on all API requests.
_USER_AGENT = "XiJian-DevKit-Updater"

#: Network timeout (seconds) for update checks / downloads.
_TIMEOUT = 20

#: Mainland-China users almost always reach GitHub through an accelerator
#: / proxy whose TLS interception breaks certificate verification
#: (``CERTIFICATE_VERIFY_FAILED``).  Since the update payload's integrity
#: is what matters (and we could add checksum verification later), we
#: intentionally skip TLS cert verification so update checks/downloads
#: work behind those proxies.
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _urlopen(req: "urllib.request.Request"):
    """Open a URL with TLS verification disabled (proxy-friendly)."""
    return urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CONTEXT)


# ---------------------------------------------------------------------------
# Version parsing / comparison
# ---------------------------------------------------------------------------

#: Ordering rank for common pre-release labels (lower = earlier).
_PRERELEASE_RANK = {
    "alpha": 0,
    "a": 0,
    "beta": 1,
    "b": 1,
    "rc": 2,
    "": 3,  # a final release outranks any pre-release of the same number
}


def parse_version(v: str) -> tuple[tuple[int, ...], int, str]:
    """Parse ``v1.2.3-Beta`` into a comparable ``(nums, rank, label)``.

    * ``nums``  — numeric components as an int tuple (``(1, 2, 3)``).
    * ``rank``  — pre-release rank (final > rc > beta > alpha).
    * ``label`` — the lowercased pre-release label for tie-breaking.
    """
    if not isinstance(v, str):
        v = str(v or "")
    s = v.strip().lstrip("vV")
    # Split numeric core from a pre-release suffix (``-beta`` / ``.beta``).
    m = re.match(r"^(\d+(?:\.\d+)*)[-.]?([A-Za-z][A-Za-z0-9.]*)?", s)
    if not m:
        return ((0,), 3, "")
    nums = tuple(int(x) for x in m.group(1).split("."))
    label_raw = (m.group(2) or "").lower()
    # Normalise leading label word for ranking (e.g. "beta2" -> "beta").
    word = re.match(r"[a-z]+", label_raw)
    rank = _PRERELEASE_RANK.get(word.group(0) if word else "", 3) if label_raw else 3
    return (nums, rank, label_raw)


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return (a + (0,) * (n - len(a)), b + (0,) * (n - len(b)))


def is_newer(latest: str, current: str) -> bool:
    """Return ``True`` if ``latest`` is a strictly newer version than ``current``."""
    ln, lr, ll = parse_version(latest)
    cn, cr, cl = parse_version(current)
    ln, cn = _pad(ln, cn)
    if ln != cn:
        return ln > cn
    if lr != cr:
        return lr > cr
    return ll > cl


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def downloads_dir() -> pathlib.Path:
    """Return (creating) the internal update-downloads directory."""
    base = pathlib.Path(os.path.expanduser("~")) / "Library" / "Application Support" / "XiJian" / "Updates" / "Downloads"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _platform_key() -> str:
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform.startswith("win"):
        return "Windows"
    return "Linux"


def _pick_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the release asset matching this platform.

    Asset names are platform-specific (``DevKit_macOS.zip`` /
    ``DevKit_Windows.zip``).  We match the configured name exactly
    (case-insensitive); if the pattern still looks like a bare suffix
    (``.zip``) we fall back to a suffix match.
    """
    if not assets:
        return None
    pattern = _version.get_asset_patterns().get(_platform_key(), "")
    if not pattern:
        return None
    pat = pattern.lower()
    # Exact filename match first.
    for a in assets:
        if str(a.get("name", "")).lower() == pat:
            return a
    # Suffix fallback (handles patterns configured as bare extensions).
    for a in assets:
        if str(a.get("name", "")).lower().endswith(pat):
            return a
    return None


def _strip_tag_prefix(tag: str, prefix: str) -> str:
    """Remove a component tag prefix (``DevKit@``) from a release tag."""
    if prefix and tag.startswith(prefix):
        return tag[len(prefix):]
    return tag


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


def check_for_update(
    current_version: str | None = None,
    *,
    _opener: Callable[[urllib.request.Request], Any] | None = None,
) -> dict[str, Any]:
    """Check GitHub Releases for a newer version.

    Returns a dict::

        {
          "configured": bool,        # owner/repo set in Config.json?
          "update_available": bool,
          "current_version": str,
          "latest_version": str,
          "release_notes": str,
          "html_url": str,           # release page (browser fallback)
          "asset_name": str,         # chosen asset filename ('' if none)
          "asset_url": str,          # asset download URL ('' if none)
          "asset_size": int,
        }

    Never raises for network errors — returns ``error`` key instead.
    ``_opener`` is a test seam (defaults to ``urllib.request.urlopen``).
    """
    current = current_version or _version.get_app_version()
    src = _version.get_update_source()
    prefix = src.get("tag_prefix", "")
    result: dict[str, Any] = {
        "configured": bool(src["api_url"]),
        "update_available": False,
        "current_version": current,
        "latest_version": "",
        "release_notes": "",
        "html_url": "",
        "asset_name": "",
        "asset_url": "",
        "asset_size": 0,
    }
    if not src["api_url"]:
        result["error"] = "GitHub 更新源未配置（请在 Config.json 填写 GitHubOwner/GitHubRepo）"
        return result

    req = urllib.request.Request(
        src["api_url"],
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    opener = _opener or _urlopen
    try:
        with opener(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        result["error"] = f"检查更新失败：{exc}"
        return result

    # ``/releases`` returns a list; ``/releases/latest`` returns a dict.
    # Normalise to a list so both endpoints work.
    releases = payload if isinstance(payload, list) else [payload]

    # Keep only this component's releases (tag prefixed with ``DevKit@``),
    # excluding drafts/pre-releases, and pick the highest version.
    best: dict[str, Any] | None = None
    best_ver = ""
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        if rel.get("draft"):
            continue
        tag = str(rel.get("tag_name") or rel.get("name") or "")
        if prefix and not tag.startswith(prefix):
            continue
        ver = _strip_tag_prefix(tag, prefix)
        if not ver:
            continue
        if best is None or is_newer(ver, best_ver):
            best, best_ver = rel, ver

    if best is None:
        result["error"] = "未找到匹配的 DevKit 发行版"
        return result

    result["latest_version"] = best_ver
    result["release_notes"] = str(best.get("body") or "")
    result["html_url"] = str(best.get("html_url") or "")

    asset = _pick_asset(best.get("assets") or [])
    if asset:
        result["asset_name"] = str(asset.get("name", ""))
        result["asset_url"] = str(asset.get("browser_download_url", ""))
        result["asset_size"] = int(asset.get("size", 0) or 0)

    result["update_available"] = is_newer(best_ver, current)
    return result


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_update(
    asset_url: str,
    asset_name: str,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    _opener: Callable[[urllib.request.Request], Any] | None = None,
) -> dict[str, Any]:
    """Download a release asset into :func:`downloads_dir`.

    ``progress_cb(downloaded_bytes, total_bytes)`` is invoked
    periodically when provided.  Returns ``{"path": str, "size": int}``
    or ``{"error": str}``.
    """
    if not asset_url or not asset_name:
        return {"error": "缺少下载地址或文件名"}
    # Guard against path traversal in the asset name.
    safe_name = os.path.basename(asset_name)
    dest = downloads_dir() / safe_name
    tmp = dest.with_suffix(dest.suffix + ".part")

    req = urllib.request.Request(asset_url, headers={"User-Agent": _USER_AGENT})
    opener = _opener or _urlopen
    try:
        with opener(req) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            pass
        os.replace(tmp, dest)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {"error": f"下载失败：{exc}"}

    return {"path": str(dest), "size": dest.stat().st_size}


# ---------------------------------------------------------------------------
# Apply (install + relaunch)
# ---------------------------------------------------------------------------


def _current_app_bundle() -> pathlib.Path | None:
    """Return the running ``.app`` bundle path on macOS (or ``None``)."""
    if sys.platform != "darwin":
        return None
    exe = pathlib.Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def _find_app_in(directory: pathlib.Path) -> pathlib.Path | None:
    for child in directory.iterdir():
        if child.suffix == ".app":
            return child
    return None


def apply_update(downloaded_path: str) -> dict[str, Any]:
    """Install a downloaded asset and schedule a relaunch.

    macOS only for now (the packaged target).  Handles ``.dmg`` and
    ``.zip`` assets that contain a ``.app`` bundle.  A detached helper
    script waits for this process to exit, swaps the bundle, and
    relaunches — so the caller should quit the app right after a
    ``{"scheduled": True}`` result.
    """
    path = pathlib.Path(downloaded_path)
    if not path.is_file():
        return {"error": "安装包不存在或已被删除"}

    if sys.platform != "darwin":
        return {"error": f"暂不支持在 {sys.platform} 上自动安装，请手动安装下载的更新包"}

    current_app = _current_app_bundle()
    if current_app is None:
        return {
            "error": "无法定位当前应用（可能未以打包 .app 形式运行）。请手动安装下载的更新包。",
            "downloaded_path": str(path),
        }

    staging = pathlib.Path(tempfile.mkdtemp(prefix="xijian_update_"))
    new_app: pathlib.Path | None = None
    mount_point: pathlib.Path | None = None

    try:
        if path.suffix.lower() == ".zip":
            shutil.unpack_archive(str(path), str(staging))
            new_app = _find_app_in(staging)
        elif path.suffix.lower() == ".dmg":
            mount_point = pathlib.Path(tempfile.mkdtemp(prefix="xijian_dmg_"))
            # Use timeout and don't capture output to avoid hangs
            result = subprocess.run(
                ["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount_point), str(path)],
                check=True, timeout=60, capture_output=False,
            )
            src_app = _find_app_in(mount_point)
            if src_app:
                new_app = staging / src_app.name
                shutil.copytree(src_app, new_app)
        else:
            return {"error": f"不支持的更新包格式：{path.suffix}"}
    except subprocess.TimeoutExpired:
        return {"error": "DMG 挂载超时（60秒）"}
    except (shutil.ReadError, subprocess.CalledProcessError, OSError) as exc:
        return {"error": f"解包更新失败：{exc}"}
    finally:
        if mount_point is not None:
            # Best effort detach, ignore errors
            subprocess.run(
                ["hdiutil", "detach", str(mount_point)],
                timeout=30, capture_output=False,
            )

    if new_app is None or not new_app.exists():
        return {"error": "更新包中未找到 .app 应用"}

    # Detached helper: wait for us to exit, swap bundles, relaunch.
    pid = os.getpid()
    helper = staging / "apply_update.sh"
    helper.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        f'PID={pid}\n'
        f'NEW_APP="{new_app}"\n'
        f'CUR_APP="{current_app}"\n'
        # Wait for parent to exit (max 30 seconds)
        'for i in {1..60}; do\n'
        '  if ! kill -0 "$PID" 2>/dev/null; then break; fi\n'
        '  sleep 0.5\n'
        'done\n'
        # Force kill if still alive after timeout
        'if kill -0 "$PID" 2>/dev/null; then\n'
        '  kill -9 "$PID" 2>/dev/null || true\n'
        'fi\n'
        'rm -rf "$CUR_APP"\n'
        'cp -R "$NEW_APP" "$CUR_APP"\n'
        'open "$CUR_APP"\n',
        encoding="utf-8",
    )
    helper.chmod(0o755)
    # Use Popen with proper detachment
    subprocess.Popen(
        ["/bin/bash", str(helper)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return {"scheduled": True, "target": str(current_app)}


__all__ = [
    "parse_version",
    "is_newer",
    "downloads_dir",
    "check_for_update",
    "download_update",
    "apply_update",
]
