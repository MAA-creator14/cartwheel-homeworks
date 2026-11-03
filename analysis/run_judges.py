"""Homework 5 pipeline: exports, judge runs, and metrics.

HW4 label files stay untouched. HW5 labels use 1 for Pass and 0 for Fail.

    uv run --no-sync python -m analysis.run_judges export-labels unbacked_promise
    uv run --no-sync python -m analysis.run_judges prepare-inputs unbacked_promise
    uv run --no-sync python -m analysis.run_judges split-data unbacked_promise
    uv run --no-sync python -m analysis.run_judges run-dev \
        unbacked_promise analysis/prompts/unbacked_promise-v0.txt
    uv run --no-sync python -m analysis.run_judges run-test unbacked_promise-v1

Model keys are read from .env; values already in the environment win.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
REPORT_DIR = Path(__file__).resolve().parent / "report"
TRACE_EXPORT = Path(__file__).resolve().parent.parent / "traces" / "support_traces.json"
INPUTS_PATH = STATE_DIR / "hw5_trace_inputs.json"
JUDGE_MODEL = "gpt-4o-mini"


def export_labels(mode: str) -> dict[str, int]:
    """Write the latest HW4 label per trace to ``hw5_labels/<mode>.jsonl``."""
    live: dict[str, dict] = {}
    for line in (STATE_DIR / "labels" / f"{mode}.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("superseded_by"):
            live[row["trace_id"]] = row

    target = STATE_DIR / "hw5_labels" / f"{mode}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        for row in live.values():
            f.write(
                json.dumps(
                    {
                        "trace_id": row["trace_id"],
                        "mode": mode,
                        "label": 1 if row["label"] == "pass" else 0,
                        "evidence": row.get("evidence", ""),
                        "note": row.get("note", ""),
                        "source": row.get("source", ""),
                        "ts": row.get("ts", ""),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    passes = sum(row["label"] == "pass" for row in live.values())
    return {"pass": passes, "fail": len(live) - passes}


def _text(parts: list[dict]) -> str:
    """Join the text parts of a Langfuse input or output message list."""
    return "\n".join(
        part.get("content", "") for message in parts for part in message.get("parts", [])
    )


def _turn_messages(trace: dict) -> list[dict]:
    """Return one turn as user text, tool calls, tool results, and the reply."""
    messages = [{"role": "user", "text": _text(trace["input"])}]
    tools = [o for o in trace["observations"] if o.get("type") == "TOOL"]
    for tool in sorted(tools, key=lambda o: o.get("startTime") or ""):
        name = tool["name"]
        arguments = json.dumps(tool.get("input"), ensure_ascii=False)
        result = json.dumps(tool.get("output"), ensure_ascii=False)
        # _flatten keeps only arguments and text, so the tool name goes inside them.
        messages.append({"role": "tool_call", "name": name, "arguments": f"{name} {arguments}"})
        messages.append({"role": "tool_result", "name": name, "text": f"{name} {result}"})
    messages.append({"role": "assistant", "text": _text(trace["output"])})
    return messages


def prepare_inputs(mode: str) -> dict[str, int]:
    """Save one judge input per labeled conversation, without labels or metadata.

    A conversation with a Fail turn uses that turn. Otherwise it uses the last
    labeled turn. Earlier turns stay in the record, later turns do not.
    """
    labels = [
        json.loads(line)
        for line in (STATE_DIR / "hw5_labels" / f"{mode}.jsonl").read_text("utf-8").splitlines()
    ]
    label_by_trace = {row["trace_id"]: row["label"] for row in labels}
    traces = json.loads(TRACE_EXPORT.read_text("utf-8"))["traces"]
    conversations: dict[str, list[dict]] = {}
    for trace in sorted(traces, key=lambda t: t["timestamp"]):
        conversations.setdefault(trace["cartwheel_scenario_id"], []).append(trace)

    records = []
    for turns in conversations.values():
        labeled = [t for t in turns if t["id"] in label_by_trace]
        if not labeled:
            continue
        failed = [t for t in labeled if label_by_trace[t["id"]] == 0]
        target = (failed or labeled)[-1]
        history = turns[: turns.index(target) + 1]
        records.append(
            {
                "trace_id": target["id"],
                "trace": [message for turn in history for message in _turn_messages(turn)],
            }
        )
    INPUTS_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    fails = sum(label_by_trace[record["trace_id"]] == 0 for record in records)
    return {"records": len(records), "pass": len(records) - fails, "fail": fails}


def split_data(mode: str) -> dict[str, dict[str, int]]:
    """Split the eligible labels 20/40/40 once and return class counts only."""
    from analysis.helpers import split_labels

    splits_path = STATE_DIR / "splits.json"
    if splits_path.exists() and mode in json.loads(splits_path.read_text("utf-8")):
        raise SystemExit(f"{mode} is already split; keep splits.json unchanged")
    records = json.loads(INPUTS_PATH.read_text("utf-8"))
    splits = split_labels(
        mode,
        fractions=(0.20, 0.40, 0.40),
        seed=7,
        min_per_class=10,
        eligible_trace_ids=[record["trace_id"] for record in records],
    )
    labels = [
        json.loads(line)
        for line in (STATE_DIR / "hw5_labels" / f"{mode}.jsonl").read_text("utf-8").splitlines()
    ]
    label_by_trace = {row["trace_id"]: row["label"] for row in labels}
    return {
        name: {
            "pass": sum(label_by_trace[trace_id] == 1 for trace_id in ids),
            "fail": sum(label_by_trace[trace_id] == 0 for trace_id in ids),
        }
        for name, ids in splits.items()
    }


def _registered_judge(mode: str, prompt_text: str, model: str) -> str:
    """Return the judge id for this exact prompt, registering it only if new.

    Reuse keeps a rerun on the same version and its cached predictions, so an
    interrupted batch resumes instead of paying for a new version.
    """
    from analysis.helpers import register_judge

    history_path = STATE_DIR / "judges" / f"_history_{mode}.json"
    if history_path.exists():
        for version in json.loads(history_path.read_text("utf-8"))["versions"]:
            path = STATE_DIR / "judges" / f"{version['judge_id']}.json"
            judge = json.loads(path.read_text("utf-8"))
            if judge["prompt_text"] == prompt_text and judge["model"] == model:
                return judge["judge_id"]
    return register_judge(mode=mode, prompt_text=prompt_text, judge_model=model)["judge_id"]


def run_development(mode: str, prompt_path: str) -> dict:
    """Run one prompt version on the development split and save its metrics."""
    from analysis.helpers import judge_alignment, run_judge

    # Saved inputs only: never fall through to Langfuse or the demo store.
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS_PATH)
    prompt_text = Path(prompt_path).read_text("utf-8")
    judge_id = _registered_judge(mode, prompt_text, JUDGE_MODEL)
    run_judge(judge_id, split="dev", batch_size=10)
    development = judge_alignment(judge_id, split="dev")
    target = REPORT_DIR / f"dev-{judge_id}.json"
    target.write_text(
        json.dumps({**development, "prompt_path": str(prompt_path), "model": JUDGE_MODEL}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return development


def run_test(judge_id: str) -> dict:
    """Freeze the chosen judge once, evaluate it on the test split, save metrics.

    A rerun after an interruption skips the freeze and resumes from cached
    predictions, so only missing traces are paid for.
    """
    from analysis.helpers import freeze_judge, judge_alignment, run_judge

    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS_PATH)
    judge = json.loads((STATE_DIR / "judges" / f"{judge_id}.json").read_text("utf-8"))
    if judge.get("status") != "frozen":
        freeze_judge(judge_id)
    run_judge(judge_id, split="test", batch_size=10)
    test = judge_alignment(judge_id, split="test")

    mode = judge["mode"]
    labels = {
        row["trace_id"]: row["label"]
        for row in map(json.loads, (STATE_DIR / "hw5_labels" / f"{mode}.jsonl").read_text("utf-8").splitlines())
    }
    test_ids = json.loads((STATE_DIR / "splits.json").read_text("utf-8"))[mode]["test"]
    class_counts = {
        "pass": sum(labels[trace_id] == 1 for trace_id in test_ids),
        "fail": sum(labels[trace_id] == 0 for trace_id in test_ids),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"test-{judge_id}.json"
    target.write_text(
        json.dumps({**test, "class_counts": class_counts, "model": judge["model"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**test, "class_counts": class_counts}


def main() -> None:
    from observability.instrument import load_env

    # Model keys come from .env; existing environment variables win.
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-labels")
    export.add_argument("mode")
    prepare = commands.add_parser("prepare-inputs")
    prepare.add_argument("mode")
    split = commands.add_parser("split-data")
    split.add_argument("mode")
    development = commands.add_parser("run-dev")
    development.add_argument("mode")
    development.add_argument("prompt_path")
    test = commands.add_parser("run-test")
    test.add_argument("judge_id")
    args = parser.parse_args()
    if args.command == "export-labels":
        print(export_labels(args.mode))
    elif args.command == "prepare-inputs":
        print(prepare_inputs(args.mode))
    elif args.command == "split-data":
        print(split_data(args.mode))
    elif args.command == "run-dev":
        print(json.dumps(run_development(args.mode, args.prompt_path), indent=2))
    elif args.command == "run-test":
        print(json.dumps(run_test(args.judge_id), indent=2))


if __name__ == "__main__":
    main()
