"""HW3 step 4: independent critic call per conversation.

The critic sees the plan's user-visible facts, role, style, and the messages
(plus other conversations' openings, to spot shared templates). It does not
see `expected`. It may rewrite wording only; plan and style are preserved.
"""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import litellm
from observability.instrument import load_env
from scenarios.drafts.generate_messages import MODEL, ROLE_DESC, STYLE_HINTS

load_env()

def prompt(conv, others):
    t = conv["tuple"]
    msgs = [conv["opening_message"], *conv["followups"]]
    return f"""You review a scripted chat from a simulated user of the Cartwheel marketplace support assistant.

User: {ROLE_DESC[t['role']]}
What the user knows and wants (the ONLY facts allowed): {conv['user_facts']}
Required style: {t['user_style']} ({STYLE_HINTS[t['user_style']]})
Messages (in order; followups are sent without seeing the assistant's replies):
{json.dumps(msgs, indent=1)}

Openings of OTHER conversations in the dataset (to detect shared templates):
{json.dumps(others, indent=1)}

Check for:
1. Invented or changed facts: identifiers, amounts, dates, names, products, or claims (e.g. complaints,
   problems, events) that are not in the allowed facts or contradict them.
2. Followups that assume a specific assistant reply (answering its question, "yes go ahead", "as you said").
3. Wording shared with other conversations, or followups that repeat the same sentence nearly verbatim.
4. Language a real user would not produce (internal policy names, rule numbers).
5. References to tools, traces, prompts, or hidden data.

If there is a problem, rewrite only the wording to fix it. Keep the same number of messages, the same
intent and facts, and the required style. If nothing is wrong, return the messages unchanged.

Return only JSON: {{"issues": ["..."], "opening_message": "...", "followups": [...]}}"""

def critique(args):
    conv, others = args
    for attempt in range(3):
        try:
            resp = litellm.completion(model=MODEL, max_tokens=2000,
                                      messages=[{"role": "user", "content": prompt(conv, others)}])
        except Exception as e:
            print(f"{conv['id']}: API error (attempt {attempt + 1}): {type(e).__name__}", file=sys.stderr)
            continue
        text = (resp.choices[0].message.content or "").strip()
        try:
            r = json.loads(text[text.find("{"): text.rfind("}") + 1])
            break
        except json.JSONDecodeError:
            print(f"{conv['id']}: unparseable reply (attempt {attempt + 1}, finish={resp.choices[0].finish_reason}): {text[:200]!r}", file=sys.stderr)
    else:
        raise RuntimeError(f"{conv['id']}: critic failed 3 times")
    assert len(r["followups"]) == len(conv["followups"]), conv["id"]
    return {**conv, "opening_message": r["opening_message"].strip(),
            "followups": [f.strip() for f in r["followups"]],
            "critic_issues": r.get("issues", []),
            "pre_critic": {"opening_message": conv["opening_message"], "followups": conv["followups"]}}

if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    convs = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    # compare against openings with the same intent (where shared templates would appear)
    jobs = [(c, [o["opening_message"] for o in convs
                 if o["id"] != c["id"] and o["tuple"]["intent"] == c["tuple"]["intent"]]) for c in convs]
    with ThreadPoolExecutor(max_workers=4) as pool:
        out = list(pool.map(critique, jobs))
    dst.write_text("".join(json.dumps(o) + "\n" for o in out))
    print(f"{len(out)} reviewed, {sum(o['opening_message'] != o['pre_critic']['opening_message'] or o['followups'] != o['pre_critic']['followups'] for o in out)} rewritten -> {dst}")
