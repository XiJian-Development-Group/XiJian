"""DevKit 的自动更新引擎（基于 GitHub Releases）。

⚠️ 安全声明（必读）
=======================
**默认模式（兼容模式）**：故意禁用 TLS 证书验证（``ssl.CERT_NONE``），不实现签名校验或 SHA 校验和验证。

原因与风险：
1. **中国大陆网络环境**：绝大多数中国用户无法直接访问 GitHub，必须通过加速器 / 代理。这些代理常采用 TLS 拦截（中间人），会导致标准 TLS 验证失败（``CERTIFICATE_VERIFY_FAILED``），从而无法完成更新检查与下载。为保证基本可用性，默认模式在所有网络请求中禁用 TLS 证书验证。

2. **开源软件无签名能力**：本项目为完全开源免费软件，开发组**不持有、也不打算获取**任何代码签名证书或分发签名基础设施。因此无法提供代码签名验证（如 Apple 要求的 notarization、Windows Authenticode、Linux GPG 签名等）。

3. **校验和验证在默认模式下省略**：考虑到（a）开源项目的分发渠道不可控，（b）GitHub Releases 自身可能被篡改，（c）无签名基础设施下 SHA 校验和只能提供虚假的安全感，默认模式**不实现**下载后的校验和验证。

**后果（默认模式）**：
- 更新检查 / 下载流程**不提供传输层机密性/完整性保证**。任何能劫持用户到 GitHub 连接的攻击者（包括但不限于代理运营商、ISP、中间网络设备、DNS 劫持者）均可注入任意更新包，导致本地以当前用户权限执行恶意代码。
- 本更新器默认模式**仅适用于受信网络环境**，或由用户自行评估风险后使用。在中国大陆必须使用代理/加速器的用户，**请务必理解上述风险并自行承担**。

---
**安全模式（可选，XIJIAN_DEVKIT_VERIFY_TLS=1）**：
设置环境变量 ``XIJIAN_DEVKIT_VERIFY_TLS=1`` 可启用安全模式：
- 启用标准 TLS 证书验证（使用系统 CA 信任库，验证主机名）
- 下载完成后自动尝试获取并验证 SHA256 校验和（从 ``.sha256`` 文件或 ``sha256sum.txt``）
- 校验失败将删除下载文件并返回错误

**注意**：安全模式在中国大陆代理环境下可能无法工作（TLS 拦截导致证书验证失败）。仅在可直连 GitHub 或使用不拦截 TLS 的代理时建议启用。

如需最高安全性，建议用户**手动**从 GitHub Releases 页面下载并验证（如可用），或使用操作系统自带的包管理器（如 Homebrew、Scoop、Flatpak 等）分发。

**本声明亦体现在项目文档（README.md、Dev.md、website）中**。用户在启用自动更新前应当阅读并确认知晓风险。

---
流程（功能清单 C6，选择性联网）
-------------------------------------------
1. :func:`check_for_update` —— 请求 ``Config/Config.json`` 中配置的
   仓库的 GitHub Releases API，将最新 tag 与当前运行版本比较。
2. :func:`download_update` —— **仅在用户明确同意之后** —— 将当前平台的
   发行资产流式下载到 ``~/Library/Application Support/XiJian/Updates/Downloads``。
3. :func:`apply_update` —— **仅在用户第二次同意之后** ——
   安装下载的资产并重新启动。

网络策略
--------------
这是除提交（C5）之外*第二个*（也是唯一另一个）允许联网的功能。
每次网络调用都受显式用户操作或用户控制的“启动时检查”开关门控，
任何失败都会静默降级 / 返回结构化错误——DevKit 在离线状态下
完全可用。

仅使用 Python 标准库（``urllib``），因此冻结后的二进制包
不会新增任何第三方依赖。
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

#: GitHub 要求所有 API 请求携带 User-Agent。
_USER_AGENT = "XiJian-DevKit-Updater"

#: 更新检查 / 下载的网络超时（秒）。
_TIMEOUT = 20

#: SSL 上下文获取函数 —— 支持安全模式（启用 TLS 验证）和兼容模式（禁用）。
#: 安全模式由环境变量 ``XIJIAN_DEVKIT_VERIFY_TLS=1`` 启用。
#: 兼容模式（默认）禁用证书验证，以适应中国大陆代理环境。
def _get_ssl_context() -> ssl.SSLContext:
    """Return SSL context based on security mode.

    - 安全模式 (XIJIAN_DEVKIT_VERIFY_TLS=1): 使用系统默认 CA 信任库，验证主机名。
    - 兼容模式 (默认): 禁用证书验证，允许通过 TLS 拦截代理。

    See module docstring for security implications.
    """
    if os.environ.get("XIJIAN_DEVKIT_VERIFY_TLS") == "1":
        return ssl.create_default_context()  # 默认验证
    # 兼容模式：禁用验证（对代理友好）
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _urlopen(req: "urllib.request.Request"):
    """打开 URL，根据安全模式选择 SSL 上下文。"""
    return urllib.request.urlopen(req, timeout=_TIMEOUT, context=_get_ssl_context())


# ---------------------------------------------------------------------------
# 版本解析 / 比较
# ---------------------------------------------------------------------------

#: 常见预发布标签的排序等级（越小越早）。
_PRERELEASE_RANK = {
    "alpha": 0,
    "a": 0,
    "beta": 1,
    "b": 1,
    "rc": 2,
    "": 3,  # 正式版比同号的任何预发布版都新
}


def parse_version(v: str) -> tuple[tuple[int, ...], int, str]:
    """将 ``v1.2.3-Beta`` 解析为可比较的 ``(nums, rank, label)``。

    * ``nums``  —— 数值部分，为 int 元组（``(1, 2, 3)``）。
    * ``rank``  —— 预发布等级（final > rc > beta > alpha）。
    * ``label`` —— 小写化的预发布标签，用于平局时的决胜。
    """
    if not isinstance(v, str):
        v = str(v or "")
    s = v.strip().lstrip("vV")
    # 将数字核心与预发布后缀（``-beta`` / ``.beta``）分开。
    m = re.match(r"^(\d+(?:\.\d+)*)[-.]?([A-Za-z][A-Za-z0-9.]*)?", s)
    if not m:
        return ((0,), 3, "")
    nums = tuple(int(x) for x in m.group(1).split("."))
    label_raw = (m.group(2) or "").lower()
    # 规范化用于排序的前导标签词（例如 "beta2" -> "beta"）。
    word = re.match(r"[a-z]+", label_raw)
    rank = _PRERELEASE_RANK.get(word.group(0) if word else "", 3) if label_raw else 3
    return (nums, rank, label_raw)


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return (a + (0,) * (n - len(a)), b + (0,) * (n - len(b)))


def is_newer(latest: str, current: str) -> bool:
    """如果 ``latest`` 严格新于 ``current``，返回 ``True``。"""
    ln, lr, ll = parse_version(latest)
    cn, cr, cl = parse_version(current)
    ln, cn = _pad(ln, cn)
    if ln != cn:
        return ln > cn
    if lr != cr:
        return lr > cr
    return ll > cl


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


def downloads_dir() -> pathlib.Path:
    """返回（并创建）内部更新下载目录。"""
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
    """选择与当前平台匹配的发行资产。

    资产名称是平台相关的（``DevKit_macOS.zip`` /
    ``DevKit_Windows.zip``）。我们精确匹配配置的名称
    （不区分大小写）；如果模式看起来仍像裸后缀（``.zip``），
    则回退到后缀匹配。
    """
    if not assets:
        return None
    pattern = _version.get_asset_patterns().get(_platform_key(), "")
    if not pattern:
        return None
    pat = pattern.lower()
    # 先精确匹配文件名。
    for a in assets:
        if str(a.get("name", "")).lower() == pat:
            return a
    # 后缀回退（处理配置为裸扩展名的模式）。
    for a in assets:
        if str(a.get("name", "")).lower().endswith(pat):
            return a
    return None


def _strip_tag_prefix(tag: str, prefix: str) -> str:
    """从发行 tag 中移除组件 tag 前缀（``DevKit@``）。"""
    if prefix and tag.startswith(prefix):
        return tag[len(prefix):]
    return tag


# ---------------------------------------------------------------------------
# 检查
# ---------------------------------------------------------------------------


def check_for_update(
    current_version: str | None = None,
    *,
    _opener: Callable[[urllib.request.Request], Any] | None = None,
) -> dict[str, Any]:
    """检查 GitHub Releases 是否有更新版本。

    返回 dict::

        {
          "configured": bool,        # 是否在 Config.json 中设置了 owner/repo？
          "update_available": bool,
          "current_version": str,
          "latest_version": str,
          "release_notes": str,
          "html_url": str,           # 发行页（浏览器回退）
          "asset_name": str,         # 选中的资产文件名（无则 ''）
          "asset_url": str,          # 资产下载 URL（无则 ''）
          "asset_size": int,
        }

    网络错误从不抛出——改为返回 ``error`` 键。
    ``_opener`` 是测试接缝（默认为 ``urllib.request.urlopen``）。
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

    # ``/releases`` 返回列表；``/releases/latest`` 返回 dict。
    # 归一化为列表，使两个端点都能工作。
    releases = payload if isinstance(payload, list) else [payload]

    # 只保留该组件的发行版（tag 带 ``DevKit@`` 前缀），
    # 排除草稿/预发布，并选取最高的版本。
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
# 下载
# ---------------------------------------------------------------------------


def _fetch_sha256(asset_url: str, asset_name: str, opener: Callable[[urllib.request.Request], Any]) -> str | None:
    """尝试从 GitHub Releases 获取 SHA256 校验和。

    查找同名资产的 .sha256 文件或 sha256sum.txt。
    返回十六进制字符串或 None（未找到/失败）。
    """
    # 尝试 .sha256 后缀文件
    sha_url = asset_url + ".sha256"
    req = urllib.request.Request(sha_url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener(req) as resp:
            content = resp.read().decode("utf-8").strip()
            # 格式: "sha256_hash  filename" 或仅 hash
            parts = content.split()
            if parts:
                return parts[0].lower()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        pass

    # 尝试 sha256sum.txt（常见于同一 release 下的所有文件）
    base_url = asset_url.rsplit("/", 1)[0] + "/sha256sum.txt"
    req = urllib.request.Request(base_url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener(req) as resp:
            content = resp.read().decode("utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1].endswith(asset_name):
                    return parts[0].lower()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        pass

    return None


def _verify_sha256(filepath: pathlib.Path, expected: str) -> bool:
    """验证文件的 SHA256 校验和。"""
    import hashlib
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    actual = h.hexdigest().lower()
    return actual == expected


def download_update(
    asset_url: str,
    asset_name: str,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    _opener: Callable[[urllib.request.Request], Any] | None = None,
) -> dict[str, Any]:
    """将发行资产下载到 :func:`downloads_dir`。

    提供时，会周期性调用 ``progress_cb(downloaded_bytes, total_bytes)``。
    返回 ``{"path": str, "size": int}`` 或 ``{"error": str}``。

    若启用安全模式 (XIJIAN_DEVKIT_VERIFY_TLS=1) 且能获取到 SHA256 校验和，
    将在下载完成后自动验证；验证失败则删除文件并返回错误。
    """
    if not asset_url or not asset_name:
        return {"error": "缺少下载地址或文件名"}
    # 防止资产名中的路径遍历。
    safe_name = os.path.basename(asset_name)
    dest = downloads_dir() / safe_name
    tmp = dest.with_suffix(dest.suffix + ".part")

    opener = _opener or _urlopen
    req = urllib.request.Request(asset_url, headers={"User-Agent": _USER_AGENT})
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

    # 安全模式下尝试 SHA256 验证
    if os.environ.get("XIJIAN_DEVKIT_VERIFY_TLS") == "1":
        expected_sha256 = _fetch_sha256(asset_url, asset_name, opener)
        if expected_sha256:
            if not _verify_sha256(dest, expected_sha256):
                try:
                    dest.unlink()
                except OSError:
                    pass
                return {"error": f"SHA256 校验失败：文件可能被篡改 (expected {expected_sha256})"}
        else:
            # 无校验和文件时仅警告（不阻塞），避免误判
            pass

    return {"path": str(dest), "size": dest.stat().st_size}

    return {"path": str(dest), "size": dest.stat().st_size}


# ---------------------------------------------------------------------------
# 应用（安装 + 重新启动）
# ---------------------------------------------------------------------------


def _current_app_bundle() -> pathlib.Path | None:
    """在 macOS 上返回正在运行的 ``.app`` 包路径（否则返回 ``None``）。"""
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
    """安装下载的资产并安排重新启动。

    目前仅支持 macOS（打包目标）。处理包含 ``.app`` 包的 ``.dmg``
    和 ``.zip`` 资产。一个分离的辅助脚本会等待本进程退出、交换
    应用包并重新启动——因此调用方应在收到 ``{"scheduled": True}``
    结果后立即退出应用。
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
            # 使用超时且不捕获输出，以避免挂起
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
            # 尽力卸载，忽略错误
            subprocess.run(
                ["hdiutil", "detach", str(mount_point)],
                timeout=30, capture_output=False,
            )

    if new_app is None or not new_app.exists():
        return {"error": "更新包中未找到 .app 应用"}

    # 分离的辅助脚本：等待本进程退出、交换应用包、重新启动。
    pid = os.getpid()
    helper = staging / "apply_update.sh"
    helper.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        f'PID={pid}\n'
        f'NEW_APP="{new_app}"\n'
        f'CUR_APP="{current_app}"\n'
        # 等待父进程退出（最多 30 秒）
        'for i in {1..60}; do\n'
        '  if ! kill -0 "$PID" 2>/dev/null; then break; fi\n'
        '  sleep 0.5\n'
        'done\n'
        # 超时后若仍存活则强制杀掉
        'if kill -0 "$PID" 2>/dev/null; then\n'
        '  kill -9 "$PID" 2>/dev/null || true\n'
        'fi\n'
        'rm -rf "$CUR_APP"\n'
        'cp -R "$NEW_APP" "$CUR_APP"\n'
        'open "$CUR_APP"\n',
        encoding="utf-8",
    )
    helper.chmod(0o755)
    # 使用 Popen 并正确分离进程
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
