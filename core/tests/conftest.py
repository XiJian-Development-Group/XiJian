"""Pytest fixtures for the XiJian API server. (XiJian API 服务器的 Pytest 固定装置)

We build a single app per session (the test suite is read-mostly and
fast enough that re-building the app per test isn't worth the cost).
(我们在每个会话构建一个应用，因为测试套件主要是读操作且足够快，
每个测试重建应用不值得。)
Every test gets a fresh ``client`` so request-id / idempotency state
doesn't leak between tests in ways that affect assertions.
(每个测试都获得一个全新的 ``client``，这样 request-id / 幂等性状态
不会以影响断言的方式在测试间泄露。)
"""

from __future__ import annotations

import os

import pytest

# Make sure ``XIJIAN_DEV=1`` is *not* set when the test suite is
# collected — otherwise :func:`xijian_api.auth.setup_token` would try
# to write a real token file.  Testing mode bypasses that path but
# the env hygiene is still nice to have.
# (确保测试套件收集时未设置 ``XIJIAN_DEV=1`` —— 否则
# :func:`xijian_api.auth.setup_token` 会尝试写入真实 token 文件。
# 测试模式会绕过该路径，但环境变量卫生仍值得保持。)
os.environ.pop("XIJIAN_DEV", None)
os.environ.pop("XIJIAN_DEV_TOKEN_FILE", None)
# The overload monitor thread races test assertions; keep it off
# unless the specific test opts in by re-setting the env var.
# (过载监控线程会与测试断言竞争；除非特定测试通过重设环境变量
# 选择加入，否则保持关闭。)
os.environ.setdefault("XIJIAN_OVERLOAD_MONITOR", "0")
# The character-state tick thread is the A3.2 equivalent — keep it
# off by default; individual tests opt in via ``monkeypatch``.
# (角色状态滴答线程是 A3.2 的对应项 —— 默认关闭；单个测试通过
# ``monkeypatch`` 选择加入。)
os.environ.setdefault("XIJIAN_STATE_TICK", "0")
# The events scheduler thread (A4.1) — same posture as A3.2.
# (事件调度器线程 (A4.1) —— 同 A3.2 姿态。)
os.environ.setdefault("XIJIAN_EVENT_SCHEDULER", "0")
# The NPC tick thread (A4.2) — same posture as A3.2 / A4.1.
# (NPC 滴答线程 (A4.2) —— 同 A3.2 / A4.1 姿态。)
os.environ.setdefault("XIJIAN_NPC_TICK", "0")
# The A5.3 scheduled-backup thread — same posture as the others.
# (A5.3 定时备份线程 —— 与其它线程相同姿态。)
os.environ.setdefault("XIJIAN_BACKUP_SCHEDULER", "0")
# A4.1 scene generation: skip probing the core image backend so
# event-fire tests are deterministic (placeholder path, AC-2).
# Individual tests opt in via ``monkeypatch``.
# (A4.1 场景生成：跳过核心图像后端探测，使事件触发测试确定
# (占位路径 AC-2)；单个测试通过 ``monkeypatch`` 选择加入。)
os.environ.setdefault("XIJIAN_SCENE_GENERATION", "0")
# The A7 proactive-contact scan thread — same posture as the others.
# (A7 主动发起扫描线程 —— 与其他后台线程同姿态，默认关闭。)
os.environ.setdefault("XIJIAN_INITIATED_TICK", "0")

from xijian_api import auth  # noqa: E402  (import after env setup)
from xijian_api.app import create_app  # noqa: E402
from xijian_api.config import API_VERSION  # noqa: E402
from xijian_api.middleware import reset_idempotency_cache_for_testing  # noqa: E402
from xijian_api.stubs import state as stubs_state  # noqa: E402


BASE_URL = "http://localhost"


@pytest.fixture(scope="session")
def app():
    """Build the Flask app once per session in testing mode.
    (在测试模式下，每个会话构建一次 Flask 应用。)
    """
    # Reset module-level state so the token is initialised fresh.
    # (重置模块级状态，以便 token 重新初始化。)
    auth.reset_for_testing()
    application = create_app(testing=True)
    application.config.update(TESTING=True)
    _register_test_routes(application)
    yield application
    # No explicit teardown — Flask test client handles it.
    # (无显式拆卸 —— Flask 测试客户端处理它。)


def _register_test_routes(application) -> None:
    """Attach a couple of test-only POST routes used by the
    idempotency and error-format tests.  These are registered on
    the app instance itself so they go through the same
    middleware/error-handler pipeline as production routes.
    (附加几个仅用于测试的 POST 路由，供幂等性和错误格式测试使用。
    这些路由注册在应用实例上，因此它们经过与生产路由相同的
    中间件/错误处理管道。)
    """

    @application.post("/v1/__test__/echo")
    def _echo():
        from flask import jsonify, request

        # Echo the parsed body back.  ``force=True`` lets us accept
        # any Content-Type for the test.
        # (回显解析后的请求体。 ``force=True`` 让我们为测试接受任何
        # Content-Type。)
        body = request.get_json(force=True, silent=True) or {}
        return jsonify({"echo": body, "ok": True}), 200


@pytest.fixture(autouse=True)
def _reset_state(app):
    """Clear idempotency cache + stub state between tests.
    (在测试间清除幂等性缓存 + 存根状态。)

    ``stubs_state.reset_for_testing`` re-seeds defaults via
    ``seed_all()``, which in turn calls
    :func:`xijian_api.routes.models.seed_default_models` — that helper
    needs an active Flask ``app_context`` so it can read
    ``current_app.config["XIJIAN_CONFIG"]``.  We push the session
    app's context here so the re-seed sees the real config (and
    therefore registers the ``[[models]]`` entries that the model
    tests assert on).
    (``stubs_state.reset_for_testing`` 通过 ``seed_all()`` 重新播种默认值，
    后者又调用 :func:`xijian_api.routes.models.seed_default_models` ——
    该助手需要一个活跃的 Flask ``app_context`` 以便读取
    ``current_app.config["XIJIAN_CONFIG"]``。我们在此推送会话应用的上下文，
    以便重新播种看到真实配置（从而注册模型测试断言的 ``[[models]]`` 条目。))

    The overload module keeps its sliding window in module-level
    ``deque`` instances that survive ``state.reset_for_testing``; we
    reset those explicitly below.
    (过载模块在模块级 ``deque`` 实例中保持其滑动窗口，这些实例在
    ``state.reset_for_testing`` 后存活；我们在下方显式重置它们。)
    """
    reset_idempotency_cache_for_testing()
    with app.app_context():
        stubs_state.reset_for_testing()
        from xijian_api.stubs import overload as ov_stub
        ov_stub.reset_for_testing()
        from xijian_api.stubs import character_state as cs_stub
        cs_stub.reset_for_testing()
        # Re-install the A3.2 default status handlers (Critical
        # subscriber) after the reset cleared the registry.
        # (在重置清空注册表后重新安装 A3.2 默认状态处理器 (Critical 订阅者)。)
        # Guarded: the A3 chapter lands this helper in parallel; until
        # it exists the reset must not fail the whole suite.
        if hasattr(cs_stub, "install_default_status_handlers"):
            cs_stub.install_default_status_handlers()
        from xijian_api.stubs import events as events_stub
        events_stub.reset_for_testing()
        from xijian_api.stubs import npcs as npcs_stub
        npcs_stub.reset_for_testing()
        # ``overload.reset_for_testing()`` above cleared the action-handler
        # registry; reinstall the A4.2 → A5.4 cross-link so the
        # TestOverloadHandler cases in ``test_xijian_npcs`` see the
        # ``_suspend_for_overload`` handler.  Idempotent.
        # (上方的 ``overload.reset_for_testing()`` 清除了动作处理器注册表；
        # 重新安装 A4.2 → A5.4 交叉链接，以便 ``test_xijian_npcs`` 中的
        # TestOverloadHandler 用例看到 ``_suspend_for_overload`` 处理器。幂等。)
        npcs_stub.install_overload_handler()
        # A5.4 cross-links for the other three actions — same pattern:
        # the registry was cleared above, so reset the guarded flags and
        # re-install each consumer (memory compress / snapshots emergency
        # dump / tts degrade).
        # (A5.4 其余三个动作的交叉链接 —— 同样模式：注册表已被清空，
        # 因此重置受保护标志并重新安装每个消费者。)
        from xijian_api.stubs import memory as memory_stub
        memory_stub.reset_for_testing()
        memory_stub.install_overload_handler()
        from xijian_api.stubs import snapshots as snapshots_stub
        snapshots_stub.reset_for_testing()
        snapshots_stub.install_overload_handler()
        from xijian_api.stubs import tts_guard as tts_guard_stub
        tts_guard_stub.reset_for_testing()
        tts_guard_stub.install_overload_handler()
        from xijian_api.stubs import world_audit as wa_stub
        wa_stub.reset_for_testing()
        from xijian_api.stubs import world_compute_config as wcc_stub
        wcc_stub.reset_for_testing()
        from xijian_api.stubs import world_environment as we_stub
        we_stub.reset_for_testing()
        # A4.3 scene system.
        # (A4.3 场景系统。)
        from xijian_api.stubs import pois as pois_stub
        pois_stub.reset_for_testing()
        from xijian_api.stubs import travel_modes as tm_stub
        tm_stub.reset_for_testing()
        from xijian_api.stubs import scene_interactions as si_stub
        si_stub.reset_for_testing()
        # A4.4 economy system.
        # (A4.4 经济系统。)
        from xijian_api.stubs import world_currencies as wc_stub
        wc_stub.reset_for_testing()
        from xijian_api.stubs import world_economy_state as wes_stub
        wes_stub.reset_for_testing()
        from xijian_api.stubs import wallets as wallets_stub
        wallets_stub.reset_for_testing()
        from xijian_api.stubs import transactions as tx_stub
        tx_stub.reset_for_testing()
        from xijian_api.stubs import economy as economy_stub
        economy_stub.reset_for_testing()
        # A5.1 output-safety system.
        # (A5.1 输出安全系统。)
        from xijian_api.stubs import safety_rules as safety_rules_stub
        safety_rules_stub.reset_for_testing()
        from xijian_api.stubs import safety as safety_stub
        safety_stub.reset_for_testing()
        # A5.2 MCP-protection system.  Reset order matters:
        # ``mcp.reset_for_testing()`` clears the audit / freeze
        # / snapshot / rule buckets AND the per-world policy
        # store; the rulebook reset has to come first so the
        # sanitize pass on the next test starts with no
        # active ``forbidden_word`` rules.
        # (A5.2 MCP 保护系统。重置顺序很重要：
        # ``mcp.reset_for_testing()`` 清除审计/冻结/快照/规则桶
        # 和每世界策略存储；规则手册重置必须先来，以便下一个测试的
        # 清理遍历从无活跃 ``forbidden_word`` 规则开始。)
        from xijian_api.stubs import mcp_rules as mcp_rules_stub
        mcp_rules_stub.reset_for_testing()
        from xijian_api.stubs import mcp as mcp_stub
        mcp_stub.reset_for_testing()
        # A5.3 automatic backup.  ``reset_for_testing()``
        # wipes the snapshot bucket AND the policy record
        # so the next test starts from the spec's default
        # (5 GiB ceiling etc.).
        # (A5.3 自动备份。``reset_for_testing()`` 清除快照桶
        # 和策略记录，以便下一个测试从规范默认值 (5 GiB 上限等) 开始。)
        from xijian_api.stubs import snapshots as snap_stub
        snap_stub.reset_for_testing()
        # A1.1 manual backups — wipes the protected-module registry,
        # per-character associations and backup records; stops the
        # daily scheduler.
        # (A1.1 手动备份 — 清除受保护模块注册表、每角色关联和备份记录；
        # 停止每日调度器。)
        from xijian_api.stubs import manual_backups as mb_stub
        mb_stub.reset_for_testing()
        # A6 / A7 / A8 (added 2026-08-01).  The A6 hooks (reply /
        # sing engines) and the A7 tick thread are module-level
        # state that survives ``state.reset_for_testing``.
        # (A6 / A7 / A8 (2026-08-01 新增)。A6 钩子 (回复/唱歌引擎)
        # 与 A7 tick 线程是模块级状态，需要显式重置。)
        from xijian_api.stubs import voice_calls as vc_stub
        vc_stub.reset_for_testing()
        from xijian_api.stubs import character_initiated_actions as cia_stub
        cia_stub.reset_for_testing()
        from xijian_api.stubs import desktop_pets as dp_stub
        dp_stub.reset_for_testing()
    yield


@pytest.fixture()
def client(app):
    """Flask test client bound to the session-scoped app.
    (绑定到会话作用域应用的 Flask 测试客户端。)
    """
    return app.test_client()


@pytest.fixture()
def token():
    """Return the Bearer token the testing app uses.
    (返回测试应用使用的 Bearer token。)
    """
    return auth.get_token() or "test-token-do-not-use-in-prod"


@pytest.fixture()
def auth_headers(token):
    """Headers dict with a valid Authorization header.
    (带有有效 Authorization 头的头部字典。)
    """
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def base_url():
    """Bare base URL for tests that need to assemble paths.
    (供需要组装路径的测试使用的裸基础 URL。)
    """
    return BASE_URL


@pytest.fixture()
def api_version():
    """Return the API version constant the server advertises.
    (返回服务器宣传的 API 版本常量。)
    """
    return API_VERSION