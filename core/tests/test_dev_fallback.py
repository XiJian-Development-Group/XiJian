"""Tests for the S1/B3 dev-fallback switch in ``app._build_app_resilient``.

``app._build_app_resilient`` 中 S1/B3 降级开关的测试。

Previously the builder automatically retried in dev mode on any
``RuntimeError`` (typically a missing token file).  Now the automatic
fallback only happens when ``XIJIAN_ALLOW_DEV_FALLBACK=1`` or the
config is already in dev mode; otherwise the ``RuntimeError`` is
re-raised so production refuses to start degraded.
"""

from __future__ import annotations

import pytest

from xijian_api.app import _build_app_resilient
from xijian_api.config import Config


class _FakeApp:
    def __init__(self, config):
        self.config = {"XIJIAN_CONFIG": config}


def _production_config() -> Config:
    cfg = Config.empty()
    # Config.empty() defaults dev=False — assert to be explicit.
    assert cfg.server.dev is False
    return cfg


def _raise_runtime_error(*args, **kwargs):
    raise RuntimeError("token file missing and XIJIAN_DEV not set")


def test_production_reraises_without_fallback(monkeypatch):
    """No env var + dev=False → RuntimeError propagates (S1/B3)."""
    from xijian_api import app as app_module

    monkeypatch.delenv("XIJIAN_ALLOW_DEV_FALLBACK", raising=False)
    monkeypatch.setattr(app_module, "create_app", _raise_runtime_error)
    with pytest.raises(RuntimeError):
        _build_app_resilient(_production_config())


def test_env_var_allows_dev_fallback(monkeypatch):
    """XIJIAN_ALLOW_DEV_FALLBACK=1 → falls back to a dev-forced build (S1/B3)."""
    from xijian_api import app as app_module

    monkeypatch.setenv("XIJIAN_ALLOW_DEV_FALLBACK", "1")
    calls: list[Config] = []

    def _create(testing, config):
        calls.append(config)
        if len(calls) == 1:
            raise RuntimeError("token file missing")
        return _FakeApp(config)

    monkeypatch.setattr(app_module, "create_app", _create)
    app = _build_app_resilient(_production_config())
    # The second (fallback) build ran with dev forced on.
    assert calls[1].server.dev is True
    assert app.config["XIJIAN_CONFIG"] is calls[1]


def test_truthy_env_variants_allow_fallback(monkeypatch):
    """'true'/'yes' count as truthy, matching config._truthy semantics (S1/B3)."""
    from xijian_api import app as app_module

    for value in ("true", "YES", "on", "1"):
        monkeypatch.setenv("XIJIAN_ALLOW_DEV_FALLBACK", value)
        calls: list[Config] = []

        def _create(testing, config):
            calls.append(config)
            if len(calls) == 1:
                raise RuntimeError("token file missing")
            return _FakeApp(config)

        monkeypatch.setattr(app_module, "create_app", _create)
        app = _build_app_resilient(_production_config())
        assert calls[1].server.dev is True
        assert app.config["XIJIAN_CONFIG"] is calls[1]


def test_dev_config_allows_fallback_without_env(monkeypatch):
    """dev=True in config permits the fallback even with no env var (S1/B3)."""
    from xijian_api import app as app_module

    monkeypatch.delenv("XIJIAN_ALLOW_DEV_FALLBACK", raising=False)
    dev_cfg = _production_config()
    import dataclasses
    dev_cfg = dataclasses.replace(dev_cfg, server=dataclasses.replace(dev_cfg.server, dev=True))

    calls: list[Config] = []

    def _create(testing, config):
        calls.append(config)
        if len(calls) == 1:
            raise RuntimeError("token file missing")
        return _FakeApp(config)

    monkeypatch.setattr(app_module, "create_app", _create)
    _build_app_resilient(dev_cfg)
    assert len(calls) == 2
