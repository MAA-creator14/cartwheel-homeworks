"""The HW4 review app groups turns by session and writes Langfuse only for labels."""

from __future__ import annotations

import json

import pytest

from analysis.helpers.normalization import normalize_trace
from analysis.review_app import loader, server


@pytest.fixture(scope="module")
def export_records() -> list[dict]:
    if not loader.EXPORT_PATH.exists():
        pytest.skip("Module 1 trace export not present")
    return json.loads(loader.EXPORT_PATH.read_text())["traces"]


@pytest.fixture(scope="module")
def sessions(export_records: list[dict]) -> list[dict]:
    return loader.build_sessions([normalize_trace(r) for r in export_records])


@pytest.fixture
def state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    files = {name: tmp_path / path.name for name, path in server.FILES.items()}
    monkeypatch.setattr(server, "FILES", files)
    monkeypatch.setattr(server, "LABELS_DIR", tmp_path / "labels")
    server.write_json("patterns", {"modes": [{"name": "unconfirmed_write"}]})
    for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.setenv(var, "test")
    calls: list[dict] = []
    monkeypatch.setattr(
        server.langfuse_io, "write_label_score", lambda **kw: calls.append(kw) or kw["trace_id"]
    )
    return calls


def test_sessions_cover_every_final_trace(export_records, sessions) -> None:
    final = [r for r in export_records if r["cartwheel_scenario_id"].startswith("final-")]
    assert len({r["cartwheel_scenario_id"] for r in final}) == len(sessions)
    assert sum(len(s["turns"]) for s in sessions) == len(final)
    assert all(s["scenario_id"].startswith("final-") for s in sessions)


def test_turns_are_in_time_order(sessions) -> None:
    multi = [s for s in sessions if len(s["turns"]) > 1]
    assert multi
    for s in multi:
        stamps = [t["timestamp"] for t in s["turns"]]
        assert stamps == sorted(stamps)


def test_tool_results_are_kept_in_full(export_records, sessions) -> None:
    raw = {
        o["id"]: (o["name"], o["input"], o["output"])
        for r in export_records
        for o in r["observations"]
        if o["type"] == "TOOL"
    }
    steps = [step for s in sessions for t in s["turns"] for step in t["steps"]]
    assert len(steps) == len(raw)
    for step in steps:
        assert (step["name"], step["arguments"], step["result"]) == raw[step["observation_id"]]


def test_wrapper_spans_and_generations_are_not_steps(sessions) -> None:
    hidden = {"openai.response", "Agent Workflow", "cartwheel-support.agent", "cartwheel.session_message"}
    names = {step["name"] for s in sessions for t in s["turns"] for step in t["steps"]}
    assert not names & hidden


def test_annotation_edit_keeps_history_and_never_writes_langfuse(state) -> None:
    server.upsert_annotation({"id": "a1", "trace_id": "t1", "note": "first reading"})
    server.upsert_annotation({"id": "a1", "trace_id": "t1", "note": "revised reading"})
    server.upsert_annotation({"id": "a1", "trace_id": "t1", "note": "revised reading", "retracted": True})
    [saved] = server.read_json("annotations")["annotations"]
    assert saved["note"] == "revised reading"
    assert [h["note"] for h in saved["history"]] == ["first reading"]
    assert saved["retracted"] is True
    assert state == []


def test_label_writes_score_and_local_file(state, monkeypatch) -> None:
    monkeypatch.setattr(server, "LANGFUSE_WRITES", True)
    rec = server.save_label({"trace_id": "t1", "mode": "unconfirmed_write", "label": "fail", "evidence": "q"})
    assert rec["value"] == 1 and rec["langfuse_synced"] is True
    assert state == [{"trace_id": "t1", "mode": "unconfirmed_write", "label": 1, "comment": "q"}]
    assert server.latest_labels()["unconfirmed_write"]["t1"]["label"] == "fail"


def test_label_offline_mode_stays_local(state, monkeypatch) -> None:
    monkeypatch.setattr(server, "LANGFUSE_WRITES", False)
    rec = server.save_label({"trace_id": "t1", "mode": "unconfirmed_write", "label": "pass"})
    assert rec["value"] == 0 and rec["langfuse_synced"] is False
    assert state == []


@pytest.mark.parametrize(
    "body",
    [
        {"trace_id": "t1", "mode": "unsupported_policy_claim", "label": "fail"},
        {"trace_id": "t1", "mode": "unconfirmed_write", "label": "maybe"},
        {"trace_id": "", "mode": "unconfirmed_write", "label": "fail"},
    ],
)
def test_label_rejects_unknown_mode_bad_value_or_missing_trace(state, body) -> None:
    with pytest.raises(ValueError):
        server.save_label(body)
    assert state == []


def test_dropped_mode_takes_no_labels(state) -> None:
    server.write_json(
        "patterns", {"modes": [{"name": "unconfirmed_write"}, {"name": "old_mode", "status": "dropped"}]}
    )
    assert server.known_modes() == {"unconfirmed_write"}
    with pytest.raises(ValueError):
        server.save_label({"trace_id": "t1", "mode": "old_mode", "label": "pass"})
    assert state == []


def test_rejected_suggestion_is_kept(state) -> None:
    server.write_json("suggestions", [{"id": "s1", "trace_id": "t1", "mode": "unconfirmed_write"}])
    server.decide_suggestion({"id": "s1", "status": "rejected", "note": "tool confirmed success"})
    [s] = server.read_json("suggestions")
    assert s["status"] == "rejected" and s["decision_note"] == "tool confirmed success"


def test_spec_reference_lists_requirements_and_threshold() -> None:
    ref = server.spec_reference()
    ids = {r["id"] for r in ref["requirements"]}
    assert {"AUTH-1", "ESC-1", "RESP-2"} <= ids
    assert ref["facts"]["refund_auto_approve_threshold_usd"] == 100
    assert len(ref["tools"]) == 9
