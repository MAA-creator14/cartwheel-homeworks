"""Review server for the HW4 open-coding and labeling workflow.

Adapted from ``analysis/server.py``: a standard-library HTTP server over the
plain files in ``analysis/state/``. The differences follow the HW4 trace
review:

- traces are served grouped by ``cartwheel.session_id`` (see ``loader.py``);
- free-form annotations are local only and never become Langfuse scores, and
  an edited note keeps its earlier text in ``history``;
- a retracted note is kept with ``retracted: true`` rather than deleted, so the
  sequence from observation to category stays inspectable;
- suggestions keep their accept or reject decision instead of disappearing;
- ``POST /api/labels`` is the only path that writes to Langfuse. Each accepted
  Pass or Fail becomes a score (1 = failure present) and a line in
  ``analysis/state/labels/<mode>.jsonl``.

API:

    GET  /                        the review app
    GET  /api/sessions[?refresh=1]
    GET  /api/annotations         POST upserts one annotation
    GET  /api/manifest            POST replaces sample_manifest.json
    GET  /api/patterns            POST replaces patterns.json
    GET  /api/suggestions         POST replaces suggestions.json
    POST /api/suggestions/decide  {id, status: accepted|rejected, note}
    GET  /api/labels              latest label per (mode, trace)
    POST /api/labels              {trace_id, mode, label: fail|pass, evidence, note}
    GET  /api/spec                requirement ids from SPEC.md, facts.yaml threshold
    GET  /api/answer_key?scenario=final-0001

Run from the repository root:

    uv run --no-sync python -m analysis.review_app.server            # :8030
    uv run --no-sync python -m analysis.review_app.server --no-langfuse
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from analysis.helpers import langfuse_io
from analysis.review_app import loader

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
STATE_DIR = REPO_ROOT / "analysis" / "state"
LABELS_DIR = STATE_DIR / "labels"
SCENARIOS_PATH = REPO_ROOT / "scenarios" / "support_scenarios.jsonl"

FILES = {
    "annotations": STATE_DIR / "annotations.json",
    "manifest": STATE_DIR / "sample_manifest.json",
    "patterns": STATE_DIR / "patterns.json",
    "suggestions": STATE_DIR / "suggestions.json",
}
DEFAULTS: dict[str, Any] = {
    "annotations": {"annotations": []},
    "manifest": {"batches": []},
    "patterns": {"modes": []},
    "suggestions": [],
}

MODE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
REQUIREMENT = re.compile(r"\*\*([A-Z]+-\d+)\.\*\*\s*(.*)")
TOOL_ROW = re.compile(r"^\|\s*(TOOL-\d+)\s*\|\s*`([^`]+)`\s*\|(.*)$")

# Langfuse writes are on unless the server is started with --no-langfuse.
LANGFUSE_WRITES = True
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(name: str) -> Any:
    path = FILES[name]
    if not path.exists():
        return DEFAULTS[name]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULTS[name]


def write_json(name: str, data: Any) -> None:
    path = FILES[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# annotations
# ---------------------------------------------------------------------------


def upsert_annotation(incoming: dict[str, Any]) -> dict[str, Any]:
    """Insert or update one annotation, keeping earlier note text in history."""
    if not incoming.get("id") or not incoming.get("trace_id"):
        raise ValueError("an annotation needs an id and a trace_id")
    if incoming.get("kind", "note") not in ("note", "no_failure"):
        raise ValueError("kind must be note or no_failure")
    doc = read_json("annotations")
    items = doc.setdefault("annotations", [])
    for i, existing in enumerate(items):
        if existing.get("id") != incoming["id"]:
            continue
        merged = {**existing, **incoming}
        history = list(existing.get("history", []))
        if incoming.get("note") != existing.get("note"):
            history.append({"note": existing.get("note"), "ts": existing.get("ts")})
        if incoming.get("retracted") and not existing.get("retracted"):
            merged["retracted_at"] = _now()
        merged["history"] = history
        merged["ts"] = _now()
        items[i] = merged
        write_json("annotations", doc)
        return merged
    created = {
        "kind": "note",
        "history": [],
        "retracted": False,
        **incoming,
        "ts": _now(),
        "created": _now(),
    }
    items.append(created)
    write_json("annotations", doc)
    return created


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------


def decide_suggestion(body: dict[str, Any]) -> dict[str, Any]:
    status = body.get("status")
    if status not in ("accepted", "rejected"):
        raise ValueError("status must be accepted or rejected")
    suggestions = read_json("suggestions")
    for s in suggestions:
        if s.get("id") == body.get("id"):
            s["status"] = status
            s["decision_note"] = body.get("note", "")
            s["decided_at"] = _now()
            write_json("suggestions", suggestions)
            return s
    raise ValueError(f"unknown suggestion id: {body.get('id')}")


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


def known_modes() -> set[str]:
    """Modes that take labels; a mode dropped during taxonomy revision does not."""
    return {
        m.get("name")
        for m in read_json("patterns").get("modes", [])
        if m.get("name") and m.get("status") != "dropped"
    }


def _label_path(mode: str) -> Path:
    return LABELS_DIR / f"{mode}.jsonl"


def latest_labels() -> dict[str, dict[str, dict[str, Any]]]:
    """Return ``{mode: {trace_id: latest record}}`` for the modes in patterns.json."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for mode in sorted(known_modes()):
        path = _label_path(mode)
        rows: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    rows[rec["trace_id"]] = rec
        out[mode] = rows
    return out


def save_label(body: dict[str, Any]) -> dict[str, Any]:
    """Append an accepted judgment locally, then write it to Langfuse."""
    mode = str(body.get("mode") or "")
    label = body.get("label")
    trace_id = str(body.get("trace_id") or "")
    if not MODE_NAME.match(mode) or mode not in known_modes():
        raise ValueError(f"mode {mode!r} is not a final mode in patterns.json")
    if label not in ("fail", "pass"):
        raise ValueError("label must be fail (failure present) or pass (absent)")
    if not trace_id:
        raise ValueError("a label needs a trace_id")
    record = {
        "trace_id": trace_id,
        "mode": mode,
        "label": label,
        "value": 1 if label == "fail" else 0,
        "evidence": body.get("evidence", ""),
        "note": body.get("note", ""),
        "source": body.get("source", "human"),
        "ts": _now(),
        "langfuse_synced": False,
    }
    if LANGFUSE_WRITES and langfuse_io.is_configured():
        try:
            langfuse_io.write_label_score(
                trace_id=trace_id,
                mode=mode,
                label=record["value"],
                comment=record["evidence"] or None,
            )
            record["langfuse_synced"] = True
        except Exception as exc:  # pragma: no cover - network-only path
            record["langfuse_error"] = str(exc)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    with _label_path(mode).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


# ---------------------------------------------------------------------------
# reference data
# ---------------------------------------------------------------------------


def spec_reference() -> dict[str, Any]:
    requirements = []
    tools = []
    for line in (REPO_ROOT / "SPEC.md").read_text(encoding="utf-8").splitlines():
        if m := REQUIREMENT.search(line):
            requirements.append({"id": m.group(1), "text": m.group(2).strip()})
        elif m := TOOL_ROW.match(line):
            cells = [c.strip() for c in m.group(3).strip("|").split("|")]
            tools.append({"id": m.group(1), "name": m.group(2), "cells": cells})
    facts = yaml.safe_load((REPO_ROOT / "facts.yaml").read_text(encoding="utf-8"))
    return {
        "requirements": requirements,
        "tools": tools,
        "facts": {k: v for k, v in facts.items() if not isinstance(v, (dict, list))},
    }


def answer_key(scenario_id: str) -> dict[str, Any]:
    for line in SCENARIOS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("id") == scenario_id:
                return {
                    "scenario_id": scenario_id,
                    "expected": rec.get("expected"),
                    "design": rec.get("tuple"),
                    "data_quality_case_id": rec.get("data_quality_case_id"),
                }
    raise ValueError(f"unknown scenario: {scenario_id}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        return

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                body = (HERE / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/sessions":
                self._json(loader.load_sessions(refresh=query.get("refresh") == ["1"]))
            elif url.path.removeprefix("/api/") in FILES:
                self._json(read_json(url.path.removeprefix("/api/")))
            elif url.path == "/api/labels":
                self._json(latest_labels())
            elif url.path == "/api/spec":
                self._json(spec_reference())
            elif url.path == "/api/answer_key":
                self._json(answer_key((query.get("scenario") or [""])[0]))
            elif url.path == "/api/status":
                self._json(
                    {
                        "langfuse_configured": langfuse_io.is_configured(),
                        "langfuse_writes": LANGFUSE_WRITES,
                    }
                )
            else:
                self._json({"error": f"unknown path: {url.path}"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        if body is None:
            self._json({"error": "expected a JSON body"}, 400)
            return
        try:
            with _lock:
                if path == "/api/annotations":
                    self._json(upsert_annotation(body))
                elif path == "/api/suggestions/decide":
                    self._json(decide_suggestion(body))
                elif path == "/api/labels":
                    self._json(save_label(body))
                elif path in ("/api/manifest", "/api/patterns", "/api/suggestions"):
                    write_json(path.removeprefix("/api/"), body)
                    self._json({"ok": True})
                else:
                    self._json({"error": f"cannot POST to {path}"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)


def main() -> None:
    global LANGFUSE_WRITES
    parser = argparse.ArgumentParser(description="HW4 review interface")
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--no-langfuse",
        action="store_true",
        help="save labels locally only (marked langfuse_synced: false)",
    )
    args = parser.parse_args()
    LANGFUSE_WRITES = not args.no_langfuse

    from observability.instrument import load_env

    load_env()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"review interface on http://{args.host}:{args.port}/")
    print(f"state: {STATE_DIR}")
    print(f"langfuse configured: {langfuse_io.is_configured()}, writes: {LANGFUSE_WRITES}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
