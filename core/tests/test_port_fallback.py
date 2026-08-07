"""Tests for automatic port fallback (端口自动更换).

Covers / 覆盖:
* ``is_port_in_use`` — real bind probe.
  ``is_port_in_use`` — 真实 bind 探测。
* ``find_port_occupant`` — psutil-backed occupant report (faked).
  ``find_port_occupant`` — psutil 支持的占用进程报告（伪造）。
* ``resolve_available_port`` — scan-up fallback within max_attempts.
  ``resolve_available_port`` — 在 max_attempts 内向上扫描回退。
* ``--port-strict`` — occupied port aborts startup (old behaviour).
  ``--port-strict`` — 端口被占用时中止启动（旧行为）。
* ``main()`` end-to-end — occupied port → fallback → healthz + port file.
  ``main()`` 端到端 — 端口被占用 → 回退 → healthz + 端口文件。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from xijian_api import app as app_module
from xijian_api import ports as ports_module
from xijian_api.ports import (
    PortExhaustedError,
    is_port_in_use,
    resolve_available_port,
)

CORE_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# is_port_in_use — real bind probe
# is_port_in_use — 真实 bind 探测
# ---------------------------------------------------------------------------


def _grab_port() -> tuple[int, socket.socket]:
    """Bind a real listener on an ephemeral port and return it.

    在临时端口上绑定一个真实监听器并返回该端口。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock.getsockname()[1], sock


def test_is_port_in_use_true_when_bound():
    """A bound port must be reported as in use.
    (已绑定的端口必须报告为被占用。)
    """
    port, sock = _grab_port()
    try:
        assert is_port_in_use("127.0.0.1", port) is True
    finally:
        sock.close()


def test_is_port_in_use_false_when_free():
    """A closed port must be reported as free.
    (已关闭的端口必须报告为空闲。)
    """
    port, sock = _grab_port()
    sock.close()
    assert is_port_in_use("127.0.0.1", port) is False


# ---------------------------------------------------------------------------
# find_port_occupant — faked psutil
# find_port_occupant — 伪造 psutil
# ---------------------------------------------------------------------------


def _fake_psutil_module(connection_records, process_map):
    """Build a fake ``psutil`` module shaped like the real one.

    构建一个形态与真实 psutil 相同的伪造模块。
    """
    import types

    class _LAddr:
        def __init__(self, port):
            self.port = port

    class _Conn:
        def __init__(self, pid, port):
            self.status = "LISTEN"
            self.pid = pid
            self.laddr = _LAddr(port)

    class _Process:
        def __init__(self, pid):
            self._pid = pid

        def name(self):
            proc = process_map.get(self._pid)
            if proc is None:
                raise OSError("gone")
            return proc["name"]

        def cmdline(self):
            proc = process_map.get(self._pid)
            if proc is None:
                raise OSError("gone")
            return proc["cmdline"]

    module = types.ModuleType("psutil")
    module.CONN_LISTEN = "LISTEN"
    module.net_connections = lambda kind="inet": [
        _Conn(r["pid"], r["port"]) for r in connection_records
    ]
    module.Process = _Process
    module.NoSuchProcess = OSError
    module.AccessDenied = PermissionError
    return module


def test_find_port_occupant_reports_pid_and_name(monkeypatch):
    """The occupant report must include PID and process name.
    (占用进程报告必须包含 PID 和进程名。)
    """
    fake = _fake_psutil_module(
        connection_records=[{"pid": 4242, "port": 18500}],
        process_map={4242: {"name": "python3", "cmdline": ["python3", "-m", "xijian_api"]}},
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    from xijian_api.ports import find_port_occupant

    report = find_port_occupant(18500)
    assert report is not None
    assert "4242" in report
    assert "python3" in report


def test_find_port_occupant_none_when_free(monkeypatch):
    """No listener → None.
    (没有监听者 → None。)
    """
    fake = _fake_psutil_module(connection_records=[], process_map={})
    monkeypatch.setitem(sys.modules, "psutil", fake)
    from xijian_api.ports import find_port_occupant

    assert find_port_occupant(18500) is None


def test_find_port_occupant_never_raises(monkeypatch):
    """A dead process between listing and lookup must not raise.
    (列出与查找之间进程消亡不得抛出异常。)
    """
    fake = _fake_psutil_module(
        connection_records=[{"pid": 999, "port": 18500}],
        process_map={},  # lookup raises OSError("gone")
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    from xijian_api.ports import find_port_occupant

    assert find_port_occupant(18500) == "PID 999（无法读取进程信息）"


# ---------------------------------------------------------------------------
# resolve_available_port — scan-up fallback
# resolve_available_port — 向上扫描回退
# ---------------------------------------------------------------------------


def test_resolve_returns_preferred_when_free(monkeypatch):
    """Free preferred port → same port, changed=False.
    (首选端口空闲 → 原端口，changed=False。)
    """
    monkeypatch.setattr(ports_module, "is_port_in_use", lambda h, p: False)
    resolution = resolve_available_port("127.0.0.1", 18500, max_attempts=5)
    assert resolution.port == 18500
    assert resolution.changed is False
    assert resolution.occupied_by is None


def test_resolve_scans_up_when_occupied(monkeypatch):
    """Occupied preferred → first free port above it, changed=True.
    (首选被占用 → 上方第一个空闲端口，changed=True。)
    """
    occupied = {18500, 18501, 18502}
    monkeypatch.setattr(
        ports_module,
        "is_port_in_use",
        lambda h, p: p in occupied,
    )
    monkeypatch.setattr(
        ports_module,
        "find_port_occupant",
        lambda p: "PID 1（test）" if p == 18500 else None,
    )
    resolution = resolve_available_port("127.0.0.1", 18500, max_attempts=10)
    assert resolution.port == 18503
    assert resolution.changed is True
    assert resolution.occupied_by == "PID 1（test）"


def test_resolve_exhausts_raises(monkeypatch):
    """All ports in range occupied → PortExhaustedError.
    (范围内所有端口都被占用 → PortExhaustedError。)
    """
    monkeypatch.setattr(ports_module, "is_port_in_use", lambda h, p: True)
    with pytest.raises(PortExhaustedError):
        resolve_available_port("127.0.0.1", 18500, max_attempts=3)


def test_resolve_stops_at_port_range_end(monkeypatch):
    """Scanning past 65535 stops early with PortExhaustedError.
    (扫描越过 65535 提前停止并抛 PortExhaustedError。)
    """
    monkeypatch.setattr(ports_module, "is_port_in_use", lambda h, p: True)
    with pytest.raises(PortExhaustedError):
        resolve_available_port("127.0.0.1", 65530, max_attempts=100)


# ---------------------------------------------------------------------------
# --port-strict
# --port-strict
# ---------------------------------------------------------------------------


def test_parse_args_port_strict_flag():
    """``--port-strict`` parses to a boolean flag.
    (``--port-strict`` 解析为布尔标志。)
    """
    assert app_module.parse_args([]).port_strict is False
    assert app_module.parse_args(["--port-strict"]).port_strict is True


def test_port_strict_occupied_aborts(monkeypatch):
    """``--port-strict`` + occupied port → exit code 1.
    (``--port-strict`` + 端口被占用 → 退出码 1。)
    """
    monkeypatch.setattr(ports_module, "is_port_in_use", lambda h, p: True)
    monkeypatch.setattr(ports_module, "find_port_occupant", lambda p: "PID 1（test）")

    class _Args:
        port = 18500
        port_strict = True
        host = None
        dev = None
        config = None
        log_level = None
        log_file = None
        no_serve = False
        server = None
        version = False

    from xijian_api.config import Config

    monkeypatch.setattr(
        app_module,
        "_load_config_resilient",
        lambda testing=False: Config.empty(),
    )
    # Port pre-flight aborts before app build — this must never run.
    # (端口预检在应用构建前中止 —— 这绝不应当执行。)
    monkeypatch.setattr(app_module, "_build_app_resilient", lambda c: pytest.fail("should not build"))

    assert app_module._run(_Args(), None) == 1


def test_port_strict_free_proceeds(monkeypatch):
    """``--port-strict`` + free port → startup proceeds to --no-serve.
    (``--port-strict`` + 端口空闲 → 启动继续到 --no-serve。)
    """
    monkeypatch.setattr(ports_module, "is_port_in_use", lambda h, p: False)
    monkeypatch.setattr(app_module, "_ensure_storage_dirs", lambda c: None)
    monkeypatch.setattr(app_module, "_print_banner", lambda *a, **k: None)

    class _Args:
        port = 18500
        port_strict = True
        host = None
        dev = None
        config = None
        log_level = None
        log_file = None
        no_serve = True  # stop before serving
        server = None
        version = False

    from xijian_api.config import Config

    monkeypatch.setattr(
        app_module,
        "_load_config_resilient",
        lambda testing=False: Config.empty(),
    )
    monkeypatch.setattr(app_module, "_build_app_resilient", lambda c: _FakeApp())

    class _FakeApp:
        def __init__(self):
            self.config = {"XIJIAN_CONFIG": Config.empty()}

    assert app_module._run(_Args(), None) == 0


def test_port_fallback_occupied_proceeds(monkeypatch, caplog):
    """Default mode + occupied port → falls back and proceeds to --no-serve,
    logging the fallback.
    (默认模式 + 端口被占用 → 回退并继续到 --no-serve，并记录回退日志。)
    """
    occupied = {18500, 18501}
    monkeypatch.setattr(
        ports_module,
        "is_port_in_use",
        lambda h, p: p in occupied,
    )
    monkeypatch.setattr(
        ports_module,
        "find_port_occupant",
        lambda p: "PID 1（test）" if p == 18500 else None,
    )
    monkeypatch.setattr(app_module, "_ensure_storage_dirs", lambda c: None)
    monkeypatch.setattr(app_module, "_print_banner", lambda *a, **k: None)

    class _Args:
        port = 18500
        port_strict = False
        host = None
        dev = None
        config = None
        log_level = None
        log_file = None
        no_serve = True
        server = None
        version = False

    from xijian_api.config import Config

    monkeypatch.setattr(
        app_module,
        "_load_config_resilient",
        lambda testing=False: Config.empty(),
    )
    monkeypatch.setattr(app_module, "_build_app_resilient", lambda c: _FakeApp())

    class _FakeApp:
        def __init__(self):
            self.config = {"XIJIAN_CONFIG": Config.empty()}

    with caplog.at_level("WARNING", logger="xijian_api.app"):
        assert app_module._run(_Args(), None) == 0
    assert "已自动更换端口" in caplog.text
    assert "PID 1（test）" in caplog.text


# ---------------------------------------------------------------------------
# main() end-to-end — occupied port → fallback → healthz + port file
# main() 端到端 — 端口被占用 → 回退 → healthz + 端口文件
# ---------------------------------------------------------------------------


def test_main_falls_back_to_free_port(tmp_path):
    """Occupying the preferred port must make ``main`` bind a free port,
    report it, publish the pid-scoped port file, and serve /healthz there.

    占用首选端口必须让 ``main`` 绑定一个空闲端口、报告它、发布按 pid
    隔离的端口文件，并在该端口提供 /healthz。
    """
    import urllib.request

    # Occupier on the preferred port.
    # 占用首选端口的监听器。
    preferred = 19500
    holder, sock = _grab_port()
    try:
        # Make sure the preferred port itself is the occupied one; if the
        # ephemeral holder collided with `preferred` pick another free base.
        # 确保首选端口本身被占用；若临时 holder 与 `preferred` 撞了，换一个空闲基数。
        while preferred == holder:
            preferred += 1
        if not is_port_in_use("127.0.0.1", preferred):
            # Bind the preferred port ourselves so fallback is exercised.
            # 自己绑定首选端口，从而真正触发回退。
            holder2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            holder2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            holder2.bind(("127.0.0.1", preferred))
            holder2.listen(1)
            holder_sock = holder2
        else:
            holder_sock = None

        env = dict(os.environ)
        env["XIJIAN_DATA_DIR"] = str(tmp_path)
        env["XIJIAN_DEV"] = "1"
        env.pop("XIJIAN_DEV_TOKEN_FILE", None)
        # Make the module importable regardless of cwd.
        # 无论 cwd 如何都让模块可导入。
        env["PYTHONPATH"] = str(CORE_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "xijian_api",
                "--port",
                str(preferred),
                "--log-level",
                "INFO",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Wait for the pid-scoped port file, then verify /healthz.
            # 等待按 pid 隔离的端口文件，然后验证 /healthz。
            port_file = Path("/tmp") / f"xijian-{proc.pid}.port"
            found = None
            deadline = time.time() + 30
            while time.time() < deadline:
                if port_file.is_file():
                    found = int(port_file.read_text(encoding="utf-8").strip())
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.2)

            assert proc.poll() is None, "Core exited unexpectedly"
            assert found is not None, "port file was never written"
            assert found != preferred, "port fallback did not happen"

            # Health check on the actual port.
            # 在实际端口上做健康检查。
            url = f"http://127.0.0.1:{found}/healthz"
            ok = False
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(url, timeout=2) as resp:
                        if resp.status == 200:
                            ok = True
                            break
                except Exception:  # noqa: BLE001 - retry until deadline
                    time.sleep(0.3)
            assert ok, f"/healthz not reachable on {url}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            if holder_sock is not None:
                holder_sock.close()
    finally:
        sock.close()
