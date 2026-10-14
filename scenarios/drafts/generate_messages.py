"""HW3 step 3: write user messages for each plan, one model call per conversation.

The prompt gets only role, user-visible facts, style, and turn count. It never
sees `expected`, record_state, policy, difficulty, or any id not in user_facts.
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import litellm
from observability.instrument import load_env

load_env()
MODEL = "anthropic/claude-sonnet-5-5"
STYLE_HINTS = {
    "neutral_conversational": "plain, polite, everyday chat",
    "terse_fragmentary": "very short fragments, minimal punctuation, no pleasantries",
    "typo_heavy": "several realistic typos and missing capitals",
    "confused_rambling": "unsure, wandering, extra irrelevant detail",
    "frustrated_impatient": "annoyed and impatient, but not abusive",
    "repetitive_pressuring": "repeats the demand and pushes for it",
    "operational_shorthand": "work shorthand, like a busy staff member typing a ticket note",
    "requests_short_plain_answer": "explicitly asks for a short, plain answer",
}
ROLE_DESC = {
    "shopper": "a shopper (customer) on the Cartwheel marketplace",
    "merchant": "a merchant who runs a store on the Cartwheel marketplace",
    "support": "a Cartwheel support staff member using the internal assistant",
}

def prompt(plan):
    t = plan["tuple"]; n = t["turn_count"]
    return f"""You are simulating {ROLE_DESC[t['role']]} typing into the Cartwheel support chat.

What this person knows and wants: {plan['user_facts']}

Writing style: {t['user_style']} ({STYLE_HINTS[t['user_style']]}).
Number of messages: {n} (one opening message{'' if n == 1 else f' plus {n-1} followup(s)'}).

Rules:
- Write as a real person would type. Keep the style in every message.
- Only use facts stated above. Do not invent order numbers, dates, prices, names, or products.
  Shoppers do not know order numbers unless stated above.
- Do not mention policies by name, rules, tools, or anything about how the assistant works.
- Followups are sent without seeing the assistant's reply, so they must make sense whatever it said:
  add detail, correct yourself, or repeat/push. Never answer a question it might have asked,
  and never say "yes go ahead" or refer to what it said.
- Avoid stock openers like "Hi there" or "Hello, I need help with".

Return only JSON: {{"opening_message": "...", "followups": [{', '.join(['"..."'] * (n - 1))}]}}"""

def generate(plan, tries=3):
    for attempt in range(tries):
        try:
            return _generate(plan)
        except Exception as e:
            if attempt == tries - 1:
                raise
            print(f"retry {plan['id']}: {type(e).__name__}", file=sys.stderr)

def _generate(plan):
    resp = litellm.completion(model=MODEL, max_tokens=600, temperature=1.0,
                              messages=[{"role": "user", "content": prompt(plan)}])
    text = resp.choices[0].message.content.strip()
    text = text[text.find("{"): text.rfind("}") + 1]
    msg = json.loads(text)
    assert len(msg["followups"]) == plan["tuple"]["turn_count"] - 1, plan["id"]
    return {**plan, "opening_message": msg["opening_message"].strip(),
            "followups": [f.strip() for f in msg["followups"]]}

if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    plans = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    with ThreadPoolExecutor(max_workers=4) as pool:
        out = list(pool.map(generate, plans))
    dst.write_text("".join(json.dumps(o) + "\n" for o in out))
    print(f"{len(out)} conversations -> {dst}")
