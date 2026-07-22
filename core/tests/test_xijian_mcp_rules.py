"""Tests for ``stubs.mcp_rules`` (A5.2) and the
``/v1/xijian/mcp/rules/*`` endpoints.

Covers:

* **Pure helpers** — kind / mode / pattern / severity validation,
  regex compile.
* **CRUD** — create / list / get / patch / delete with active
  toggle.
* **Hot path** — :func:`match_action_rules` returns sorted hits;
  broken regex is logged + skipped; unknown kind returns no
  matches.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import mcp_rules as rules_stub
from xijian_api.stubs.mcp_rules import (
    DEFAULT_SEVERITY,
    KIND_APP_LAUNCH,
    KIND_FILE_DELETE,
    KIND_FILE_READ,
    KIND_FILE_WRITE,
    KIND_NETWORK,
    KIND_SETTINGS_MODIFY,
    KIND_SHELL,
    KIND_SYSTEM_CMD,
    MAX_PATTERN_LEN,
    MAX_SEVERITY,
    MIN_SEVERITY,
    MODE_BLACKLIST,
    MODE_WHITELIST,
    VALID_KINDS,
    VALID_MODES,
    MCPRuleError,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestValidateKind:
    @pytest.mark.parametrize("kind", [
        KIND_FILE_DELETE, KIND_FILE_WRITE, KIND_FILE_READ,
        KIND_SHELL, KIND_NETWORK, KIND_APP_LAUNCH,
        KIND_SETTINGS_MODIFY, KIND_SYSTEM_CMD,
    ])
    def test_each_valid(self, kind):
        assert rules_stub._validate_kind(kind) == kind

    @pytest.mark.parametrize("bad", ["", "unknown", None, 123, []])
    def test_invalid(self, bad):
        with pytest.raises(MCPRuleError):
            rules_stub._validate_kind(bad)

    def test_valid_kinds_is_frozenset(self):
        assert isinstance(VALID_KINDS, frozenset)
        assert len(VALID_KINDS) == 8


class TestValidateMode:
    def test_blacklist(self):
        assert rules_stub._validate_mode(MODE_BLACKLIST) == MODE_BLACKLIST

    def test_whitelist(self):
        assert rules_stub._validate_mode(MODE_WHITELIST) == MODE_WHITELIST

    @pytest.mark.parametrize("bad", ["", "unknown", None, 123])
    def test_invalid(self, bad):
        with pytest.raises(MCPRuleError):
            rules_stub._validate_mode(bad)

    def test_valid_modes_is_frozenset(self):
        assert isinstance(VALID_MODES, frozenset)
        assert VALID_MODES == frozenset({MODE_BLACKLIST, MODE_WHITELIST})


class TestValidatePattern:
    def test_simple(self):
        assert rules_stub._validate_pattern("foo") == "foo"

    def test_regex_chars_allowed(self):
        # MCP rules are always regex (not literal), so
        # metachars must be accepted.
        assert rules_stub._validate_pattern("rm\\s+-rf\\s+/") == "rm\\s+-rf\\s+/"

    @pytest.mark.parametrize("bad", ["", None, 123, []])
    def test_invalid(self, bad):
        with pytest.raises(MCPRuleError):
            rules_stub._validate_pattern(bad)

    def test_too_long(self):
        with pytest.raises(MCPRuleError, match="too long"):
            rules_stub._validate_pattern("x" * (MAX_PATTERN_LEN + 1))


class TestValidateSeverity:
    def test_min(self):
        assert rules_stub._validate_severity(MIN_SEVERITY) == MIN_SEVERITY

    def test_max(self):
        assert rules_stub._validate_severity(MAX_SEVERITY) == MAX_SEVERITY

    def test_default(self):
        assert DEFAULT_SEVERITY == 3

    @pytest.mark.parametrize("bad", [MIN_SEVERITY - 1, MAX_SEVERITY + 1, 0, 6])
    def test_out_of_range(self, bad):
        with pytest.raises(MCPRuleError):
            rules_stub._validate_severity(bad)

    @pytest.mark.parametrize("bad", [True, "3", 3.0, None, []])
    def test_non_int(self, bad):
        with pytest.raises(MCPRuleError):
            rules_stub._validate_severity(bad)


class TestCompilePattern:
    def test_simple(self):
        compiled = rules_stub._compile_pattern(KIND_SHELL, "rm\\s+-rf")
        assert compiled is not None
        assert compiled.search("rm -rf /etc") is not None

    def test_case_insensitive(self):
        compiled = rules_stub._compile_pattern(KIND_SHELL, "SHUTDOWN")
        assert compiled.search("please Shutdown now") is not None

    def test_broken_regex_returns_none(self):
        compiled = rules_stub._compile_pattern(KIND_SHELL, "[unterminated")
        assert compiled is None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreate:
    def test_minimal(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL,
            pattern="rm",
            mode=MODE_BLACKLIST,
        )
        assert record["action_kind"] == KIND_SHELL
        assert record["mode"] == MODE_BLACKLIST
        assert record["severity"] == DEFAULT_SEVERITY
        assert record["is_active"] is True
        assert record["id"].startswith("mcpr_")
        assert "created_at" in record
        assert "updated_at" in record

    def test_all_fields(self):
        record = rules_stub.create(
            action_kind=KIND_FILE_DELETE,
            pattern="/etc/.*",
            mode=MODE_BLACKLIST,
            severity=5,
            is_active=False,
        )
        assert record["severity"] == 5
        assert record["is_active"] is False

    def test_duplicate_id(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
            rule_id="mcpr_test_dup",
        )
        with pytest.raises(MCPRuleError, match="already exists"):
            rules_stub.create(
                action_kind=KIND_SHELL, pattern="y", mode=MODE_BLACKLIST,
                rule_id="mcpr_test_dup",
            )

    def test_invalid_kind(self):
        with pytest.raises(MCPRuleError, match="action_kind"):
            rules_stub.create(
                action_kind="unknown", pattern="x", mode=MODE_BLACKLIST,
            )

    def test_invalid_mode(self):
        with pytest.raises(MCPRuleError, match="mode"):
            rules_stub.create(
                action_kind=KIND_SHELL, pattern="x", mode="unknown",
            )

    def test_invalid_severity(self):
        with pytest.raises(MCPRuleError, match="severity"):
            rules_stub.create(
                action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
                severity=10,
            )

    def test_empty_pattern(self):
        with pytest.raises(MCPRuleError, match="pattern"):
            rules_stub.create(
                action_kind=KIND_SHELL, pattern="", mode=MODE_BLACKLIST,
            )


class TestGet:
    def test_existing(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        fetched = rules_stub.get(record["id"])
        assert fetched == record

    def test_missing(self):
        assert rules_stub.get("mcpr_nope") is None


class TestListActive:
    def test_empty(self):
        assert rules_stub.list_active() == []

    def test_filters_inactive(self):
        a = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
            is_active=True,
        )
        b = rules_stub.create(
            action_kind=KIND_SHELL, pattern="y", mode=MODE_BLACKLIST,
            is_active=False,
        )
        out = rules_stub.list_active()
        ids = [r["id"] for r in out]
        assert a["id"] in ids
        assert b["id"] not in ids

    def test_filters_by_action_kind(self):
        a = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        b = rules_stub.create(
            action_kind=KIND_NETWORK, pattern="y", mode=MODE_BLACKLIST,
        )
        out = rules_stub.list_active(action_kind=KIND_SHELL)
        ids = [r["id"] for r in out]
        assert a["id"] in ids
        assert b["id"] not in ids

    def test_filters_by_mode(self):
        a = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        b = rules_stub.create(
            action_kind=KIND_SHELL, pattern="y", mode=MODE_WHITELIST,
        )
        out = rules_stub.list_active(mode=MODE_WHITELIST)
        ids = [r["id"] for r in out]
        assert b["id"] in ids
        assert a["id"] not in ids

    def test_sorted_by_severity_desc(self):
        low = rules_stub.create(
            action_kind=KIND_SHELL, pattern="a", mode=MODE_BLACKLIST,
            severity=1,
        )
        high = rules_stub.create(
            action_kind=KIND_SHELL, pattern="b", mode=MODE_BLACKLIST,
            severity=5,
        )
        mid = rules_stub.create(
            action_kind=KIND_SHELL, pattern="c", mode=MODE_BLACKLIST,
            severity=3,
        )
        out = rules_stub.list_active()
        ids = [r["id"] for r in out]
        # All three present, in severity desc order.
        assert ids.index(high["id"]) < ids.index(mid["id"]) < ids.index(low["id"])


class TestListAll:
    def test_includes_inactive(self):
        a = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
            is_active=True,
        )
        b = rules_stub.create(
            action_kind=KIND_SHELL, pattern="y", mode=MODE_BLACKLIST,
            is_active=False,
        )
        out = rules_stub.list_all()
        ids = [r["id"] for r in out]
        assert a["id"] in ids
        assert b["id"] in ids

    def test_empty(self):
        assert rules_stub.list_all() == []


class TestUpdate:
    def test_patch_severity(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
            severity=3,
        )
        updated = rules_stub.update(record["id"], {"severity": 5})
        assert updated["severity"] == 5
        assert updated["updated_at"] >= record["created_at"]

    def test_patch_pattern(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        updated = rules_stub.update(record["id"], {"pattern": "y"})
        assert updated["pattern"] == "y"

    def test_patch_is_active(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
            is_active=True,
        )
        updated = rules_stub.update(record["id"], {"is_active": False})
        assert updated["is_active"] is False

    def test_patch_mode(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        updated = rules_stub.update(record["id"], {"mode": MODE_WHITELIST})
        assert updated["mode"] == MODE_WHITELIST

    def test_patch_action_kind(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        updated = rules_stub.update(record["id"], {"action_kind": KIND_NETWORK})
        assert updated["action_kind"] == KIND_NETWORK

    def test_immutable_id(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        with pytest.raises(MCPRuleError, match="immutable"):
            rules_stub.update(record["id"], {"id": "mcpr_other"})

    def test_immutable_created_at(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        with pytest.raises(MCPRuleError, match="immutable"):
            rules_stub.update(record["id"], {"created_at": 0.0})

    def test_invalid_severity_patch(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        with pytest.raises(MCPRuleError):
            rules_stub.update(record["id"], {"severity": 99})

    def test_invalid_is_active_patch(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        with pytest.raises(MCPRuleError):
            rules_stub.update(record["id"], {"is_active": "yes"})

    def test_missing(self):
        assert rules_stub.update("mcpr_nope", {"severity": 5}) is None


class TestDelete:
    def test_existing(self):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        assert rules_stub.delete(record["id"]) is True
        assert rules_stub.get(record["id"]) is None

    def test_missing(self):
        assert rules_stub.delete("mcpr_nope") is False


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------


class TestMatchActionRules:
    def test_empty(self):
        assert rules_stub.match_action_rules(KIND_SHELL, "rm -rf /") == []

    def test_blacklist_hit(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="rm\\s+-rf",
            mode=MODE_BLACKLIST, severity=5,
        )
        out = rules_stub.match_action_rules(KIND_SHELL, "rm -rf /etc")
        assert len(out) == 1
        assert out[0]["mode"] == MODE_BLACKLIST

    def test_whitelist_hit(self):
        rules_stub.create(
            action_kind=KIND_APP_LAUNCH, pattern="^chrome$",
            mode=MODE_WHITELIST, severity=3,
        )
        out = rules_stub.match_action_rules(KIND_APP_LAUNCH, "chrome")
        assert len(out) == 1
        assert out[0]["mode"] == MODE_WHITELIST

    def test_filters_by_action_kind(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        rules_stub.create(
            action_kind=KIND_NETWORK, pattern="x", mode=MODE_BLACKLIST,
        )
        out = rules_stub.match_action_rules(KIND_SHELL, "x")
        assert len(out) == 1
        assert out[0]["action_kind"] == KIND_SHELL

    def test_skips_inactive(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
            is_active=False,
        )
        assert rules_stub.match_action_rules(KIND_SHELL, "x") == []

    def test_skips_broken_regex(self):
        # Pattern doesn't compile → matcher skips silently.
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="[broken",
            mode=MODE_BLACKLIST, severity=5,
        )
        assert rules_stub.match_action_rules(KIND_SHELL, "anything") == []

    def test_unknown_kind_returns_empty(self):
        # Unknown action_kind must NOT fall through to a
        # different kind's rules.
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        assert rules_stub.match_action_rules("unknown_kind", "x") == []

    def test_empty_payload(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        assert rules_stub.match_action_rules(KIND_SHELL, "") == []
        assert rules_stub.match_action_rules(KIND_SHELL, None) == []

    def test_sorted_by_severity_desc(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="common",
            mode=MODE_BLACKLIST, severity=1,
        )
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="common",
            mode=MODE_BLACKLIST, severity=5,
        )
        out = rules_stub.match_action_rules(KIND_SHELL, "common pattern")
        assert out[0]["severity"] == 5
        assert out[1]["severity"] == 1

    def test_case_insensitive_match(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="SHUTDOWN",
            mode=MODE_BLACKLIST,
        )
        out = rules_stub.match_action_rules(KIND_SHELL, "shutdown now")
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_seed_default_is_idempotent(self):
        rules_stub.seed_default()
        rules_stub.seed_default()
        assert rules_stub.list_all() == []

    def test_reset_for_testing_wipes(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        rules_stub.reset_for_testing()
        assert rules_stub.list_all() == []


# ---------------------------------------------------------------------------
# HTTP / route layer
# ---------------------------------------------------------------------------


class TestHTTPCreate:
    def test_create_via_http(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/rules",
            headers=auth_headers,
            json={
                "action_kind": KIND_SHELL,
                "pattern": "rm\\s+-rf",
                "mode": MODE_BLACKLIST,
                "severity": 5,
            },
        )
        assert res.status_code == 201
        body = res.get_json()
        assert body["action_kind"] == KIND_SHELL
        assert body["mode"] == MODE_BLACKLIST
        assert body["severity"] == 5
        assert body["id"].startswith("mcpr_")

    def test_create_missing_action_kind(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/rules",
            headers=auth_headers,
            json={"pattern": "x", "mode": MODE_BLACKLIST},
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "rule_error"

    def test_create_missing_mode(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/rules",
            headers=auth_headers,
            json={"action_kind": KIND_SHELL, "pattern": "x"},
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "rule_error"

    def test_create_invalid_body(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/rules",
            headers=auth_headers,
            data="not json",
        )
        assert res.status_code == 400


class TestHTTPList:
    def test_list_all(self, client, auth_headers):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="a", mode=MODE_BLACKLIST,
        )
        rules_stub.create(
            action_kind=KIND_NETWORK, pattern="b", mode=MODE_BLACKLIST,
        )
        res = client.get(
            "/v1/xijian/mcp/rules", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert len(body["rules"]) == 2

    def test_list_active_filter(self, client, auth_headers):
        a = rules_stub.create(
            action_kind=KIND_SHELL, pattern="a", mode=MODE_BLACKLIST,
            is_active=True,
        )
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="b", mode=MODE_BLACKLIST,
            is_active=False,
        )
        res = client.get(
            "/v1/xijian/mcp/rules?active=1", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        ids = [r["id"] for r in body["rules"]]
        assert a["id"] in ids
        assert len(body["rules"]) == 1

    def test_list_filter_by_kind(self, client, auth_headers):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="a", mode=MODE_BLACKLIST,
        )
        rules_stub.create(
            action_kind=KIND_NETWORK, pattern="b", mode=MODE_BLACKLIST,
        )
        res = client.get(
            "/v1/xijian/mcp/rules?action_kind=shell",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert len(body["rules"]) == 1
        assert body["rules"][0]["action_kind"] == KIND_SHELL

    def test_list_filter_by_mode(self, client, auth_headers):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="a", mode=MODE_BLACKLIST,
        )
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="b", mode=MODE_WHITELIST,
        )
        res = client.get(
            "/v1/xijian/mcp/rules?mode=whitelist",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert len(body["rules"]) == 1
        assert body["rules"][0]["mode"] == MODE_WHITELIST

    def test_list_invalid_kind(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/mcp/rules?action_kind=unknown",
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_list_invalid_mode(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/mcp/rules?mode=unknown",
            headers=auth_headers,
        )
        assert res.status_code == 400


class TestHTTPGet:
    def test_existing(self, client, auth_headers):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        res = client.get(
            f"/v1/xijian/mcp/rules/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["id"] == record["id"]

    def test_missing(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/mcp/rules/mcpr_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404


class TestHTTPPatch:
    def test_patch(self, client, auth_headers):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
            severity=3,
        )
        res = client.patch(
            f"/v1/xijian/mcp/rules/{record['id']}",
            headers=auth_headers,
            json={"severity": 5, "is_active": False},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["severity"] == 5
        assert body["is_active"] is False

    def test_patch_invalid(self, client, auth_headers):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        res = client.patch(
            f"/v1/xijian/mcp/rules/{record['id']}",
            headers=auth_headers,
            json={"severity": 99},
        )
        assert res.status_code == 400

    def test_patch_missing(self, client, auth_headers):
        res = client.patch(
            "/v1/xijian/mcp/rules/mcpr_phantom",
            headers=auth_headers,
            json={"severity": 3},
        )
        assert res.status_code == 404


class TestHTTPDelete:
    def test_delete(self, client, auth_headers):
        record = rules_stub.create(
            action_kind=KIND_SHELL, pattern="x", mode=MODE_BLACKLIST,
        )
        res = client.delete(
            f"/v1/xijian/mcp/rules/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["deleted"] is True

    def test_delete_missing(self, client, auth_headers):
        res = client.delete(
            "/v1/xijian/mcp/rules/mcpr_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/xijian/mcp/rules"),
            ("POST", "/v1/xijian/mcp/rules"),
            ("GET", "/v1/xijian/mcp/rules/mcpr_phantom"),
            ("PATCH", "/v1/xijian/mcp/rules/mcpr_phantom"),
            ("DELETE", "/v1/xijian/mcp/rules/mcpr_phantom"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d"
            % (method, path, res.status_code)
        )
