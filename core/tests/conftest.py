"""Pytest fixtures for the XiJian API server.

We build a single app per session (the test suite is read-mostly and
fast enough that re-building the app per test isn't worth the cost).
Every test gets a fresh ``client`` so request-id / idempotency state
doesn't leak between tests in ways that affect assertions.
"""

from __future__ import annotations

import os

import pytest

# Make sure ``XIJIAN_DEV=1`` is *not* set when the test suite is
# collected — otherwise :func:`xijian_api.auth.setup_token` would try
# to write a real token file.  Testing mode bypasses that path but
# the env hygiene is still nice to have.
os.environ.pop("XIJIAN_DEV", None)
os.environ.pop("XIJIAN_DEV_TOKEN_FILE", None)
# The overload monitor thread races test assertions; keep it off
# unless the specific test opts in by re-setting the env var.
os.environ.setdefault("XIJIAN_OVERLOAD_MONITOR", "0")
# The character-state tick thread is the A3.2 equivalent — keep it
# off by default; individual tests opt in via ``monkeypatch``.
os.environ.setdefault("XIJIAN_STATE_TICK", "0")
# The events scheduler thread (A4.1) — same posture as A3.2.
os.environ.setdefault("XIJIAN_EVENT_SCHEDULER", "0")
# The NPC tick thread (A4.2) — same posture as A3.2 / A4.1.
os.environ.setdefault("XIJIAN_NPC_TICK", "0")

from xijian_api import auth  # noqa: E402  (import after env setup)
from xijian_api.app import create_app  # noqa: E402
from xijian_api.config import API_VERSION  # noqa: E402
from xijian_api.middleware import reset_idempotency_cache_for_testing  # noqa: E402
from xijian_api.stubs import state as stubs_state  # noqa: E402


BASE_URL = "http://localhost"


@pytest.fixture(scope="session")
def app():
    """Build the Flask app once per session in testing mode."""
    # Reset module-level state so the token is initialised fresh.
    auth.reset_for_testing()
    application = create_app(testing=True)
    application.config.update(TESTING=True)
    _register_test_routes(application)
    yield application
    # No explicit teardown — Flask test client handles it.


def _register_test_routes(application) -> None:
    """Attach a couple of test-only POST routes used by the
    idempotency and error-format tests.  These are registered on
    the app instance itself so they go through the same
    middleware/error-handler pipeline as production routes."""

    @application.post("/v1/__test__/echo")
    def _echo():
        from flask import jsonify, request

        # Echo the parsed body back.  ``force=True`` lets us accept
        # any Content-Type for the test.
        body = request.get_json(force=True, silent=True) or {}
        return jsonify({"echo": body, "ok": True}), 200


@pytest.fixture(autouse=True)
def _reset_state(app):
    """Clear idempotency cache + stub state between tests.

    ``stubs_state.reset_for_testing`` re-seeds defaults via
    ``seed_all()``, which in turn calls
    :func:`xijian_api.routes.models.seed_default_models` — that helper
    needs an active Flask ``app_context`` so it can read
    ``current_app.config["XIJIAN_CONFIG"]``.  We push the session
    app's context here so the re-seed sees the real config (and
    therefore registers the ``[[models]]`` entries that the model
    tests assert on).

    The overload module keeps its sliding window in module-level
    ``deque`` instances that survive ``state.reset_for_testing``; we
    reset those explicitly below.
    """
    reset_idempotency_cache_for_testing()
    with app.app_context():
        stubs_state.reset_for_testing()
        from xijian_api.stubs import overload as ov_stub
        ov_stub.reset_for_testing()
        from xijian_api.stubs import character_state as cs_stub
        cs_stub.reset_for_testing()
        from xijian_api.stubs import events as events_stub
        events_stub.reset_for_testing()
        from xijian_api.stubs import npcs as npcs_stub
        npcs_stub.reset_for_testing()
        # ``overload.reset_for_testing()`` above cleared the action-handler
        # registry; reinstall the A4.2 → A5.4 cross-link so the
        # TestOverloadHandler cases in ``test_xijian_npcs`` see the
        # ``_suspend_for_overload`` handler.  Idempotent.
        npcs_stub.install_overload_handler()
        from xijian_api.stubs import world_audit as wa_stub
        wa_stub.reset_for_testing()
        from xijian_api.stubs import world_compute_config as wcc_stub
        wcc_stub.reset_for_testing()
        from xijian_api.stubs import world_environment as we_stub
        we_stub.reset_for_testing()
        # A4.3 scene system.
        from xijian_api.stubs import pois as pois_stub
        pois_stub.reset_for_testing()
        from xijian_api.stubs import travel_modes as tm_stub
        tm_stub.reset_for_testing()
        from xijian_api.stubs import scene_interactions as si_stub
        si_stub.reset_for_testing()
        # A4.4 economy system.
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
        from xijian_api.stubs import mcp_rules as mcp_rules_stub
        mcp_rules_stub.reset_for_testing()
        from xijian_api.stubs import mcp as mcp_stub
        mcp_stub.reset_for_testing()
    yield


@pytest.fixture()
def client(app):
    """Flask test client bound to the session-scoped app."""
    return app.test_client()


@pytest.fixture()
def token():
    """Return the Bearer token the testing app uses."""
    return auth.get_token() or "test-token-do-not-use-in-prod"


@pytest.fixture()
def auth_headers(token):
    """Headers dict with a valid Authorization header."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def base_url():
    """Bare base URL for tests that need to assemble paths."""
    return BASE_URL


@pytest.fixture()
def api_version():
    """Return the API version constant the server advertises."""
    return API_VERSION
