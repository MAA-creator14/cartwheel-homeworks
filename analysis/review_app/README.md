# HW4 review interface

A review interface for open coding and structured labeling of Cartwheel traces.
It shows one conversation per page, with traces grouped by `cartwheel.session_id`
and turns in order. Each tool call appears with its full result next to the reply.

Run from the repository root with the Langfuse stack up:

```bash
uv run --no-sync python -m analysis.review_app.server           # http://127.0.0.1:8030/
uv run --no-sync python -m analysis.review_app.server --no-langfuse   # labels saved locally only
```

On the first load the server reads every `final-*` trace from Langfuse, which takes
about a minute. It caches the result in `analysis/review_app/.cache/sessions.json`,
which is git-ignored. Open `/api/sessions?refresh=1` to rebuild the cache. When
`LANGFUSE_*` is not set, the server falls back to `traces/support_traces.json`, and
the status line in the header shows which source is in use.

| Tab | Purpose | File |
| --- | --- | --- |
| Review | Read a conversation. For each turn, write a free-form note on the first failure (select text to attach the note to it) or mark "no failure observed". The answer key unlocks only after every turn has a judgment. | `analysis/state/annotations.json` (local only) |
| Taxonomy | Modes with definition, boundary, requirement, evaluator, positives, close negatives, and the original notes | `analysis/state/patterns.json` |
| Label | One Fail/Pass per trace and mode, with evidence | Langfuse score (1 = failure present) and `analysis/state/labels/<mode>.jsonl` |
| Progress | Review-set size, judged and unjudged traces per batch, label completeness, sample fractions, suggestion decisions | Reads all of the above |

Batches come from `analysis/state/sample_manifest.json`, and agent suggestions from
`analysis/state/suggestions.json`. When you edit a note, its earlier text is kept in
`history`. A retracted note stays in the file with `retracted: true`. Accepted and
rejected suggestions keep their decision and your reason.
