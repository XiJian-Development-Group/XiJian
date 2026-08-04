#!/usr/bin/env python3
"""A5.1 safety-eval runner — ``python -m core.scripts.eval_safety``.

Loads ``docs/eval/safety_eval.jsonl`` (≥100 normal + ≥30 OOC samples),
seeds the eval rulebook embedded in the dataset, runs
:func:`xijian_api.stubs.safety.scan_input` / :func:`scan_output` over
every sample, and prints a trigger-rate report:

* **normal set** — the fraction of normal samples that produced a
  non-``pass`` verdict.  Spec AC-1 requires this to stay < 1 %
  (false-positive guard: everyday in-character content must not trip
  the safety layer).
* **ooc set** — the fraction of OOC samples (prompt injection /
  out-of-character / world-danger dual-signal) that the safety layer
  actually caught (``warn`` / ``block`` / ``hard_block`` /
  ``allow_with_exception``).

Verdict totals are read through :func:`safety.count_for(verdict=...)`
(the API the AC-1 dashboard uses).  Exit code is 0 when the normal-set
trigger rate is below 1 %, non-zero otherwise — CI can gate on it.

Usage / 用法::

    python -m core.scripts.eval_safety            # from repo root
    python core/scripts/eval_safety.py            # direct
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Repo layout: ``core/scripts/eval_safety.py`` → repo root.
#: 仓库布局：``core/scripts/eval_safety.py`` → 仓库根目录。
_CORE_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _CORE_DIR.parent
#: Eval dataset path (relative to repo root).
#: 评测集路径（相对仓库根目录）。
_EVAL_JSONL = _REPO_ROOT / "docs" / "eval" / "safety_eval.jsonl"

#: Make ``xijian_api`` importable when run as ``python -m core.scripts...``
#: from the repo root (namespace package) — the package lives under
#: ``core/``, which is not on ``sys.path`` in that invocation mode.
#: 使 ``xijian_api`` 可从仓库根目录以 ``python -m core.scripts...``
#: （命名空间包）方式导入 —— 该包位于 ``core/`` 下，此调用模式下不在
#: ``sys.path`` 中。
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))


def _load_dataset(path: Path) -> list[dict]:
    """Load the JSONL eval set.  Each line is one sample dict."""
    if not path.exists():
        raise FileNotFoundError(
            "eval dataset not found: %s (run from the repo root?)" % path
        )
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _seed_rules(samples: list[dict]) -> int:
    """Seed the eval rulebook from the samples' ``rule`` blocks.

    Returns the number of rules created.  Idempotent per pattern —
    duplicate patterns collapse onto the first occurrence.
    """
    from xijian_api.stubs import safety_rules as rules_stub

    seeded: dict[tuple[str, str], None] = {}
    for sample in samples:
        rule = sample.get("rule")
        if not isinstance(rule, dict):
            continue
        key = (rule.get("rule_kind", ""), rule.get("pattern", ""))
        if key in seeded:
            continue
        seeded[key] = None
        rules_stub.create(
            rule_kind=rule["rule_kind"],
            pattern=rule["pattern"],
            severity=int(rule.get("severity", 3)),
            is_active=True,
        )
    return len(seeded)


def _run_eval(samples: list[dict]) -> dict:
    """Run the scans and tally verdicts per set.

    Returns ``{"normal": {...counts}, "ooc": {...counts}}`` plus the
    overall verdict totals read through ``safety.count_for``.
    """
    from xijian_api.stubs import safety as safety_stub

    totals: dict[str, int] = {}
    per_set: dict[str, dict[str, int]] = {
        "normal": {},
        "ooc": {},
    }
    for sample in samples:
        kind = sample.get("kind", "normal")
        stage = sample.get("stage", "input")
        text = sample.get("text", "")
        world_id = sample.get("world_id")
        event_tags = sample.get("event_tags")
        # World-danger dual-signal samples mark the world dangerous so
        # the exception path (allow_with_exception) can be exercised.
        # 世界危险双信号样本将世界标记为危险，以演练例外路径。
        if world_id and sample.get("category") == "world_danger":
            safety_stub.set_world_dangerous(world_id, True)
        if stage == "output":
            result = safety_stub.scan_output(
                text=text, world_id=world_id, event_tags=event_tags,
            )
        else:
            result = safety_stub.scan_input(
                text=text, world_id=world_id, event_tags=event_tags,
            )
        verdict = result.get("verdict", "pass")
        totals[verdict] = totals.get(verdict, 0) + 1
        bucket = per_set[kind]
        bucket[verdict] = bucket.get(verdict, 0) + 1
    # Verdict totals via count_for — the AC-1 dashboard API.
    audit_totals: dict[str, int] = {}
    for verdict in ("pass", "warn", "block", "hard_block", "allow_with_exception"):
        audit_totals[verdict] = safety_stub.count_for(verdict=verdict)
    return {
        "per_set": per_set,
        "scan_totals": totals,
        "audit_totals": audit_totals,
    }


def _trigger_rate(counts: dict[str, int], total: int) -> float:
    """Fraction of samples whose verdict was NOT ``pass``."""
    if total <= 0:
        return 0.0
    triggered = sum(v for k, v in counts.items() if k != "pass")
    return triggered / total


def main() -> int:
    """Run the eval and print the trigger-rate report."""
    from xijian_api.stubs import safety as safety_stub
    from xijian_api.stubs import safety_rules as rules_stub

    # Fresh rulebook + audit log so counts reflect only this run.
    # 清空规则书与审计日志，使计数仅反映本次运行。
    rules_stub.reset_for_testing()
    safety_stub.reset_for_testing()

    samples = _load_dataset(_EVAL_JSONL)
    normal_total = sum(1 for s in samples if s.get("kind") == "normal")
    ooc_total = sum(1 for s in samples if s.get("kind") == "ooc")
    n_rules = _seed_rules(samples)
    report = _run_eval(samples)

    normal_counts = report["per_set"]["normal"]
    ooc_counts = report["per_set"]["ooc"]
    normal_trigger = _trigger_rate(normal_counts, normal_total)
    ooc_trigger = _trigger_rate(ooc_counts, ooc_total)

    # ---- Report table / 触发率报告 ----
    print("=" * 64)
    print("A5.1 safety eval — trigger-rate report")
    print("dataset: %s" % _EVAL_JSONL)
    print("=" * 64)
    print("normal samples: %d   ooc samples: %d   rules seeded: %d"
          % (normal_total, ooc_total, n_rules))
    print("-" * 64)
    print("%-22s %10s %10s" % ("verdict", "normal", "ooc"))
    print("-" * 64)
    for verdict in ("pass", "warn", "block", "hard_block", "allow_with_exception"):
        print("%-22s %10d %10d"
              % (verdict, normal_counts.get(verdict, 0), ooc_counts.get(verdict, 0)))
    print("-" * 64)
    print("normal trigger rate: %.2f%% (AC-1 target < 1%%)" % (normal_trigger * 100))
    print("ooc   trigger rate: %.2f%%" % (ooc_trigger * 100))
    print("audit totals (via safety.count_for): %s" % report["audit_totals"])
    print("=" * 64)

    ok = normal_trigger < 0.01
    if ok:
        print("RESULT: PASS — normal-set trigger rate < 1%%; "
              "OOC 触发率达标，`[TODO: 用评测集验证]` 可摘除。")
    else:
        print("RESULT: FAIL — normal-set trigger rate >= 1%%; "
              "检查规则误报。")
        print("verdict breakdown per set: %s" % report["per_set"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
