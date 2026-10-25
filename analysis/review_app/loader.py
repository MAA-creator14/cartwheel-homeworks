"""Load Cartwheel traces and group them into reviewable sessions.

Cartwheel writes one Langfuse trace per user turn, so a multi-turn
conversation is spread over several traces. Langfuse's own ``sessionId`` is
empty on these traces; the conversation key lives only in the
``cartwheel.session_id`` attribute. This module groups the traces by that
attribute, orders the turns in time, and reduces each turn to what a reviewer
reads: the user message, the tool calls with their full results, and the final
reply. Wrapper spans (``cartwheel.session_message``, ``Agent Workflow``, the
agent span) and the model-call generations, whose outputs are not recorded on
the LiteLLM path, are counted but not rendered.

Live Langfuse is the default source (``langfuse_io.fetch_traces``). The Module 1
export in ``traces/support_traces.json`` is the offline fallback.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.helpers import langfuse_io
from analysis.helpers.normalization import _text, normalize_trace

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_PATH = REPO_ROOT / "traces" / "support_traces.json"
CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "sessions.json"

# Only the HW3 final run is reviewed; smoke and pilot traces are excluded.
SCENARIO_PREFIX = "final-"

# Used to build a link back to each trace in the Langfuse UI.
LANGFUSE_PROJECT = os.environ.get("CARTWHEEL_LANGFUSE_PROJECT", "cartwheel-dev")


def load_raw_traces(source: str = "auto") -> tuple[list[dict[str, Any]], str]:
    """Return normalized traces and the name of the source that supplied them.

    ``source`` is ``"langfuse"``, ``"export"``, or ``"auto"`` (Langfuse when
    configured, otherwise the export). A Langfuse failure under ``"auto"``
    falls back to the export so the reviewer can keep working; the returned
    source name records which one was used.
    """
    if source in ("auto", "langfuse") and langfuse_io.is_configured():
        try:
            return langfuse_io.fetch_traces(limit=2000), "langfuse"
        except Exception:
            if source == "langfuse":
                raise
    if source == "langfuse":
        raise langfuse_io.LangfuseNotConfigured("LANGFUSE_* is not set")
    data = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    records = data["traces"] if isinstance(data, dict) else data
    return [normalize_trace(r) for r in records], "export"


def _system_prompt(observations: list[dict[str, Any]]) -> str:
    for obs in observations:
        if obs.get("type") != "GENERATION" or not isinstance(obs.get("input"), list):
            continue
        for message in obs["input"]:
            if isinstance(message, dict) and message.get("role") == "system":
                return _text([message])
    return ""


def _tool_steps(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool calls in start order, each paired with its full result.

    Calls that start at the same millisecond ran in parallel; they share a
    ``parallel_group`` so the interface does not imply an order between them.
    """
    tools = sorted(
        (o for o in observations if o.get("type") == "TOOL"),
        key=lambda o: o.get("start_time") or "",
    )
    starts: dict[str, int] = defaultdict(int)
    for obs in tools:
        starts[(obs.get("start_time") or "")[:23]] += 1
    steps = []
    group = 0
    last_start = None
    for obs in tools:
        start = (obs.get("start_time") or "")[:23]
        if start != last_start:
            group += 1
            last_start = start
        result = obs.get("output")
        ok = result.get("ok") if isinstance(result, dict) else None
        steps.append(
            {
                "observation_id": obs.get("id"),
                "name": obs.get("name"),
                "arguments": obs.get("input"),
                "result": result,
                "ok": ok,
                "error": result.get("error") if isinstance(result, dict) else None,
                "status": result.get("status") if isinstance(result, dict) else None,
                "start_time": obs.get("start_time"),
                "parallel": starts[start] > 1,
                "parallel_group": group,
            }
        )
    return steps


def _turn(trace: dict[str, Any], host: str) -> dict[str, Any]:
    observations = trace.get("observations") or []
    return {
        "trace_id": trace["trace_id"],
        "timestamp": trace.get("timestamp"),
        "user": _text(trace.get("input")),
        "reply": _text(trace.get("output")),
        "steps": _tool_steps(observations),
        "model_calls": sum(o.get("type") == "GENERATION" for o in observations),
        "prompt_version": trace.get("meta", {}).get("prompt_version"),
        "langfuse_url": f"{host}/project/{LANGFUSE_PROJECT}/traces/{trace['trace_id']}",
    }


def build_sessions(
    traces: list[dict[str, Any]], prefix: str = SCENARIO_PREFIX
) -> list[dict[str, Any]]:
    """Group traces by ``cartwheel.session_id`` into ordered conversations."""
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        meta = trace.get("meta", {})
        scenario = meta.get("scenario_id") or ""
        if not scenario.startswith(prefix):
            continue
        session_id = meta.get("session_id") or f"trace:{trace['trace_id']}"
        groups[session_id].append(trace)

    sessions = []
    for session_id, group in groups.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        first = group[0]
        meta = first.get("meta", {})
        sessions.append(
            {
                "session_id": session_id,
                "scenario_id": meta.get("scenario_id"),
                "role": meta.get("role"),
                "user_id": first.get("metadata", {}).get("cartwheel.user_id"),
                "system_prompt": _system_prompt(first.get("observations") or []),
                "turns": [_turn(t, host) for t in group],
            }
        )
    sessions.sort(key=lambda s: s["scenario_id"] or "")
    return sessions


def load_sessions(refresh: bool = False, source: str = "auto") -> dict[str, Any]:
    """Return ``{"source", "fetched_at", "sessions"}``, using the local cache.

    The cache keeps the reviewer from re-reading every trace from Langfuse on
    each page load. ``refresh`` rebuilds it from the source.
    """
    if not refresh and CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    traces, used = load_raw_traces(source)
    payload = {
        "source": used,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sessions": build_sessions(traces),
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    return payload
