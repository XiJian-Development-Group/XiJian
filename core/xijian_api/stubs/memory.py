"""Stub memory service — entries CRUD + decay + recall_search + async ops.

This module backs ``/v1/xijian/memory/*`` and is also the data source for
the forced recall pipeline described in the A1.2 spec of
``docs/Dev. Function List功能清单v2.md``.

Data model (per A1.2 §技术视角)
--------------------------------

Each entry carries:

* ``type``: ``"long"`` or ``"short"``
* ``importance``: 0.0–1.0 (REAL in SQL)
* ``decay_score``: 0.0–1.0, dynamic for ``short`` entries
* ``access_count`` / ``last_access_at``: bumped on every successful recall
* ``source``: ``dialogue`` / ``manual`` / ``world_event`` / ``derived``
* ``source_ref_id``: optional link back to the originating conversation /
  event
* ``tags``: JSON array
* ``embedding`` / ``embedding_model``: reserved for later (TODO)

Backward compatibility
----------------------

The old ``attributes`` field (``{importance: "high", decay: "slow", ...}``)
is still accepted on :func:`create` and surfaces in :func:`get` / :func:`list_all`
records so the route layer keeps working.  New code should rely on the
typed fields above.

Seeding
-------

:func:`seed_default` populates three demo entries (long-term identity,
short-term preference, short-term fact containing the keyword "冰淇淋"
so search tests have something to find).  Reset hooks in
:func:`xijian_api.stubs.state.reset_for_testing` re-run this on every
test boundary.
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_memory_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


#: Default character id used when seeded entries don't belong to a
#: specific character.  Matches the convention used by other stubs
#: (e.g. ``char_yuki`` is the canonical demo character).
_DEFAULT_CHARACTER = "char_yuki"

#: Per-character default for ``short_term_decay_rate`` (per spec: 0.05/hour).
DEFAULT_SHORT_TERM_DECAY_RATE = 0.05

#: Per-character default for ``short_term_importance_min`` (per spec: 0.3).
DEFAULT_SHORT_TERM_IMPORTANCE_MIN = 0.3

#: Per-character default for ``long_term_importance_min`` (per spec: 0.6).
DEFAULT_LONG_TERM_IMPORTANCE_MIN = 0.6


# ---------------------------------------------------------------------------
# Helpers — coercion from old payload shapes
# ---------------------------------------------------------------------------


def _coerce_importance(raw: Any, *, default: float = 0.5) -> float:
    """Best-effort coerce an importance value into ``[0.0, 1.0]``.

    Accepts the legacy string form (``"high"`` / ``"normal"`` / ``"low"``)
    as well as floats / ints.  Out-of-range numbers are clamped.
    """
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in {"high", "h"}:
            return 0.9
        if key in {"normal", "n", "medium", "med", "default"}:
            return 0.5
        if key in {"low", "l"}:
            return 0.2
        try:
            return max(0.0, min(1.0, float(raw)))
        except ValueError:
            return default
    return default


def _coerce_decay_score(raw: Any, *, default: float = 1.0) -> float:
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in {"fast"}:
            return 0.3
        if key in {"slow"}:
            return 0.95
        if key in {"normal", "default"}:
            return 0.7
        try:
            return max(0.0, min(1.0, float(raw)))
        except ValueError:
            return default
    return default


def _normalise_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(t) for t in raw if t]
    if isinstance(raw, str):
        # Allow comma-separated strings.
        return [t.strip() for t in raw.split(",") if t.strip()]
    return [str(raw)]


# ---------------------------------------------------------------------------
# Record creation
# ---------------------------------------------------------------------------


def _new_entry(payload: dict) -> dict:
    """Build a fully-typed memory record from a payload.

    The legacy ``attributes`` block (string ``importance`` / ``decay``)
    is preserved on the record for callers that still read it; new
    readers should prefer the typed fields.
    """
    legacy_attributes = (payload.get("attributes") or {}).copy()
    importance = payload.get("importance")
    if importance is None:
        importance = legacy_attributes.get("importance")
    importance_f = _coerce_importance(importance)

    decay = payload.get("decay_score")
    if decay is None:
        decay = legacy_attributes.get("decay")
    decay_f = _coerce_decay_score(decay)

    entry_type = payload.get("type")
    if entry_type not in {"long", "short"}:
        # Infer from importance: high importance → long, else short.
        entry_type = "long" if importance_f >= DEFAULT_LONG_TERM_IMPORTANCE_MIN else "short"

    source = payload.get("source") or legacy_attributes.get("category") or "manual"

    record = {
        "id": gen_memory_id(),
        "object": "memory.entry",
        "character_id": payload.get("character_id"),
        "type": entry_type,
        "content": payload.get("content", ""),
        "importance": importance_f,
        "source": source,
        "source_ref_id": payload.get("source_ref_id"),
        "tags": _normalise_tags(payload.get("tags")),
        "access_count": int(payload.get("access_count", 0) or 0),
        "last_access_at": payload.get("last_access_at"),
        "decay_score": decay_f,
        "created_at": now_ts(),
        "updated_at": now_ts(),
        "deleted_at": None,
        # Legacy compatibility — keep the old shape available so the
        # existing route layer (and any callers reading ``attributes``)
        # keep working without forcing an immediate migration.
        "attributes": legacy_attributes or {
            "importance": (
                "high" if importance_f >= 0.7
                else "low" if importance_f <= 0.3
                else "normal"
            ),
            "decay": (
                "slow" if decay_f >= 0.9
                else "fast" if decay_f <= 0.4
                else "normal"
            ),
            "category": source,
        },
    }
    return record


# ---------------------------------------------------------------------------
# Public CRUD
# ---------------------------------------------------------------------------


def create(payload: dict) -> dict:
    record = _new_entry(payload)
    state.memory[record["id"]] = record
    return record


def list_all(
    *,
    character_id: str | None = None,
    tags: list[str] | None = None,
    importance: str | None = None,
    type: str | None = None,  # noqa: A002 — match SQL column name
) -> list[dict]:
    items = list(state.memory.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]
    if tags:
        items = [it for it in items if any(t in (it.get("tags") or []) for t in tags)]
    if importance:
        items = [
            it
            for it in items
            if (it.get("attributes") or {}).get("importance") == importance
        ]
    if type:
        items = [it for it in items if it.get("type") == type]
    return items


def get(entry_id: str) -> dict | None:
    return state.memory.get(entry_id)


def update(entry_id: str, patch: dict) -> dict | None:
    record = state.memory.get(entry_id)
    if record is None:
        return None
    if "content" in patch:
        record["content"] = patch["content"]
    if "tags" in patch:
        record["tags"] = _normalise_tags(patch["tags"])
    if "attributes" in patch:
        record["attributes"] = {**(record.get("attributes") or {}), **patch["attributes"]}
    # Allow the new typed fields to be patched directly.
    if "importance" in patch:
        record["importance"] = _coerce_importance(patch["importance"])
    if "decay_score" in patch:
        record["decay_score"] = _coerce_decay_score(patch["decay_score"])
    if "type" in patch and patch["type"] in {"long", "short"}:
        record["type"] = patch["type"]
    record["updated_at"] = now_ts()
    return record


def delete(entry_id: str) -> bool:
    return state.memory.pop(entry_id, None) is not None


def search(
    *,
    query: str,
    character_id: str | None = None,
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    """Legacy keyword search — kept for route-level ``POST /memory/search``.

    New callers should prefer :func:`recall_search`, which applies the
    importance / decay ranking defined in the A1.2 spec.
    """
    q = (query or "").lower()
    items = list(state.memory.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]

    hits: list[dict] = []
    for it in items:
        content = (it.get("content") or "").lower()
        if not content:
            continue
        score = 0.95 if q and q in content else 0.6 + random.random() * 0.25
        if score < min_score:
            continue
        hits.append({"entry": it, "score": round(score, 3)})

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[: max(1, top_k)]


# ---------------------------------------------------------------------------
# Decay algorithm (A1.2 §技术视角 → 遗忘算法)
# ---------------------------------------------------------------------------


def compute_decay_score(
    entry: dict,
    *,
    now: int | None = None,
    rate: float = DEFAULT_SHORT_TERM_DECAY_RATE,
) -> float:
    """Return ``decay_score(t) = decay_score(t₀) × exp(-rate × Δh)``.

    ``t₀`` is the more recent of ``last_access_at`` and ``created_at``.
    ``Δh`` is the number of hours between ``t₀`` and ``now`` (clamped to
    non-negative).  Long-term entries are not subject to decay — their
    score stays at 1.0 — matching the spec's "长期记忆永不衰减".
    """
    if (entry.get("type") or "short") == "long":
        return 1.0
    if now is None:
        now = now_ts()
    t0 = entry.get("last_access_at") or entry.get("created_at") or now
    delta_h = max(0.0, (now - t0) / 3600.0)
    base = float(entry.get("decay_score", 1.0) or 1.0)
    return base * math.exp(-rate * delta_h)


def should_promote_to_long(
    entry: dict,
    *,
    threshold: float = DEFAULT_SHORT_TERM_IMPORTANCE_MIN,
    importance_min: float = 0.5,
) -> bool:
    """Per spec: a short-term entry whose decay score drops below the
    threshold AND has fewer than 2 accesses AND importance ≥ 0.5 is a
    long-term promotion candidate."""
    if (entry.get("type") or "short") != "short":
        return False
    if float(entry.get("access_count", 0) or 0) >= 2:
        return False
    if float(entry.get("importance", 0.0) or 0.0) < importance_min:
        return False
    score = float(entry.get("decay_score", 1.0) or 1.0)
    return score < threshold


def promote_to_long(entry_id: str) -> dict | None:
    """Promote an entry to ``type="long"`` and reset its decay_score."""
    record = state.memory.get(entry_id)
    if record is None:
        return None
    record["type"] = "long"
    record["decay_score"] = 1.0
    record["updated_at"] = now_ts()
    return record


# ---------------------------------------------------------------------------
# Recall search (A1.2 §强制调用规则)
# ---------------------------------------------------------------------------


def _text_match_score(query: str, entry: dict) -> float:
    q = (query or "").lower().strip()
    if not q:
        return 0.0
    content = (entry.get("content") or "").lower()
    tags = " ".join(entry.get("tags") or []).lower()
    haystack = f"{content}\n{tags}"
    if q in haystack:
        return 1.0
    q_words = {w for w in q.split() if w}
    if not q_words:
        return 0.0
    c_words = set(haystack.split())
    overlap = len(q_words & c_words)
    if not overlap:
        return 0.0
    return min(1.0, overlap / len(q_words))


def _recency_bonus(entry: dict, *, now: int | None = None) -> float:
    """Light recency weighting so very recent entries rank slightly higher.

    The bonus is at most +0.1 and decays linearly over 30 days.  Long
    memory doesn't get it (it never decays anyway).
    """
    if (entry.get("type") or "short") == "long":
        return 0.0
    if now is None:
        now = now_ts()
    ts = entry.get("created_at") or now
    age_days = max(0.0, (now - ts) / 86400.0)
    if age_days >= 30:
        return 0.0
    return 0.1 * (1.0 - age_days / 30.0)


def recall_search(
    *,
    character_id: str | None,
    query: str,
    top_k: int = 5,
    now: int | None = None,
    decay_rate: float = DEFAULT_SHORT_TERM_DECAY_RATE,
    bump_access: bool = True,
) -> list[dict]:
    """Search memory for entries relevant to ``query``.

    Returns ``[{"entry": <record>, "score": <float>}, ...]`` sorted by
    score descending.  The score is computed as:

        text_match * importance * live_decay_score + recency_bonus

    where ``live_decay_score`` is :func:`compute_decay_score` evaluated
    at ``now`` (i.e. transient — we don't mutate the stored score here).

    Side effect: when ``bump_access`` is True (default), the top
    ``top_k`` hits get their ``access_count`` incremented and
    ``last_access_at`` updated to ``now``.  This makes the recall
    behaviour visible to other readers and feeds the promotion
    heuristic.
    """
    if now is None:
        now = now_ts()
    items = list(state.memory.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]

    hits: list[dict] = []
    for entry in items:
        text_score = _text_match_score(query, entry)
        if text_score <= 0.0:
            continue
        importance = float(entry.get("importance", 0.5) or 0.5)
        decay = compute_decay_score(entry, now=now, rate=decay_rate)
        recency = _recency_bonus(entry, now=now)
        score = text_score * importance * decay + recency
        hits.append({"entry": entry, "score": round(score, 4)})

    hits.sort(key=lambda h: h["score"], reverse=True)
    sliced = hits[: max(1, top_k)]

    if bump_access:
        for h in sliced:
            entry = h["entry"]
            entry["access_count"] = int(entry.get("access_count", 0) or 0) + 1
            entry["last_access_at"] = now

    return sliced


# ---------------------------------------------------------------------------
# Async ops
# ---------------------------------------------------------------------------


_consolidate_jobs: dict[str, dict] = {}


def schedule_consolidate(job_id: str, character_id: str | None = None) -> None:
    def _run():
        time.sleep(0.05)
        _consolidate_jobs[job_id] = {
            "job_id": job_id,
            "status": "completed",
            "character_id": character_id,
            "finished_at": now_ts(),
        }
    threading.Thread(target=_run, daemon=True).start()


def consolidate_status(job_id: str) -> dict | None:
    return _consolidate_jobs.get(job_id)


def forget(*, entry_ids: list[str] | None = None, decay: str | None = None) -> dict:
    """Forget entries by id or by decay class."""
    removed = 0
    if entry_ids:
        for entry_id in entry_ids:
            if delete(entry_id):
                removed += 1
        return {"forgotten": removed, "by": "ids"}
    if decay:
        for key in list(state.memory.keys()):
            entry = state.memory[key]
            entry_decay = (entry.get("attributes") or {}).get("decay", "normal")
            if entry_decay == decay:
                delete(key)
                removed += 1
        return {"forgotten": removed, "by": "decay", "decay": decay}
    return {"forgotten": 0, "by": "noop"}


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_default(character_id: str | None = None) -> None:
    """Populate the store with three demo entries.

    The entries cover the three scenarios a typical test / smoke check
    exercises:

    1. **Long-term identity** — Yuki's name and role (type=long,
       importance 0.9, decay_score pinned at 1.0).
    2. **Short-term preference** — user's preference for cats (type=short,
       importance 0.7).
    3. **Short-term fact** — recent mention of "冰淇淋" so the keyword
       search test (``POST /v1/xijian/memory/search`` with query="冰淇淋")
       has something to find.

    Calling :func:`seed_default` is idempotent — it only seeds when the
    store is empty.
    """
    if state.memory:
        return
    target_char = character_id or _DEFAULT_CHARACTER
    now = now_ts()
    seeds = [
        {
            "character_id": target_char,
            "type": "long",
            "content": "Yuki 是主人的 AI 助手，性格温和、细心，喜欢猫和安静的氛围。",
            "importance": 0.9,
            "decay_score": 1.0,
            "source": "manual",
            "tags": ["identity", "persona"],
        },
        {
            "character_id": target_char,
            "type": "short",
            "content": "用户最近提到他喜欢草莓口味的冰淇淋。",
            "importance": 0.7,
            "decay_score": 0.95,
            "source": "dialogue",
            "tags": ["preference", "food"],
        },
        {
            "character_id": target_char,
            "type": "short",
            "content": "用户昨晚说有点累，今天起得比平时晚。",
            "importance": 0.4,
            "decay_score": 0.6,
            "source": "dialogue",
            "tags": ["status"],
        },
    ]
    for seed in seeds:
        record = _new_entry(seed)
        # Use deterministic timestamps so tests can reason about decay.
        record["created_at"] = now
        record["updated_at"] = now
        record["last_access_at"] = now
        state.memory[record["id"]] = record


# ---------------------------------------------------------------------------
# load_context — A1.2 §技术视角 → 自动记忆载入
# ---------------------------------------------------------------------------
#
# Mermaid step 1 from the spec:
#
#   S->>M: loadContext(character_id, budget_tokens)
#   M->>M: 1) 读取 character_memory_config
#   M->>M: 2) 选取长期记忆（按 importance + tags + recency 排序，取 top N）
#   M->>M: 3) 选取短期记忆（按 decay_score ≥ 阈值，取 top K）
#   M->>M: 4) 拼接为 system prompt + history
#   M-->>S: 返回 context 包
#
# Edge case from the spec:
#
#   上下文窗口剩余空间 < 配置的 10% → 触发"按重要性裁剪"而非全量注入。
#
# Implementation contract:
#
# * Pure function — no side effects on the memory store beyond bumping
#   ``access_count`` / ``last_access_at`` for the entries that actually
#   make it into the context.  That mirrors the recall-pipeline's
#   "successful read counts as access" behaviour so the promotion
#   heuristic stays accurate.
# * The returned ``system_message`` is a Markdown block designed to be
#   prepended to a chat as a ``system``-role message.
# * The returned envelope includes diagnostics (counts, ids, tokens,
#   ``trimmed`` flag) so callers can log / assert without re-counting.


from xijian_api.stubs import memory_config as memory_config_stub  # noqa: E402


#: Rough heuristic for token estimation when we don't have a real
#: tokenizer on hand.  Most chat models hover around 3–4 chars per
#: token for CJK text; 4 is a safe upper bound.  We add 8 tokens per
#: entry for the bullet / header overhead.
_CHARS_PER_TOKEN = 4
_TOKENS_PER_ENTRY_OVERHEAD = 8


def _estimate_tokens(text: str) -> int:
    """Best-effort token estimate for ``text``.

    Used by :func:`load_context` to decide whether the assembled context
    fits in the budget without invoking a real tokenizer.  The estimate
    is intentionally conservative (rounded up) so we don't blow the
    budget when an exact counter would have allowed one more entry.
    """
    if not text:
        return 0
    return max(1, -(-len(text) // _CHARS_PER_TOKEN))


def _format_long_term_block(entries: list[dict]) -> str:
    """Render the long-term-memory Markdown block."""
    if not entries:
        return ""
    lines = [f"## 长期记忆（共 {len(entries)} 条）"]
    for entry in entries:
        importance = float(entry.get("importance", 0.0) or 0.0)
        lines.append(
            f"- (importance={importance:.2f}) {entry.get('content', '')}"
        )
    return "\n".join(lines)


def _format_short_term_block(entries: list[dict]) -> str:
    """Render the short-term-memory Markdown block with live decay scores."""
    if not entries:
        return ""
    lines = [f"## 短期记忆（共 {len(entries)} 条）"]
    now = now_ts()
    for entry in entries:
        importance = float(entry.get("importance", 0.0) or 0.0)
        decay = compute_decay_score(entry, now=now)
        lines.append(
            f"- (importance={importance:.2f}, decay={decay:.2f}) "
            f"{entry.get('content', '')}"
        )
    return "\n".join(lines)


def load_context(
    character_id: str | None,
    *,
    budget_tokens: int | None = None,
    now: int | None = None,
    bump_access: bool = True,
) -> dict[str, Any]:
    """Assemble the per-character memory context for a new dialogue.

    Parameters
    ----------
    character_id:
        The character to load context for.  When ``None`` the function
        returns an empty envelope — useful for the "no character set"
        case so callers don't have to special-case the path.
    budget_tokens:
        Token budget for the assembled context.  When ``None`` we
        derive it from the character's :class:`character_memory_config`
        as ``max_context_tokens - reserve_tokens_for_reply``.  Pass an
        explicit value to override (e.g. for tests or when the caller
        knows the model window is smaller).
    now:
        Override the clock (epoch ms) used for decay computation.  Tests
        use this to make decay deterministic.
    bump_access:
        When ``True`` (default), entries that survive the trim step
        have their ``access_count`` incremented and ``last_access_at``
        updated.  Disable for read-only introspection.

    Returns
    -------
    dict
        Envelope with the following keys:

        ``system_message``
            Markdown block ready to inject as a ``system``-role chat
            message.  Empty when no entries are selected.
        ``long_term_count`` / ``short_term_count``
            Number of entries that made it through trim.
        ``long_term_ids`` / ``short_term_ids``
            Entry ids, in the same order as the rendered Markdown.
        ``estimated_tokens``
            Token estimate for the assembled system_message.
        ``budget_tokens``
            Effective budget used (the resolved value, not the
            caller-supplied override if any).
        ``trimmed``
            ``True`` when the importance-based trim step kicked in.
        ``used_config``
            The :class:`character_memory_config` snapshot that drove
            the assembly — handy for debugging and assertions.
        ``empty``
            ``True`` when no character_id was supplied or no entries
            matched the configured filters.
    """
    if character_id is None:
        return {
            "system_message": "",
            "long_term_count": 0,
            "short_term_count": 0,
            "long_term_ids": [],
            "short_term_ids": [],
            "estimated_tokens": 0,
            "budget_tokens": int(budget_tokens or 0),
            "trimmed": False,
            "used_config": {},
            "empty": True,
        }

    cfg = memory_config_stub.get(character_id)
    # ``reserve_tokens_for_reply`` is *already excluded* from the budget:
    # the chat pipeline guarantees room for the model's response.
    resolved_budget = int(
        budget_tokens
        if budget_tokens is not None
        else max(0, cfg["max_context_tokens"] - cfg["reserve_tokens_for_reply"])
    )

    # ---- 1. Long-term selection ------------------------------------------
    long_pool = [
        entry
        for entry in state.memory.values()
        if (entry.get("character_id") == character_id
            and (entry.get("type") or "short") == "long"
            and float(entry.get("importance", 0.0) or 0.0)
            >= float(cfg["long_term_importance_min"]))
    ]
    long_pool.sort(
        key=lambda e: (
            float(e.get("importance", 0.0) or 0.0),
            int(e.get("created_at") or 0),
        ),
        reverse=True,
    )
    long_top = long_pool[: int(cfg["max_long_term"])]

    # ---- 2. Short-term selection -----------------------------------------
    short_pool: list[tuple[dict, float]] = []
    threshold = float(cfg["short_term_importance_min"])
    decay_rate = float(cfg["short_term_decay_rate"])
    for entry in state.memory.values():
        if entry.get("character_id") != character_id:
            continue
        if (entry.get("type") or "short") != "short":
            continue
        importance = float(entry.get("importance", 0.0) or 0.0)
        if importance < threshold:
            continue
        live_decay = compute_decay_score(entry, now=now, rate=decay_rate)
        if live_decay < threshold:
            continue
        short_pool.append((entry, live_decay))
    # Score = decay × importance.  Tie-break by created_at so the
    # selection is stable across calls within the same instant.
    short_pool.sort(
        key=lambda pair: (
            pair[1] * float(pair[0].get("importance", 0.0) or 0.0),
            int(pair[0].get("created_at") or 0),
        ),
        reverse=True,
    )
    short_top = [pair[0] for pair in short_pool[: int(cfg["max_short_term"])]]
    short_decay = {pair[0]["id"]: pair[1] for pair in short_pool[: int(cfg["max_short_term"])]}

    # ---- 3. Assemble + estimate tokens ----------------------------------
    long_block = _format_long_term_block(long_top)
    short_block = _format_short_term_block(short_top)
    blocks = [b for b in (long_block, short_block) if b]
    system_message = "\n\n".join(blocks)
    estimated = (
        _estimate_tokens(system_message)
        + _TOKENS_PER_ENTRY_OVERHEAD * (len(long_top) + len(short_top))
    )

    # ---- 4. Importance-based trim if over budget ------------------------
    trimmed = False
    if estimated > resolved_budget:
        trimmed = True
        # Re-score every selected entry by importance (long) or
        # decay × importance (short).  Walk greedily from most
        # important down; stop when the next entry would push us
        # past the budget.  Long-term entries still get priority on
        # tie because of the tuple sort key.
        candidates: list[tuple[float, int, dict, str]] = []
        for entry in long_top:
            score = float(entry.get("importance", 0.0) or 0.0)
            candidates.append((score, 1, entry, "long"))
        for entry in short_top:
            score = short_decay[entry["id"]] * float(entry.get("importance", 0.0) or 0.0)
            candidates.append((score, 0, entry, "short"))
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)

        kept_long: list[dict] = []
        kept_short: list[dict] = []
        used = _TOKENS_PER_ENTRY_OVERHEAD  # budget for headers
        for score, _, entry, kind in candidates:
            cost = _estimate_tokens(entry.get("content", "")) + _TOKENS_PER_ENTRY_OVERHEAD
            if used + cost > resolved_budget:
                continue
            if kind == "long":
                kept_long.append(entry)
            else:
                kept_short.append(entry)
            used += cost
        long_top = kept_long
        short_top = kept_short

        long_block = _format_long_term_block(long_top)
        short_block = _format_short_term_block(short_top)
        blocks = [b for b in (long_block, short_block) if b]
        system_message = "\n\n".join(blocks)
        estimated = used

    # ---- 5. Bookkeeping -------------------------------------------------
    if bump_access:
        ts = now if now is not None else now_ts()
        for entry in long_top:
            entry["access_count"] = int(entry.get("access_count", 0) or 0) + 1
            entry["last_access_at"] = ts
        for entry in short_top:
            entry["access_count"] = int(entry.get("access_count", 0) or 0) + 1
            entry["last_access_at"] = ts

    return {
        "system_message": system_message,
        "long_term_count": len(long_top),
        "short_term_count": len(short_top),
        "long_term_ids": [e["id"] for e in long_top],
        "short_term_ids": [e["id"] for e in short_top],
        "estimated_tokens": estimated,
        "budget_tokens": resolved_budget,
        "trimmed": trimmed,
        "used_config": dict(cfg),
        "empty": not long_top and not short_top,
    }


__all__ = [
    "DEFAULT_SHORT_TERM_DECAY_RATE",
    "DEFAULT_SHORT_TERM_IMPORTANCE_MIN",
    "DEFAULT_LONG_TERM_IMPORTANCE_MIN",
    "compute_decay_score",
    "should_promote_to_long",
    "promote_to_long",
    "recall_search",
    "load_context",
    "seed_default",
    "create",
    "list_all",
    "get",
    "update",
    "delete",
    "search",
    "schedule_consolidate",
    "consolidate_status",
    "forget",
]
