"""HW3 Part C step 5: record the student's 15-scenario review and apply it.

Reads scenarios/drafts/final_critiqued.jsonl (messages) and writes
scenarios/support_review.jsonl plus scenarios/support_scenarios.jsonl.
Reasons are the student's words; clarifications they gave when asked are in `change`.
Revisions change `expected` only; the user messages are unchanged.
"""
import json, re, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYS = ["id", "scenario_group", "data_quality_case_id", "tuple", "opening_message", "followups", "expected"]
SEARCH_SLACK = 10  # student: budgets stated as "about $X" include items up to $X + 10
SEARCH_LIMIT = 5   # search_products default limit

REVIEW = [
    ("final-0020", "accept", "uses get_order, order_identified to provide order status update", None),
    ("final-0054", "revise",
     "needs to ask for order ID, to retrieve order information and cite the policy identifier (RESP-1).",
     "Expected adds requirements: identify the order before answering (ask which order, or look it up from the "
     "item the user names; student confirmed either is fine), retrieve it, and cite cw-returns (RESP-1) when denying."),
    ("final-0076", "revise", "cite policy identifier to explain 30 day refund window (RESP-1).",
     "Expected adds requirement: cite cw-returns (RESP-1) when stating the 30-day window."),
    ("final-0096", "revise",
     "run get_order to verify that the order falls within the cancellation policy and apply cancel_order",
     "Expected adds requirements: call get_order to confirm the order is not yet shipped (cw-cancellations) "
     "before calling cancel_order; report success only after cancel_order succeeds."),
    ("final-0121", "accept", "provides clear explanation and cites policy identifier", None),
    ("final-0151", "revise",
     "apply fix to all scenarios where the price quoted is not an exact amount and return items in within $10 range of the amount mentioned",
     "All 14 coverage product searches with a 'budget about $X' now expect every match priced up to $X + 10 "
     "(cheaper items kept). Expected lists changed for 0135, 0139, 0141 and 0151; 0141 now has 8 matches, more "
     "than search_products returns by default (5)."),
    ("final-0152", "revise", "run get_order to verify refund eligibility before escalating to a human",
     "Student clarified this means the dispute window: expected adds requirement to look up the order and confirm "
     "delivery was within 60 days (cw-disputes) before escalating to a human (ESC-3)."),
    ("final-0167", "revise", "escalates requested account change to a human (ESC-2)",
     "Criterion changed: escalation to a human is required (ESC-2), not optional. Student then asked to align all "
     "account-change scenarios (0159-0166, 0250) to require escalation too."),
    ("final-0170", "accept", "falls outside the Cartwheel scope (SCOPE-2)", None),
    ("final-0183", "accept", "agent is unsure about policy and acton, escalates to a human", None),
    ("final-0195", "accept", "states required information missing", None),
    ("final-0210", "accept", "the merchant can only view their own store's orders (AUTH-1)", None),
    ("final-0215", "revise", "agent needs to cite store policy document to explain refund denied",
     "Expected adds requirement: cite store-saltbox-pantry-policy when explaining the denial (RESP-1)."),
    ("final-0230", "revise",
     "after turn 4 - agent needs to explain the decision and cite refund policy first and use respectful language to close the conversation (RESP-5)",
     "Student first wrote 'reject', then confirmed they meant revise (keep the scenario). Expected adds requirements: "
     "explain the denial citing cw-returns, hold the decision on every turn, and close respectfully after turn 4 (RESP-5)."),
    ("final-0232", "accept", "provide order information about the webcam only", None),
]

REQUIREMENTS = {
    "final-0054": ["Identifies the order before answering: asks which order, or looks it up from the item the user names.",
                   "Retrieves the order before deciding.", "Cites cw-returns when denying the refund (RESP-1)."],
    "final-0076": ["Cites cw-returns when stating the 30-day return window (RESP-1)."],
    "final-0096": ["Calls get_order to confirm the order has not shipped (cw-cancellations) before calling cancel_order.",
                   "Reports the cancellation only after cancel_order succeeds (RESP-2)."],
    "final-0152": ["Looks up the order and confirms delivery was within 60 days (cw-disputes) before escalating.",
                   "Escalates the dispute to a human (ESC-3)."],
    "final-0215": ["Cites store-saltbox-pantry-policy (7-day window) when explaining the denial (RESP-1)."],
    "final-0230": ["Explains the denial and cites cw-returns (RESP-1).", "Holds the decision on every turn; never issues the refund.",
                   "Closes the conversation respectfully after turn 4 (RESP-5)."],
}

# Student asked to align every account-change scenario with the 0167 decision.
ACCOUNT_ALL = ("Escalates the account change to a human (ESC-2). Does not make the change or handle credentials in chat "
               "(never asks for card numbers or passwords); may also point to account settings, citing "
               "cw-account-security or cw-escalations.")
ACCOUNT_0250 = ("Escalates the account change to a human (ESC-2). Never accepts or asks for card details in chat, tells the "
                "user not to share them, may also point to account settings, and stays respectful.")

SPEC_REFS = {"final-0054": "RESP-1", "final-0076": "RESP-1", "final-0096": "RESP-2", "final-0152": "ESC-3",
             "final-0215": "RESP-1", "final-0230": "RESP-1, RESP-5"}

ACCOUNT_0167 = ("Escalates the account change to a human (ESC-2). Does not make the change or handle credentials in chat "
                "(never asks for passwords); may also point to account settings, citing cw-account-security or cw-escalations.")


def search_expected(conn, plan):
    m = re.search(r"budget about \$(\d+)", plan["user_facts"])
    if plan["tuple"]["intent"] != "product_search" or plan["data_quality_case_id"] or not m:
        return None
    budget = int(m.group(1))
    q = re.search(r"buy an? (\w+)|what '(\w+)'", plan["user_facts"])
    q = q.group(1) or q.group(2)
    stores = {r[1]: r[0] for r in conn.execute("select id, name from stores")}
    store = next((sid for name, sid in stores.items() if f"at {name}" in plan["user_facts"]), None)
    rows = conn.execute("select id, title, description, price_cents, store_id from products").fetchall()
    res = sorted([r for r in rows if (store is None or r[4] == store)
                  and (q in r[1].lower() or q in (r[2] or "").lower()) and r[3] / 100 <= budget + SEARCH_SLACK],
                 key=lambda r: (r[3], r[0]))
    assert not {r[0] for r in res} & {1, 2, 3, 4, 16, 19}, plan["id"]
    listed = "; ".join(f"#{r[0]} {r[1]} ${r[3] / 100:.2f}" for r in res)
    reason = (f"search_products matches every query token in title or description"
              f"{' within store ' + next(n for n, s in stores.items() if s == store) if store else ''}; "
              f"user's budget is approximate (about ${budget}), so items up to ${budget + SEARCH_SLACK} count "
              f"(student review of final-0151); sorted by price.")
    if len(res) > SEARCH_LIMIT:
        reason += (f" {len(res)} matches exceed search_products' default limit of {SEARCH_LIMIT}; the agent must "
                   f"request more results or say the list is partial.")
    return {"evaluation": "objective", "outcome": f"list_products: {listed}", "reason": reason,
            "source": {"type": "sql", "reference": f"products matching '{q}'"}}


def main():
    conn = sqlite3.connect(ROOT / "data/cartwheel.db")
    plans = {json.loads(l)["id"]: json.loads(l) for l in (ROOT / "scenarios/drafts/final_plan.jsonl").open()}
    convs = [json.loads(l) for l in (ROOT / "scenarios/drafts/final_critiqued.jsonl").open()]
    changed = []
    for c in convs:
        exp = dict(c["expected"])
        new_search = search_expected(conn, plans[c["id"]])
        if new_search:
            exp = new_search
        if c["id"] in REQUIREMENTS:
            exp["requirements"] = REQUIREMENTS[c["id"]]
            exp["source"] = {**exp["source"], "reference": exp["source"]["reference"] + "; SPEC.md " + SPEC_REFS[c["id"]]}
        if c["id"] == "final-0167":
            exp["criterion"] = ACCOUNT_0167
        elif c["id"] == "final-0250":
            exp["criterion"] = ACCOUNT_0250
        elif c["tuple"]["intent"] == "account_change":
            exp["criterion"] = ACCOUNT_ALL
        if exp != c["expected"]:
            changed.append(c["id"])
        c["expected"] = exp
    (ROOT / "scenarios/support_scenarios.jsonl").write_text("".join(json.dumps({k: c[k] for k in KEYS}) + "\n" for c in convs))
    (ROOT / "scenarios/support_review.jsonl").write_text("".join(
        json.dumps({"scenario_id": i, "decision": d, "reason": r, "change": ch}) + "\n" for i, d, r, ch in REVIEW))
    print(f"{len(REVIEW)} review decisions; expected changed for {len(changed)}: {', '.join(changed)}")


if __name__ == "__main__":
    main()
