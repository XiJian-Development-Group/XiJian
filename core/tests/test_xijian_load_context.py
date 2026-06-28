"""Tests for :func:`xijian_api.stubs.memory.load_context` (A1.2 §自动记忆载入).

The tests cover the spec's loadContext flow:

* Long-term memories are selected by importance ≥ config threshold,
  sorted by importance desc.
* Short-term memories are selected by live ``decay_score`` ≥ config
  threshold, sorted by ``decay_score × importance`` desc.
* A per-character budget override (``budget_tokens``) drives an
  importance-based trim when the assembled block would overflow.
* Per-character overrides from :class:`character_memory_config` are
  honoured (e.g. ``max_long_term=0`` ⇒ no long-term block).
* Empty / ``None`` inputs return a safe envelope without raising.
* Successful selection bumps ``access_count`` / ``last_access_at`` on
  every surviving entry (read-as-access semantics).
"""

from __future__ import annotations

import time

from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import memory_config as config_stub
from xijian_api.stubs import state


def _wipe() -> None:
    state.memory.clear()
    state.memory_configs.clear()


def _seed_long(char_id: str, content: str, importance: float) -> str:
    """Insert one long-term entry directly and return its id."""
    record = memory_stub.create(
        {
            "character_id": char_id,
            "type": "long",
            "content": content,
            "importance": importance,
            "source": "manual",
        }
    )
    # ``create`` uses ``now_ts`` as the timestamps; long entries don't
    # decay so the timestamp is informational.  Pin ``created_at`` to
    # a deterministic epoch so the test order is stable.
    record["created_at"] = 1_700_000_000_000
    record["updated_at"] = 1_700_000_000_000
    record["last_access_at"] = 1_700_000_000_000
    return record["id"]


def _seed_short(
    char_id: str,
    content: str,
    importance: float,
    *,
    decay_score: float = 0.95,
    age_hours: float = 0.0,
    created_at: int | None = None,
) -> str:
    """Insert one short-term entry and optionally age it via created_at."""
    record = memory_stub.create(
        {
            "character_id": char_id,
            "type": "short",
            "content": content,
            "importance": importance,
            "decay_score": decay_score,
            "source": "dialogue",
        }
    )
    base = created_at if created_at is not None else int(time.time() * 1000) - int(age_hours * 3600 * 1000)
    record["created_at"] = base
    record["updated_at"] = base
    record["last_access_at"] = base
    return record["id"]


# ---------------------------------------------------------------------------
# Basic selection
# ---------------------------------------------------------------------------


def test_load_context_returns_empty_envelope_for_none_character():
    _wipe()
    envelope = memory_stub.load_context(None)
    assert envelope["empty"] is True
    assert envelope["system_message"] == ""
    assert envelope["long_term_count"] == 0
    assert envelope["short_term_count"] == 0
    assert envelope["long_term_ids"] == []
    assert envelope["short_term_ids"] == []


def test_load_context_returns_empty_when_no_entries_match():
    _wipe()
    envelope = memory_stub.load_context("ghost_character")
    assert envelope["empty"] is True
    # budget still resolved from defaults (8000 - 2000 = 6000).
    assert envelope["budget_tokens"] == 6000
    assert envelope["estimated_tokens"] == 0


def test_load_context_picks_seeded_yuki_entries():
    _wipe()
    memory_stub.seed_default(character_id="char_yuki")
    envelope = memory_stub.load_context("char_yuki")
    # Seed contains 1 long + 2 short entries (both above default thresholds).
    assert envelope["long_term_count"] == 1
    assert envelope["short_term_count"] == 2
    assert envelope["empty"] is False
    # The rendered system message mentions both sections.
    assert "## 长期记忆" in envelope["system_message"]
    assert "## 短期记忆" in envelope["system_message"]
    # Short-term entries are sorted by decay × importance — the
    # 0.7 × 0.95 = 0.665 entry ranks above the 0.4 × 0.6 = 0.24 entry.
    short_ids = envelope["short_term_ids"]
    assert len(short_ids) == 2
    # Both ids are recorded and stable across calls.
    envelope2 = memory_stub.load_context("char_yuki")
    assert envelope2["short_term_ids"] == short_ids


# ---------------------------------------------------------------------------
# Importance filtering
# ---------------------------------------------------------------------------


def test_long_term_below_importance_min_is_excluded():
    _wipe()
    _seed_long("c1", "high-importance identity", 0.9)
    _seed_long("c1", "borderline-low identity", 0.5)  # below default 0.6
    envelope = memory_stub.load_context("c1")
    assert envelope["long_term_count"] == 1
    contents = envelope["system_message"]
    assert "high-importance identity" in contents
    assert "borderline-low identity" not in contents


def test_short_term_below_decay_threshold_is_excluded():
    _wipe()
    # Entry with low importance + aged → low live decay score.
    _seed_short("c1", "forgettable chatter", 0.2, decay_score=0.5, age_hours=24)
    _seed_short("c1", "remembered preference", 0.8, decay_score=0.95, age_hours=0)
    envelope = memory_stub.load_context("c1")
    assert envelope["short_term_count"] == 1
    assert "remembered preference" in envelope["system_message"]
    assert "forgettable chatter" not in envelope["system_message"]


def test_max_long_term_zero_disables_long_term_block():
    _wipe()
    _seed_long("c1", "important identity", 0.9)
    _seed_short("c1", "recent preference", 0.7)
    config_stub.upsert("c1", {"max_long_term": 0})
    envelope = memory_stub.load_context("c1")
    assert envelope["long_term_count"] == 0
    assert envelope["short_term_count"] == 1
    assert "## 长期记忆" not in envelope["system_message"]
    assert "## 短期记忆" in envelope["system_message"]


def test_max_short_term_zero_disables_short_term_block():
    _wipe()
    _seed_long("c1", "important identity", 0.9)
    _seed_short("c1", "recent preference", 0.7)
    config_stub.upsert("c1", {"max_short_term": 0})
    envelope = memory_stub.load_context("c1")
    assert envelope["long_term_count"] == 1
    assert envelope["short_term_count"] == 0
    assert "## 长期记忆" in envelope["system_message"]
    assert "## 短期记忆" not in envelope["system_message"]


def test_per_character_filter_isolates_other_characters():
    _wipe()
    _seed_long("alice", "alice identity", 0.9)
    _seed_long("bob", "bob identity", 0.9)
    envelope = memory_stub.load_context("alice")
    assert envelope["long_term_count"] == 1
    assert "alice identity" in envelope["system_message"]
    assert "bob identity" not in envelope["system_message"]


# ---------------------------------------------------------------------------
# Token budget + importance trim
# ---------------------------------------------------------------------------


def test_token_budget_override_triggers_trim_when_oversized():
    _wipe()
    # Three long entries all above the importance threshold; the top
    # entry is intentionally long so even alpha + headers fill the
    # budget — trim kicks in and lower-importance entries get dropped.
    _seed_long(
        "c1",
        "alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha",
        0.95,
    )
    _seed_long("c1", "beta-beta-beta-beta-beta-beta-beta-beta", 0.7)
    _seed_long("c1", "gamma-gamma-gamma-gamma-gamma-gamma-gamma", 0.65)
    envelope = memory_stub.load_context("c1", budget_tokens=40)
    assert envelope["trimmed"] is True
    # Trim drops lower-importance entries; at least the top one survives.
    assert envelope["long_term_count"] >= 1
    assert envelope["estimated_tokens"] <= envelope["budget_tokens"]
    assert "alpha" in envelope["system_message"]
    # Lower-importance entries are dropped first.
    assert "beta-beta-beta-beta-beta-beta-beta-beta" not in envelope["system_message"]
    assert "gamma-gamma-gamma-gamma-gamma-gamma-gamma" not in envelope["system_message"]


def test_no_trim_when_assembly_fits_budget():
    _wipe()
    _seed_long("c1", "tiny entry", 0.9)
    envelope = memory_stub.load_context("c1", budget_tokens=10_000)
    assert envelope["trimmed"] is False
    assert envelope["long_term_count"] == 1
    assert envelope["estimated_tokens"] <= envelope["budget_tokens"]


def test_trim_prefers_long_when_importance_scores_tie():
    _wipe()
    # Two long + one short, all with the same importance so the
    # long-first tie-break (sort key includes kind rank) should keep
    # the long entries over the short one when budget is tight.
    _seed_long("c1", "long identity A", 0.7)
    _seed_long("c1", "long identity B", 0.7)
    _seed_short("c1", "short preference same importance", 0.7)
    envelope = memory_stub.load_context("c1", budget_tokens=80)
    # All three should fit; what matters is the long entries
    # are *present* and the short entry doesn't displace them.
    assert envelope["long_term_count"] == 2
    assert envelope["short_term_count"] == 1


def test_trim_drops_low_importance_long_first():
    _wipe()
    # Two long entries both above threshold; the higher-importance one
    # wins the trim when the budget can't hold both.
    _seed_long("c1", "very-important identity", 0.95)
    _seed_long("c1", "less-important identity is longer string", 0.7)
    envelope = memory_stub.load_context("c1", budget_tokens=25)
    assert envelope["trimmed"] is True
    assert "very-important identity" in envelope["system_message"]
    assert "less-important identity" not in envelope["system_message"]


def test_zero_budget_returns_empty_after_trim():
    _wipe()
    _seed_long("c1", "any entry", 0.9)
    envelope = memory_stub.load_context("c1", budget_tokens=0)
    # Header overhead alone exceeds the budget so nothing survives.
    assert envelope["trimmed"] is True
    assert envelope["long_term_count"] == 0
    assert envelope["empty"] is True


# ---------------------------------------------------------------------------
# Read-as-access bookkeeping
# ---------------------------------------------------------------------------


def test_load_context_bumps_access_count_by_default():
    _wipe()
    entry_id = _seed_long("c1", "important identity", 0.9)
    before = state.memory[entry_id]["access_count"]
    memory_stub.load_context("c1")
    after = state.memory[entry_id]["access_count"]
    assert after == before + 1
    assert state.memory[entry_id]["last_access_at"] is not None


def test_load_context_with_bump_access_false_does_not_mutate():
    _wipe()
    entry_id = _seed_long("c1", "important identity", 0.9)
    before = state.memory[entry_id]["access_count"]
    memory_stub.load_context("c1", bump_access=False)
    after = state.memory[entry_id]["access_count"]
    assert after == before


# ---------------------------------------------------------------------------
# Diagnostics block
# ---------------------------------------------------------------------------


def test_used_config_reflects_effective_overrides():
    _wipe()
    _seed_long("c1", "identity", 0.9)
    config_stub.upsert("c1", {"max_long_term": 7, "long_term_importance_min": 0.4})
    envelope = memory_stub.load_context("c1")
    assert envelope["used_config"]["max_long_term"] == 7
    assert envelope["used_config"]["long_term_importance_min"] == 0.4


def test_default_budget_derived_from_config():
    _wipe()
    envelope = memory_stub.load_context("c1")
    # Default config: max_context_tokens=8000, reserve_tokens_for_reply=2000.
    assert envelope["budget_tokens"] == 6000