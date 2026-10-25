"""HW3 Part C: pick 50 monitoring scenarios (seeded) for the Homework 7 comparison.

All 15 human-reviewed scenarios are included. Coverage is filled to 35 in proportion to
intent counts; challenge is filled to 15 with one scenario per damaged-record case plus
boundary cases.
"""
import json, random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
rng = random.Random(20261014)
rows = [json.loads(l) for l in (ROOT / "scenarios/support_scenarios.jsonl").open()]
by_id = {r["id"]: r for r in rows}
reviewed = [json.loads(l)["scenario_id"] for l in (ROOT / "scenarios/support_review.jsonl").open()]
chosen = list(reviewed)

# challenge: one per damaged-record case, then boundary cases not yet represented
chal_extra = ["final-0176", "final-0186", "final-0196", "final-0201",  # missing date, store mismatch, bad price, missing title
              "final-0206", "final-0224", "final-0227", "final-0241", "final-0246"]  # shopper out-of-scope, day 30, $100, restocking, dispute >60d
chosen += chal_extra

# coverage: fill each intent to its proportional share of 35
cov = [r for r in rows if r["scenario_group"] == "coverage"]
share = Counter(r["tuple"]["intent"] for r in cov)
for intent, n in sorted(share.items()):
    target = round(35 * n / len(cov))
    have = sum(1 for i in chosen if by_id[i]["scenario_group"] == "coverage" and by_id[i]["tuple"]["intent"] == intent)
    pool = [r["id"] for r in cov if r["tuple"]["intent"] == intent and r["id"] not in chosen]
    chosen += rng.sample(pool, max(0, target - have))
while sum(by_id[i]["scenario_group"] == "coverage" for i in chosen) < 35:   # rounding top-up
    chosen.append(rng.choice([r["id"] for r in cov if r["id"] not in chosen]))
while sum(by_id[i]["scenario_group"] == "coverage" for i in chosen) > 35:
    chosen.remove(next(i for i in reversed(chosen) if by_id[i]["scenario_group"] == "coverage" and i not in reviewed))

assert len(chosen) == len(set(chosen)) == 50, len(chosen)
out = [by_id[i] for i in sorted(chosen)]
(ROOT / "scenarios/monitoring_scenarios.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
print(Counter(r["scenario_group"] for r in out), Counter(r["tuple"]["role"] for r in out))
print(Counter(r["tuple"]["intent"] for r in out))
print(Counter(r["tuple"]["difficulty"] for r in out), Counter(r["data_quality_case_id"] for r in out if r["data_quality_case_id"]))
