"""Monitor one failure mode with the frozen Homework 5 judge.

Two ways to choose the traces:

    uv run python -m monitoring.run --period before          # a period in config.json
    uv run python -m monitoring.run --last-hours 24          # the scheduled job

A period must contain every scenario ID in ``scenarios/monitoring_scenarios.jsonl``,
one conversation record per scenario. A ``--last-hours`` window groups traces by
``meta.session_id`` instead. In both modes every eligible trace must use the
configured Cartwheel model.

Without ``--yes`` the command stops after printing the sample plan and the number
of judge calls, so no paid call happens by accident. With ``--yes`` it runs the
frozen judge once on the union of the random sample and the risk groups, then
corrects the random-sample rate, writes Langfuse scores, and saves the history
line and chart.

Local output (sample plan, verdicts, estimate) goes to ``monitoring/output/``,
which is gitignored and uploaded as a workflow artifact by the scheduled job.
Verdicts are cached there per batch, so a rerun reuses them unless ``--rejudge``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "monitoring" / "config.json"
SCENARIOS_PATH = ROOT / "scenarios" / "monitoring_scenarios.jsonl"
HISTORY_PATH = ROOT / "monitoring" / "history.jsonl"
CHART_PATH = ROOT / "monitoring" / "prevalence.svg"
OUTPUT_DIR = ROOT / "monitoring" / "output"


# ---------------------------------------------------------------------------
# config and time window
# ---------------------------------------------------------------------------


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"time {value!r} needs a UTC offset")
    return parsed.astimezone(timezone.utc)


def period_window(config: dict[str, Any], label: str) -> tuple[datetime, datetime]:
    for period in config["periods"]:
        if period["label"] == label:
            start, end = _parse_utc(period["from"]), _parse_utc(period["to"])
            if start >= end:
                raise ValueError(f"period {label!r} ends before it starts")
            return start, end
    raise ValueError(f"no period {label!r} in {CONFIG_PATH.name}")


# ---------------------------------------------------------------------------
# Langfuse fetch
# ---------------------------------------------------------------------------


def fetch_window(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Full JSON trace records whose timestamp falls in [start, end]."""
    from analysis.helpers import langfuse_io
    from scenarios.export_langfuse import _json_default, _jsonable

    if not langfuse_io.is_configured():
        raise langfuse_io.LangfuseNotConfigured(
            "set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_HOST"
        )
    from langfuse import Langfuse

    client = Langfuse()
    summaries: list[Any] = []
    page = 1
    while True:
        response = client.api.trace.list(
            page=page, limit=100, from_timestamp=start, to_timestamp=end
        )
        batch = list(response.data or [])
        summaries.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    records: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        if summary.id not in records:
            # Same serialization as the HW3 export the HW5 judge was validated
            # on (sort_keys=True), so tool results keep the same key order.
            record = _jsonable(client.api.trace.get(summary.id))
            records[summary.id] = json.loads(
                json.dumps(record, sort_keys=True, default=_json_default)
            )
    return list(records.values())


# ---------------------------------------------------------------------------
# conversation records
# ---------------------------------------------------------------------------


def _bare_model(model: str) -> str:
    """``anthropic/claude-opus-4-6`` -> ``claude-opus-4-6`` (LiteLLM prefix)."""
    return model.split("/", 1)[1] if "/" in model else model


def trace_models(trace: dict[str, Any]) -> set[str]:
    return {
        _bare_model(str(obs["model"]))
        for obs in trace.get("observations") or []
        if obs.get("type") == "GENERATION" and obs.get("model")
    }


def trace_tools(trace: dict[str, Any]) -> list[str]:
    tools = [
        obs for obs in trace.get("observations") or [] if obs.get("type") == "TOOL"
    ]
    return [str(obs["name"]) for obs in sorted(tools, key=lambda o: o.get("startTime") or "")]


def _trace_meta(trace: dict[str, Any]) -> dict[str, Any]:
    from analysis.helpers.normalization import normalize_trace
    from scenarios.export_langfuse import _scenario_id

    meta = dict(normalize_trace(trace)["meta"])
    meta.setdefault("scenario_id", _scenario_id(trace))
    return meta


def conversation_text(turns: list[dict[str, Any]]) -> str:
    """The Homework 5 judge input: ordered turns with tool calls and results.

    Uses the same per-turn builder and flattener as the HW5 inputs, so the
    frozen judge sees text in the format it was validated on. No labels,
    annotations, or scenario metadata enter the text.
    """
    from analysis.helpers.normalization import normalize_trace
    from analysis.run_judges import _turn_messages

    messages = [message for turn in turns for message in _turn_messages(turn)]
    return normalize_trace({"trace_id": turns[-1]["id"], "trace": messages})["text"]


def build_conversations(
    traces: list[dict[str, Any]],
    model: str,
    group_key: str,
    required_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Group eligible traces into one record per conversation.

    ``group_key`` is ``scenario_id`` for a comparison period and ``session_id``
    for a ``--last-hours`` window. A trace without that key is not eligible.
    With ``required_ids`` only those groups are eligible, and every one of them
    must be present. Any eligible trace on another model rejects the batch.

    Returns (records ordered by group id, number of eligible traces).
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        key = _trace_meta(trace).get(group_key)
        if not key or (required_ids is not None and key not in required_ids):
            continue
        groups[str(key)].append(trace)

    if required_ids is not None:
        missing = sorted(required_ids - set(groups))
        if missing:
            raise ValueError(
                f"period is missing {len(missing)} scenario ids, e.g. {missing[:5]}; "
                "choose one complete run, do not combine retries"
            )

    wrong = sorted(
        {
            f"{trace['id']}: {sorted(trace_models(trace)) or 'no model'}"
            for group in groups.values()
            for trace in group
            if trace_models(trace) != {model}
        }
    )
    if wrong:
        raise ValueError(f"traces not on {model}: {wrong[:5]}")

    records = []
    for key in sorted(groups):
        turns = sorted(groups[key], key=lambda t: t.get("timestamp") or "")
        tools = list(dict.fromkeys(name for turn in turns for name in trace_tools(turn)))
        records.append(
            {
                "id": str(turns[-1]["id"]),  # final trace receives the score
                group_key: key,
                "trace_ids": [str(turn["id"]) for turn in turns],
                "tools": tools,
                "turn_count": len(turns),
                "text": conversation_text(turns),
            }
        )
    return records, sum(len(group) for group in groups.values())


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def upsert_history(record: dict[str, Any], path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    """Replace the line with the same label, keeping the order of first write."""
    rows = []
    if path.exists():
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    for index, row in enumerate(rows):
        if row["label"] == record["label"]:
            rows[index] = record
            break
    else:
        rows.append(record)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


def write_chart(rows: list[dict[str, Any]], threshold: float, mode: str) -> None:
    from monitoring.chart import prevalence_chart

    points = [
        {
            "label": row["label"],
            "corrected": row["corrected_rate"],
            "ci_low": row["interval"][0],
            "ci_high": row["interval"][1],
        }
        for row in rows
    ]
    CHART_PATH.write_text(prevalence_chart(points, threshold=threshold, mode=mode), "utf-8")


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


def run(
    label: str,
    start: datetime,
    end: datetime,
    config: dict[str, Any],
    *,
    scheduled: bool,
    approve: bool,
    rejudge: bool,
) -> dict[str, Any]:
    from monitoring.sample import DEFAULT_RISK_GROUPS, select_traces

    judge_id, mode, model = config["judge_id"], config["judge_mode"], config["model"]
    unknown = [name for name in config["risk_groups"] if name not in DEFAULT_RISK_GROUPS]
    if unknown:
        raise ValueError(f"unknown risk groups {unknown}")
    risk_groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    out_dir = OUTPUT_DIR / label

    required = None
    if not scheduled:
        required = {
            json.loads(line)["id"]
            for line in SCENARIOS_PATH.read_text("utf-8").splitlines()
            if line.strip()
        }
    traces = fetch_window(start, end)
    records, eligible_traces = build_conversations(
        traces, model, "session_id" if scheduled else "scenario_id", required
    )
    if required is not None and len(records) != len(required):
        raise ValueError(f"expected {len(required)} conversation records, got {len(records)}")

    summary: dict[str, Any] = {
        "label": label,
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": end.isoformat().replace("+00:00", "Z"),
        "judge_id": judge_id,
        "model": model,
        "langfuse_traces": eligible_traces,
        "conversations": len(records),
    }
    if not records:
        summary.update({"random_sample": 0, "risk_sample": 0, "judge_calls": 0})
        _write_json(out_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2))
        print("No eligible conversations in this window; the judge was not called.")
        return summary

    plan = select_traces(records, config["random_rate"], risk_groups)
    risk_ids = list(
        dict.fromkeys(t["id"] for group in plan["risk_groups"].values() for t in group)
    )
    summary.update(
        {
            "random_sample": len(plan["random"]),
            "risk_sample": len(risk_ids),
            "risk_group_sizes": {k: len(v) for k, v in plan["risk_groups"].items()},
            "judge_calls": len(plan["to_judge"]),
        }
    )
    _write_json(
        out_dir / "plan.json",
        {
            **summary,
            "random": [t["id"] for t in plan["random"]],
            "risk_groups": {k: [t["id"] for t in v] for k, v in plan["risk_groups"].items()},
            "records": [{k: v for k, v in r.items() if k != "text"} for r in records],
        },
    )
    print(
        f"[{label}] {eligible_traces} Langfuse traces -> {len(records)} conversations; "
        f"random {len(plan['random'])}, risk {len(risk_ids)} "
        f"({summary['risk_group_sizes']}); judge calls {len(plan['to_judge'])} "
        f"on {judge_id}"
    )

    # Judge the union once; cached verdicts make a rerun free.
    from monitoring.run_judges import judge_sample, load_monitoring_judge

    judge = load_monitoring_judge(judge_id)
    if judge["mode"] != mode:
        raise ValueError(f"{judge_id} judges {judge['mode']}, config says {mode}")
    cache_path = out_dir / "verdicts.json"
    to_judge_ids = [t["id"] for t in plan["to_judge"]]
    cached = json.loads(cache_path.read_text("utf-8")) if cache_path.exists() else {}
    if (
        not rejudge
        and cached.get("judge_id") == judge_id
        and cached.get("prompt_hash") == judge["prompt_hash"]
        and set(to_judge_ids) <= set(cached.get("verdicts", {}))
    ):
        verdicts = {tid: int(cached["verdicts"][tid]) for tid in to_judge_ids}
        print(f"Reusing {len(verdicts)} cached verdicts from {cache_path.relative_to(ROOT)}")
    else:
        if not approve:
            print("Stopped before the judge. Rerun with --yes to make these judge calls.")
            return summary
        verdicts = judge_sample(judge_id, plan["to_judge"])
        _write_json(
            cache_path,
            {"judge_id": judge_id, "prompt_hash": judge["prompt_hash"], "verdicts": verdicts},
        )

    random_verdicts = {t["id"]: verdicts[t["id"]] for t in plan["random"]}
    risk_verdicts = {tid: verdicts[tid] for tid in risk_ids}
    _write_json(
        out_dir / "verdicts_split.json",
        {
            "random": random_verdicts,
            "risk": risk_verdicts,
            "risk_groups": {
                name: {t["id"]: verdicts[t["id"]] for t in group}
                for name, group in plan["risk_groups"].items()
            },
        },
    )

    # Part C: correct the random-sample rate, then write scores and history.
    from monitoring.correct import corrected_mode_prevalence
    from monitoring.run_judges import judge_test_data
    from monitoring.write_scores import build_score_records, post_scores

    test_labels, test_preds = judge_test_data(judge_id)
    estimate = corrected_mode_prevalence(list(random_verdicts.values()), test_labels, test_preds)
    _write_json(out_dir / "estimate.json", estimate)
    scores = build_score_records(mode, random_verdicts, risk_verdicts, estimate, label)
    written = post_scores(scores)

    history = {
        **{k: summary[k] for k in ("label", "from", "to", "judge_id", "model")},
        "langfuse_traces": eligible_traces,
        "conversations": len(records),
        "random_sample": len(plan["random"]),
        "risk_sample": len(risk_ids),
        "risk_flagged": sum(risk_verdicts.values()),
        "raw_rate": estimate["raw"],
        "corrected_rate": estimate["corrected"],
        "interval": [estimate["ci_low"], estimate["ci_high"]],
        "confidence": estimate["confidence"],
        "failure_sensitivity": estimate["failure_sensitivity"],
        "pass_specificity": estimate["pass_specificity"],
        "threshold": config["threshold"],
    }
    _write_json(out_dir / "history_line.json", history)
    if not scheduled:
        rows = upsert_history(history)
        write_chart(rows, config["threshold"], mode)
    print(json.dumps({**history, "scores_written": written}, indent=2))
    return history


def main(argv: list[str] | None = None) -> int:
    from observability.instrument import load_env

    load_env()  # keys from .env; values already in the environment win
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument("--period", help="a period label from monitoring/config.json")
    window.add_argument("--last-hours", type=float, help="monitor the trailing window")
    parser.add_argument("--yes", action="store_true", help="approve the judge calls")
    parser.add_argument("--rejudge", action="store_true", help="ignore cached verdicts")
    args = parser.parse_args(argv)

    config = load_config()
    if args.period:
        start, end = period_window(config, args.period)
        label, scheduled = args.period, False
    else:
        end = datetime.now(timezone.utc).replace(microsecond=0)
        start = end - timedelta(hours=args.last_hours)
        label, scheduled = f"daily-{end:%Y-%m-%dT%H%MZ}", True
    run(label, start, end, config, scheduled=scheduled, approve=args.yes, rejudge=args.rejudge)
    return 0


if __name__ == "__main__":
    sys.exit(main())
