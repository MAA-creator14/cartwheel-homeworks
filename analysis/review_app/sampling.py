"""Part B batch selection for the review app.

Writes batches to ``analysis/state/sample_manifest.json``. Each call adds one
batch and never picks a trace that is already in an earlier batch. Selection
uses only trace structure (role, turn, tool activity, lengths), never the
outcome of a review or a model's failure prediction.

    uv run --no-sync python -m analysis.review_app.sampling batch1
    uv run --no-sync python -m analysis.review_app.sampling batch2
    uv run --no-sync python -m analysis.review_app.sampling batch3
    uv run --no-sync python -m analysis.review_app.sampling batch4
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import date, datetime, timezone
from pathlib import Path

from analysis.helpers.selection import _kmeans, _standardize

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "analysis" / "review_app" / ".cache" / "sessions.json"
MANIFEST = ROOT / "analysis" / "state" / "sample_manifest.json"
ANNOTATIONS = ROOT / "analysis" / "state" / "annotations.json"

# Sessions read in standard Langfuse during Part A1 (interface design only).
PART_A_SCENARIOS = {
    "final-0039", "final-0063", "final-0050", "final-0212",
    "final-0155", "final-0085", "final-0151", "final-0114",
}
ROLES = ("shopper", "merchant", "support")


def load_traces() -> list[dict]:
    sessions = json.loads(CACHE.read_text(encoding="utf-8"))["sessions"]
    out = []
    for s in sessions:
        if s["scenario_id"] in PART_A_SCENARIOS:
            continue
        for i, t in enumerate(s["turns"], start=1):
            steps = t.get("steps") or []
            out.append({
                "trace_id": t["trace_id"],
                "scenario_id": s["scenario_id"],
                "turn": i,
                "role": s.get("role") or "unknown",
                "n_turns": len(s["turns"]),
                "n_tools": len(steps),
                "distinct_tools": len({x["name"] for x in steps}),
                "any_not_ok": int(any(x.get("ok") is False for x in steps)),
                "user_len": len(t.get("user") or ""),
                "reply_len": len(t.get("reply") or ""),
                "_turn": t,
            })
    return out


def features(t: dict) -> list[float]:
    role = [float(t["role"] == r) for r in ROLES]
    return role + [float(t[k]) for k in
                   ("turn", "n_turns", "n_tools", "distinct_tools",
                    "any_not_ok", "user_len", "reply_len")]


def read_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"batches": []}


def used_ids(manifest: dict) -> set[str]:
    return {t["trace_id"] for b in manifest["batches"] for t in b["traces"]}


def annotated_ids() -> set[str]:
    """Traces already read (e.g. other turns judged while reviewing a session)."""
    if not ANNOTATIONS.exists():
        return set()
    return {a["trace_id"] for a in json.loads(ANNOTATIONS.read_text(encoding="utf-8"))["annotations"]}


def entry(t: dict, reason: str) -> dict:
    return {"trace_id": t["trace_id"], "scenario_id": t["scenario_id"],
            "turn": t["turn"], "role": t["role"], "reason": reason}


def batch1(pool: list[dict], seed: int, n_uniform: int = 15, n_clusters: int = 15) -> list[dict]:
    rng = random.Random(seed)
    uniform = rng.sample(pool, n_uniform)
    picks = [entry(t, "uniform random") for t in uniform]
    rest = [t for t in pool if t not in uniform]

    vectors = _standardize([features(t) for t in rest])
    assign = _kmeans(vectors, k=n_clusters, seed=seed)
    for c in sorted(set(assign)):
        idx = [i for i, a in enumerate(assign) if a == c]
        dims = len(vectors[0])
        centroid = [sum(vectors[i][d] for i in idx) / len(idx) for d in range(dims)]
        best = min(idx, key=lambda i: sum((vectors[i][d] - centroid[d]) ** 2 for d in range(dims)))
        picks.append(entry(rest[best], f"cluster {c} representative ({len(idx)} traces)"))
    return picks


def batch2(pool: list[dict], seed: int, per_role: int = 10) -> list[dict]:
    """Product dimension chosen before outcomes: user role, equal picks per role."""
    rng = random.Random(seed)
    picks = []
    for role in ROLES:
        members = [t for t in pool if t["role"] == role]
        picks += [entry(t, f"role = {role}") for t in rng.sample(members, per_role)]
    return picks


WORLD_ASOF = date(2026, 7, 1)  # seed/generate.py WORLD_ASOF
DEFECT_ORDERS = {8001, 8002, 8003}  # seed/generate.py _seed_data_quality_cases
DEFECT_PRODUCTS = {1, 2, 3, 4}
POLICY_NUMBER = re.compile(r"\b(14|30|45|60)[ -]days?\b|\$100\b|\b15 ?(%|percent)", re.I)


def _steps(t: dict) -> list[dict]:
    return t["_turn"].get("steps") or []


def _orders(t: dict) -> list[dict]:
    out = []
    for s in _steps(t):
        r = s.get("result") or {}
        out += [r["order"]] if isinstance(r.get("order"), dict) else []
        out += [o for o in r.get("orders") or [] if isinstance(o, dict)]
    return out


def _age_days(o: dict) -> int | None:
    d = o.get("delivered_at")
    return (WORLD_ASOF - date.fromisoformat(d)).days if d else None


def _escalated(t: dict) -> bool:
    return any(s["name"] == "escalate_to_human" for s in _steps(t))


def _queued_refund(t: dict) -> bool:
    return any(s["name"] == "issue_refund" and (s.get("result") or {}).get("status") == "queued_for_approval"
               for s in _steps(t))


def _policy_number(t: dict) -> bool:
    return bool(POLICY_NUMBER.search(t["_turn"].get("reply") or ""))


def _defect_record(t: dict) -> bool:
    ids = {o.get("order_id") for o in _orders(t)}
    products = {p.get("product_id") for s in _steps(t) for p in (s.get("result") or {}).get("products") or []}
    return bool(ids & DEFECT_ORDERS or products & DEFECT_PRODUCTS)


def _several_matches(t: dict) -> bool:
    return any(len((s.get("result") or {}).get("orders") or []) >= 2 for s in _steps(t))


def _route_edge(t: dict) -> bool:
    if not re.search(r"refund|money back|dispute|return", t["_turn"].get("user") or "", re.I):
        return False
    for o in _orders(t):
        age = _age_days(o)
        if age is not None and (25 <= age <= 35 or 40 <= age <= 50 or 55 <= age <= 65):
            return True
        if 80 <= float(o.get("total_usd") or 0) <= 120:
            return True
    return False


def _not_delivered(t: dict) -> bool:
    return any(o.get("status") in ("cancelled", "placed", "shipped") for o in _orders(t))


# Each depth search filters on trace inputs and tool results, never on whether
# the reply fails. Matches include likely positives and close negatives.
DEPTH_SEARCHES = [
    ("unexplained_escalation", "turn calls escalate_to_human", [_escalated], 5),
    ("missing_policy_citation", "reply uses a policy number (14/30/45/60 days, $100, 15%)", [_policy_number], 4),
    ("unbacked_promise", "issue_refund returned queued_for_approval, else escalation", [_queued_refund, _escalated], 4),
    ("unflagged_missing_or_inconsistent_data", "seeded data-defect order/product, else find_order with several matches",
     [_defect_record, _several_matches], 4),
    ("wrong_resolution_route", "refund/dispute request near a window edge or the $100 threshold", [_route_edge], 4),
    ("irrelevant_or_contradictory_statement", "order is cancelled, placed, or shipped (not delivered)", [_not_delivered], 4),
]


def batch3(pool: list[dict], seed: int) -> list[dict]:
    """Depth searches for candidate modes and close negatives, one search per mode."""
    rng = random.Random(seed)
    picks, taken = [], set()
    for mode, desc, filters, n in DEPTH_SEARCHES:
        got = []
        for f in filters:  # earlier filters first, later ones only fill the gap
            members = [t for t in pool if t["trace_id"] not in taken and f(t)]
            chosen = rng.sample(members, min(n - len(got), len(members)))
            got += chosen
            taken |= {t["trace_id"] for t in chosen}
            if len(got) == n:
                break
        picks += [entry(t, f"depth search {mode}: {desc} ({f.__name__.strip('_')})") for t in got]
    return picks


def batch4(pool: list[dict], seed: int, n: int = 15) -> list[dict]:
    """Saturation check after the draft taxonomy: uniform sample of the remaining pool."""
    rng = random.Random(seed)
    return [entry(t, "uniform random (saturation check)") for t in rng.sample(pool, n)]


BATCHES = {
    "batch1": (batch1, "15 uniform random traces + 1 centroid trace from each of 15 k-means "
                       "clusters over role, turn, tool counts, ok:false, and text lengths"),
    "batch2": (batch2, "product dimension user role, chosen before any outcomes: "
                       "10 uniform random traces each for shopper, merchant, support"),
    "batch3": (batch3, "25 depth-search traces, one search per candidate mode after axial round 2, "
                       "filtering on tool results and inputs (not on reply outcome) to find positives "
                       "and close negatives"),
    "batch4": (batch4, "15 uniform random traces after the draft taxonomy, to check whether new "
                       "modes still appear"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", choices=sorted(BATCHES))
    ap.add_argument("--seed", type=int, default=20261021)
    args = ap.parse_args()

    manifest = read_manifest()
    if any(b["name"] == args.batch for b in manifest["batches"]):
        raise SystemExit(f"{args.batch} already exists in {MANIFEST}")
    skip = used_ids(manifest) | annotated_ids()
    pool = [t for t in load_traces() if t["trace_id"] not in skip]
    select, method = BATCHES[args.batch]
    traces = select(pool, args.seed)
    manifest["batches"].append({
        "name": args.batch,
        "method": method,
        "seed": args.seed,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "traces": traces,
    })
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{args.batch}: {len(traces)} traces from a pool of {len(pool)}")
    for t in traces:
        print(f"  {t['scenario_id']} T{t['turn']} {t['role']:<13} {t['reason']}")


if __name__ == "__main__":
    main()
