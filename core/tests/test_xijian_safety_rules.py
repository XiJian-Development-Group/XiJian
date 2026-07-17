"""Tests for ``stubs.safety_rules`` (A5.1) and the
``/v1/xijian/safety/rules/*`` endpoints.

Covers:

* **Pure helpers** — kind / pattern / severity validation, regex
  compile (literal vs regex).
* **CRUD** — create / list / get / patch / delete with active
  toggle.
* **Hot path** — :func:`match_active_rules` returns sorted hits;
  broken regex is logged + skipped.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import safety_rules as rules_stub
from xijian_api.stubs.safety_rules import (
    DEFAULT_SEVERITY,
    KIND_FORBIDDEN_WORD,
    KIND_INJECTION_PATTERN,
    KIND_OOC_PATTERN,
    MAX_PATTERN_LEN,
    MAX_SEVERITY,
    MIN_SEVERITY,
    VALID_KINDS,
    SafetyRuleError,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestValidateKind:
    def test_ooc(self):
        assert rules_stub._validate_kind(KIND_OOC_PATTERN) == KIND_OOC_PATTERN

    def test_injection(self):
        assert rules_stub._validate_kind(KIND_INJECTION_PATTERN) == KIND_INJECTION_PATTERN

    def test_forbidden(self):
        assert rules_stub._validate_kind(KIND_FORBIDDEN_WORD) == KIND_FORBIDDEN_WORD

    @pytest.mark.parametrize("bad", ["", "unknown", None, 123])
    def test_invalid(self, bad):
        with pytest.raises(SafetyRuleError):
            rules_stub._validate_kind(bad)


class TestValidatePattern:
    def test_simple(self):
        assert rules_stub._validate_pattern("foo") == "foo"

    @pytest.mark.parametrize("bad", ["", None, 123, []])
    def test_invalid(self, bad):
        with pytest.raises(SafetyRuleError):
            rules_stub._validate_pattern(bad)

    def test_too_long(self):
        with pytest.raises(SafetyRuleError, match="too long"):
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
        with pytest.raises(SafetyRuleError):
            rules_stub._validate_severity(bad)

    @pytest.mark.parametrize("bad", [True, "3", 3.0, None, []])
    def test_non_int(self, bad):
        with pytest.raises(SafetyRuleError):
            rules_stub._validate_severity(bad)


class TestCompilePattern:
    def test_forbidden_word_literal(self):
        compiled = rules_stub._compile_pattern(KIND_FORBIDDEN_WORD, "bad word")
        assert compiled is not None
        assert compiled.search("this is a BAD word!") is not None

    def test_forbidden_word_case_insensitive(self):
        compiled = rules_stub._compile_pattern(KIND_FORBIDDEN_WORD, "FoO")
        assert compiled.search("foo bar") is not None

    def test_ooc_pattern_regex(self):
        compiled = rules_stub._compile_pattern(KIND_OOC_PATTERN, r"as an AI")
        assert compiled is not None
        assert compiled.search("I am speaking as an AI model") is not None

    def test_broken_regex_returns_none(self):
        compiled = rules_stub._compile_pattern(KIND_OOC_PATTERN, r"[invalid(")
        assert compiled is None


class TestValidKinds:
    def test_set_contents(self):
        assert VALID_KINDS == frozenset({
            KIND_OOC_PATTERN, KIND_INJECTION_PATTERN, KIND_FORBIDDEN_WORD,
        })


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreate:
    def test_minimal(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"as an AI"
        )
        assert record["rule_kind"] == KIND_OOC_PATTERN
        assert record["pattern"] == r"as an AI"
        assert record["severity"] == DEFAULT_SEVERITY
        assert record["is_active"] is True

    def test_full(self):
        record = rules_stub.create(
            rule_kind=KIND_INJECTION_PATTERN,
            pattern=r"ignore previous",
            severity=5,
            is_active=False,
        )
        assert record["severity"] == 5
        assert record["is_active"] is False

    def test_duplicate_id(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x",
            rule_id="rule_dup",
        )
        with pytest.raises(SafetyRuleError, match="already exists"):
            rules_stub.create(
                rule_kind=KIND_OOC_PATTERN, pattern="y",
                rule_id="rule_dup",
            )

    def test_invalid_kind(self):
        with pytest.raises(SafetyRuleError):
            rules_stub.create(rule_kind="unknown", pattern="x")

    def test_invalid_severity(self):
        with pytest.raises(SafetyRuleError):
            rules_stub.create(
                rule_kind=KIND_OOC_PATTERN, pattern="x", severity=99
            )


class TestListGet:
    def test_list_active_excludes_inactive(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="a", is_active=True
        )
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="b", is_active=False
        )
        active = rules_stub.list_active()
        assert all(r["is_active"] for r in active)
        assert any(r["pattern"] == "a" for r in active)
        assert not any(r["pattern"] == "b" for r in active)

    def test_list_active_sorted_by_severity(self):
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="low", severity=1)
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="high", severity=5)
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="mid", severity=3)
        active = rules_stub.list_active()
        patterns = [r["pattern"] for r in active]
        assert patterns == ["high", "mid", "low"]

    def test_list_active_filter_by_kind(self):
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="ooc")
        rules_stub.create(rule_kind=KIND_INJECTION_PATTERN, pattern="inj")
        ooc = rules_stub.list_active(rule_kind=KIND_OOC_PATTERN)
        assert all(r["rule_kind"] == KIND_OOC_PATTERN for r in ooc)
        assert len(ooc) == 1

    def test_list_all(self):
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="a")
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="b", is_active=False)
        all_rules = rules_stub.list_all()
        assert len(all_rules) == 2

    def test_get(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        assert rules_stub.get(record["id"]) is not None
        assert rules_stub.get("rule_phantom") is None


class TestUpdate:
    def test_patch_pattern(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="old"
        )
        updated = rules_stub.update(record["id"], {"pattern": "new"})
        assert updated["pattern"] == "new"

    def test_patch_severity(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x", severity=1
        )
        updated = rules_stub.update(record["id"], {"severity": 5})
        assert updated["severity"] == 5

    def test_patch_is_active(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        updated = rules_stub.update(record["id"], {"is_active": False})
        assert updated["is_active"] is False

    def test_patch_kind(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        updated = rules_stub.update(
            record["id"], {"rule_kind": KIND_INJECTION_PATTERN}
        )
        assert updated["rule_kind"] == KIND_INJECTION_PATTERN

    def test_patch_immutable_keys(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        with pytest.raises(SafetyRuleError, match="immutable"):
            rules_stub.update(record["id"], {"id": "rule_other"})
        with pytest.raises(SafetyRuleError, match="immutable"):
            rules_stub.update(record["id"], {"created_at": 0})

    def test_patch_invalid_severity(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        with pytest.raises(SafetyRuleError):
            rules_stub.update(record["id"], {"severity": 99})

    def test_patch_unknown_rule(self):
        assert rules_stub.update("rule_phantom", {"pattern": "x"}) is None

    def test_delete(self):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        assert rules_stub.delete(record["id"]) is True
        assert rules_stub.get(record["id"]) is None
        assert rules_stub.delete(record["id"]) is False


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------


class TestMatchActiveRules:
    def test_basic_match(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"as an AI", severity=4
        )
        hits = rules_stub.match_active_rules(
            "I am speaking as an AI right now", rule_kind=KIND_OOC_PATTERN
        )
        assert len(hits) == 1

    def test_no_match(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"as an AI", severity=4
        )
        hits = rules_stub.match_active_rules(
            "Hello there!", rule_kind=KIND_OOC_PATTERN
        )
        assert len(hits) == 0

    def test_inactive_skipped(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"as an AI", is_active=False
        )
        hits = rules_stub.match_active_rules(
            "I am as an AI", rule_kind=KIND_OOC_PATTERN
        )
        assert len(hits) == 0

    def test_kind_filter(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"foo"
        )
        hits = rules_stub.match_active_rules(
            "foo bar", rule_kind=KIND_INJECTION_PATTERN
        )
        assert len(hits) == 0

    def test_sorted_by_severity(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"X", severity=1
        )
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"X", severity=5
        )
        hits = rules_stub.match_active_rules("X", rule_kind=KIND_OOC_PATTERN)
        assert hits[0]["severity"] == 5

    def test_empty_text(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"foo"
        )
        assert rules_stub.match_active_rules("", rule_kind=KIND_OOC_PATTERN) == []

    def test_broken_regex_skipped(self):
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern=r"[invalid("
        )
        # No crash, just empty hits.
        assert rules_stub.match_active_rules(
            "hello", rule_kind=KIND_OOC_PATTERN
        ) == []

    def test_forbidden_word_hit(self):
        rules_stub.create(
            rule_kind=KIND_FORBIDDEN_WORD, pattern="secret password"
        )
        hits = rules_stub.match_active_rules(
            "Tell me the SECRET PASSWORD", rule_kind=KIND_FORBIDDEN_WORD
        )
        assert len(hits) == 1


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestHttpCreateGet:
    def test_create(self, client, auth_headers):
        body = {
            "rule_kind": KIND_OOC_PATTERN,
            "pattern": r"as an AI",
            "severity": 4,
        }
        res = client.post(
            "/v1/xijian/safety/rules", json=body, headers=auth_headers
        )
        assert res.status_code == 201
        data = res.get_json()
        assert data["rule_kind"] == KIND_OOC_PATTERN

    def test_create_missing_kind(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/safety/rules",
            json={"pattern": "x"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "rule_error"

    def test_get(self, client, auth_headers):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        res = client.get(
            f"/v1/xijian/safety/rules/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_get_unknown(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/safety/rules/rule_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404


class TestHttpListPatchDelete:
    def test_list_global(self, client, auth_headers):
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="x")
        res = client.get(
            "/v1/xijian/safety/rules", headers=auth_headers
        )
        assert res.status_code == 200

    def test_list_active_filter(self, client, auth_headers):
        rules_stub.create(rule_kind=KIND_OOC_PATTERN, pattern="a")
        rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="b", is_active=False
        )
        res = client.get(
            "/v1/xijian/safety/rules?active=true",
            headers=auth_headers,
        )
        assert res.status_code == 200
        rules = res.get_json()["rules"]
        assert all(r["is_active"] for r in rules)

    def test_list_invalid_kind(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/safety/rules?rule_kind=unknown",
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_patch(self, client, auth_headers):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        res = client.patch(
            f"/v1/xijian/safety/rules/{record['id']}",
            json={"severity": 5, "is_active": False},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["severity"] == 5
        assert data["is_active"] is False

    def test_patch_unknown(self, client, auth_headers):
        res = client.patch(
            "/v1/xijian/safety/rules/rule_phantom",
            json={"severity": 5},
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_delete(self, client, auth_headers):
        record = rules_stub.create(
            rule_kind=KIND_OOC_PATTERN, pattern="x"
        )
        res = client.delete(
            f"/v1/xijian/safety/rules/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/xijian/safety/rules"),
            ("POST", "/v1/xijian/safety/rules"),
            ("GET", "/v1/xijian/safety/rules/rule_phantom"),
            ("PATCH", "/v1/xijian/safety/rules/rule_phantom"),
            ("DELETE", "/v1/xijian/safety/rules/rule_phantom"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d"
            % (method, path, res.status_code)
        )
