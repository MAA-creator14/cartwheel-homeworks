"""HW3 Part C, steps 1-2: plan the 250 final scenarios from the seeded data.

Every expected value is computed here from the database, facts.yaml, and the
policy pages (never from a model). `user_facts` holds only what the simulated
user knows; it is the sole input to message generation. As-of date comes from
meta.world_asof. Run after `python -m seed.generate` (deterministic seed).

Lessons from the pilot review:
- Duplicate-title case: store 1 has four "Heavy-Duty Vase" products (1, 2, 16, 19).
- Student rule: asking before acting is a failure only when the user clearly
  asked for the action, so user_facts state explicitly whether the user asks
  the agent to act ("asks the agent to refund it") or only asks for information.
- Product search answers use the tool's matching (title OR description).
"""
from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "cartwheel.db"
OUT = Path(__file__).with_name("final_plan.jsonl")
rng = random.Random(20261013)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
ASOF = date.fromisoformat(conn.execute("select value from meta where key='world_asof'").fetchone()[0])
THRESHOLD = 100.0
PLATFORM_WINDOW = 30
DISPUTE_WINDOW = 60
STORES = {r["id"]: dict(r) for r in conn.execute("select * from stores")}
STORE_POLICY = {
    2: "store-juniper-home-goods-policy", 5: "store-cascade-audio-policy",
    7: "store-northwind-books-policy", 10: "store-meridian-cycles-policy",
    13: "store-saltbox-pantry-policy", 15: "store-second-stitch-apparel-policy",
}
DQ_ORDERS = {8001, 8002, 8003}
DQ_PRODUCTS = {1, 2, 3, 4, 16, 19}
PINNED = {3980, 4127, 4455}
PRODUCTS = {r["id"]: dict(r) for r in conn.execute("select * from products")}
SUPPORT = [9501, 9502, 9503, 9504, 9505]


def d(s):
    return date.fromisoformat(s) if s else None


ORDERS = []
for r in conn.execute("select o.*, p.title from orders o join products p on p.id=o.product_id"):
    o = dict(r)
    o["total"] = o["total_cents"] / 100
    o["delivered"] = d(o["delivered_at"])
    o["age"] = (ASOF - o["delivered"]).days if o["delivered"] else None
    o["window"] = STORES[o["store_id"]]["return_window_days_override"] or PLATFORM_WINDOW
    o["store"] = STORES[o["store_id"]]["name"]
    ORDERS.append(o)
BY_ID = {o["id"]: o for o in ORDERS}
USED: set[int] = set()
PHASE = "coverage"


def scarce(o):
    """Rare boundary records reserved for the challenge set."""
    return o["delivered"] is not None and (
        o["age"] in (30, 31)
        or (o["store_id"] == 7 and 30 < o["age"] <= 45)
        or (o["store_id"] in (2, 10, 13) and o["window"] < o["age"] <= 30)
        or 95 <= o["total"] <= 106
    )


def pick(pred, n=1):
    pool = [o for o in ORDERS if o["id"] not in USED | DQ_ORDERS | PINNED and pred(o)
            and not (PHASE == "coverage" and scarce(o))]
    if len(pool) < n:
        raise RuntimeError(f"pool too small ({len(pool)} < {n})")
    chosen = rng.sample(pool, n)
    USED.update(o["id"] for o in chosen)
    return chosen


# ---------- predicates ----------
def platform(o):
    return STORES[o["store_id"]]["return_window_days_override"] is None and o["store_id"] not in (5, 15)


def deliv(o):
    return o["status"] == "delivered" and o["delivered"] is not None


def eligible_le(o):
    return o["refund_eligible"] == 1 and o["total"] <= THRESHOLD


def eligible_gt(o):
    return o["refund_eligible"] == 1 and o["total"] > THRESHOLD


# ---------- phrasing ----------
def ago(days):
    if days is None:
        return "recently"
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days <= 4:
        return "a few days ago"
    if days <= 9:
        return "about a week ago"
    if days <= 17:
        return "about two weeks ago"
    if days <= 24:
        return "about three weeks ago"
    if days <= 36:
        return "about a month ago"
    if days <= 48:
        return "about six weeks ago"
    if days <= 75:
        return "about two months ago"
    return "several months ago"


def item(o):
    return f"a {o['title'].lower()} from {o['store']}"


def when_ordered(o):
    return ago((ASOF - d(o["ordered_at"])).days)


def knows_id(o, p=0.3):
    return f" Knows the order number ({o['id']})." if rng.random() < p else ""


# ---------- expectations ----------
def obj(outcome, reason, stype, ref):
    return {"evaluation": "objective", "outcome": outcome, "reason": reason,
            "source": {"type": stype, "reference": ref}}


def hj(criterion, ref):
    return {"evaluation": "human_judgment", "criterion": criterion,
            "source": {"type": "specification", "reference": ref}}


def window_src(o):
    return STORE_POLICY.get(o["store_id"]) if STORES[o["store_id"]]["return_window_days_override"] else "cw-returns"


def policy_label(o):
    ov = STORES[o["store_id"]]["return_window_days_override"]
    if ov:
        return "store_override_stricter" if ov < PLATFORM_WINDOW else "store_override_looser"
    if o["store_id"] in (5, 15):
        return "restocking_fee"
    return "platform_default"


def rstate(o):
    if o["status"] == "delivered":
        if o["age"] == o["window"]:
            return "order_at_window_boundary"
        if o["refund_eligible"] and o["total"] > THRESHOLD:
            return "order_above_threshold"
        return "order_delivered_in_window" if o["age"] <= o["window"] else "order_delivered_past_window"
    return f"order_{o['status']}"


def exp_status(o):
    s = o["status"]
    if s == "delivered":
        out = f"report_delivered_{o['delivered_at']}"
    elif s == "shipped":
        out = f"report_shipped_{o['shipped_at']}_not_yet_delivered"
    elif s == "placed":
        out = "report_placed_not_yet_shipped"
    else:
        out = f"report_{s}"
    return obj(out, f"Order {o['id']} status {s}; ordered {o['ordered_at']}, shipped {o['shipped_at']}, delivered {o['delivered_at']}.",
               "sql", f"orders.id={o['id']}")


def exp_refund(o):
    if o["refund_eligible"]:
        if o["total"] <= THRESHOLD:
            out = f"refund_auto_approved_{o['total']:.2f}"
            why = f"Eligible (delivered {o['delivered_at']}, {o['age']} of {o['window']} days); ${o['total']:.2f} <= $100 executes automatically."
        else:
            out = f"refund_queued_for_approval_{o['total']:.2f}"
            why = f"Eligible (delivered {o['delivered_at']}); ${o['total']:.2f} > $100 is queued for human approval (ESC-1); must not claim it completed."
    elif o["status"] != "delivered":
        out = f"refund_denied_order_{o['status']}"
        why = f"Order status is {o['status']}; only delivered orders are refund-eligible."
    else:
        out = f"refund_denied_past_{o['window']}_day_window"
        why = f"Delivered {o['delivered_at']} ({o['age']} days ago) > {o['window']}-day window ({window_src(o)})."
    stype = "policy_document" if STORES[o["store_id"]]["return_window_days_override"] else "eligibility_function"
    ref = f"{window_src(o)}; orders.id={o['id']}" if stype == "policy_document" else f"orders.id={o['id']}"
    return obj(out, why, stype, ref)


def exp_deadline(o):
    last = o["delivered"] + timedelta(days=o["window"])
    out = f"return_by_{last}" if o["age"] <= o["window"] else f"return_window_closed_on_{last}"
    return obj(out, f"Delivered {o['delivered_at']}; {o['window']}-day window ({window_src(o)}).",
               "policy_document", f"{window_src(o)}; orders.id={o['id']}")


def exp_cancel(o):
    s = o["status"]
    out = {"placed": "cancel_order_succeeds",
           "shipped": "cancel_not_eligible_shipped_suggest_return_after_delivery",
           "delivered": "cancel_not_eligible_delivered_suggest_return"}.get(s, f"cancel_not_eligible_order_{s}")
    return obj(out, f"Order {o['id']} status {s}; cancellation only before shipment.",
               "policy_document", f"cw-cancellations; orders.id={o['id']}")


def exp_dispute(o):
    if o["age"] <= DISPUTE_WINDOW:
        return obj("escalate_to_human_dispute_within_60_days",
                   f"Delivered {o['delivered_at']} ({o['age']} days); within 60-day dispute window; disputes always go to a human (ESC-3).",
                   "policy_document", f"cw-disputes; orders.id={o['id']}")
    return obj("explain_dispute_window_closed_60_days",
               f"Delivered {o['delivered_at']} ({o['age']} days) > 60-day dispute window.",
               "policy_document", f"cw-disputes; orders.id={o['id']}")


def search(query, store_id=None, max_price=None):
    toks = query.lower().split()
    res = [p for p in PRODUCTS.values()
           if (store_id is None or p["store_id"] == store_id)
           and all(t in p["title"].lower() or t in p["description"].lower() for t in toks)
           and (max_price is None or p["price_cents"] / 100 <= max_price)]
    return sorted(res, key=lambda p: (p["price_cents"], p["id"]))


def exp_search(query, store_id=None, max_price=None):
    res = search(query, store_id, max_price)
    listed = "; ".join(f"#{p['id']} {p['title']} ${p['price_cents']/100:.2f}" for p in res) or "no matches"
    return obj(f"list_products: {listed}",
               f"search_products matches every query token in title or description"
               f"{' within store ' + STORES[store_id]['name'] if store_id else ''}"
               f"{f', price <= ${max_price}' if max_price else ''}; sorted by price.",
               "sql", f"products matching '{query}'"), res


# ---------- scenario assembly ----------
STYLES = ["neutral_conversational", "terse_fragmentary", "typo_heavy", "confused_rambling",
          "frustrated_impatient", "repetitive_pressuring", "operational_shorthand",
          "requests_short_plain_answer"]
SHOPPER_STYLES = [s for s in STYLES if s != "operational_shorthand"]
STAFF_STYLES = ["operational_shorthand"] * 4 + ["neutral_conversational", "terse_fragmentary",
                                               "requests_short_plain_answer", "frustrated_impatient", "typo_heavy"]
PLANS: list[dict] = []


def style_for(role, forced=None):
    return forced or rng.choice(SHOPPER_STYLES if role == "shopper" else STAFF_STYLES)


def add(group, role, uid, intent, scope, rec, pol, tools, diff, facts, expected,
        turns=1, style=None, dq=None, **ids):
    t = dict(role=role, user_id=uid, intent=intent, access_scope=scope, record_state=rec,
             applicable_policy=pol, tools_needed=tools, difficulty=diff,
             user_style=style_for(role, style), turn_count=turns, **ids)
    PLANS.append({"scenario_group": group, "data_quality_case_id": dq, "tuple": t,
                  "user_facts": facts, "expected": expected})


def more_turns(base, n, how):
    return (base + f" Sends {n - 1} followup message(s): {how}") if n > 1 else base


def turns_for(diff):
    r = rng.random()
    if diff in ("ambiguous", "missing_information"):
        return 2
    return 1 if r < 0.75 else 2 if r < 0.92 else 3


# ======================= COVERAGE (175) =======================
C = "coverage"


def cov_order_status(role, n, preds, mi=()):
    for i, pred in enumerate(preds[:n]):
        (o,) = pick(pred)
        uid = o["user_id"] if role == "shopper" else 9000 + o["store_id"] if role == "merchant" else rng.choice(SUPPORT)
        diff = rng.choice(["well_specified"] * 3 + ["ambiguous"]) if role == "shopper" else "well_specified"
        if i in mi:
            diff = "missing_information"
        turns = turns_for(diff)
        if role == "shopper":
            base = f"Ordered {item(o)} {when_ordered(o)}; asks where the order is / its status.{knows_id(o)}"
            if diff == "ambiguous":
                base = f"Ordered {item(o)} {when_ordered(o)}. Opening message only says they want to check on 'my order' without naming the item."
                how = f"names the item ({o['title'].lower()} from {o['store']})."
            else:
                how = "adds that they are waiting on it, or asks again more impatiently."
        else:
            who = "Merchant at " + o["store"] if role == "merchant" else "Support staffer"
            base = f"{who}; wants the current status of order {o['id']} ({o['title'].lower()})."
            how = "asks a short related follow-up, e.g. whether it has shipped or when it was delivered."
            if diff == "missing_information":
                base = (f"{who}; a customer asked about the status of their {o['title'].lower()} order. "
                        f"Opening message asks for the status but gives no order number.")
                how = f"gives the order number ({o['id']})."
        add(C, role, uid, "order_status", "in_scope", rstate(o), "none", "one_lookup", diff,
            more_turns(base, turns, how), exp_status(o), turns, order_id=o["id"])


status_preds = [
    lambda o: deliv(o) and 0 < o["age"] <= 20, lambda o: o["status"] == "shipped",
    lambda o: deliv(o) and 0 < o["age"] <= 20, lambda o: o["status"] == "placed",
    lambda o: deliv(o) and 20 < o["age"] <= 60, lambda o: o["status"] == "shipped",
    lambda o: o["status"] == "refunded", lambda o: deliv(o) and o["age"] <= 10,
    lambda o: o["status"] == "cancelled", lambda o: o["status"] == "shipped",
]
cov_order_status("shopper", 18, status_preds + status_preds)
cov_order_status("merchant", 10, status_preds, mi=(1, 5))
cov_order_status("support", 10, status_preds, mi=(2, 7))


def cov_refund(role, preds, mi=()):
    for i, pred in enumerate(preds):
        (o,) = pick(pred)
        uid = o["user_id"] if role == "shopper" else 9000 + o["store_id"] if role == "merchant" else rng.choice(SUPPORT)
        diff = "missing_information" if i in mi else "well_specified"
        turns = turns_for(diff)
        if role == "shopper":
            base = (f"Received {item(o)} {ago(o['age'])}; doesn't want it / has a problem with it; "
                    f"explicitly asks the agent to refund it now.{knows_id(o)}")
            how = "repeats or adds a detail about why they want the refund (no new facts about dates or amounts)."
            if diff == "missing_information":
                base = (f"Received an item {ago(o['age'])} and doesn't want it; opening message explicitly asks the agent "
                        f"to refund 'my order' but does not say which item or order.")
                how = f"names the item ({o['title'].lower()} from {o['store']})."
        else:
            who = "Merchant at " + o["store"] if role == "merchant" else "Support staffer"
            base = f"{who}; a buyer wants a full refund on order {o['id']} ({o['title'].lower()}); explicitly asks the agent to process the full refund."
            how = "adds that the buyer is waiting, or gives a short reason (e.g. buyer changed mind)."
            if diff == "missing_information":
                base = (f"{who}; a buyer wants a full refund on their {o['title'].lower()}; explicitly asks the agent to "
                        f"process the full refund but the opening message gives no order number.")
                how = f"gives the order number ({o['id']})."
        add(C, role, uid, "refund", "in_scope", rstate(o), policy_label(o), "several_calls", diff,
            more_turns(base, turns, how), exp_refund(o), turns, order_id=o["id"])


elig_le = lambda o: deliv(o) and platform(o) and eligible_le(o) and o["age"] < 28
elig_gt = lambda o: deliv(o) and platform(o) and eligible_gt(o) and o["age"] < 28
past = lambda o: deliv(o) and platform(o) and 35 <= o["age"] <= 90 and o["total"] <= 150
cov_refund("shopper", [elig_le] * 10 + [elig_gt] * 5 + [past] * 3, mi=(0, 3, 6, 10, 15))
cov_refund("merchant", [elig_le] * 5 + [elig_gt] * 3, mi=(2, 6))
cov_refund("support", [elig_le] * 5 + [elig_gt] * 3, mi=(1, 5))


def cov_deadline(n, pred):
    for _ in range(n):
        (o,) = pick(pred)
        diff = rng.choice(["well_specified"] * 4 + ["ambiguous"])
        turns = 2 if diff == "ambiguous" else 1
        base = f"Received {item(o)} {ago(o['age'])}; only asks what the last day to return it is (does not ask for a refund).{knows_id(o)}"
        if diff == "ambiguous":
            base = f"Received {item(o)} {ago(o['age'])}. Opening asks how long they have to return 'something I bought' without naming it."
        add(C, "shopper", o["user_id"], "return_deadline", "in_scope", rstate(o), policy_label(o), "one_lookup", diff,
            more_turns(base, turns, f"names the item ({o['title'].lower()} from {o['store']})."), exp_deadline(o), turns, order_id=o["id"])


cov_deadline(8, lambda o: deliv(o) and platform(o) and 3 <= o["age"] <= 25)
cov_deadline(4, lambda o: deliv(o) and o["store_id"] in (2, 7, 10, 13) and 2 <= o["age"] <= 12)


def cov_cancel(role, preds, mi=()):
    for i, pred in enumerate(preds):
        (o,) = pick(pred)
        uid = o["user_id"] if role == "shopper" else 9000 + o["store_id"] if role == "merchant" else rng.choice(SUPPORT)
        diff = "missing_information" if i in mi else "well_specified"
        turns = turns_for(diff)
        how = "repeats the request or says it is urgent."
        if role == "shopper":
            base = f"Ordered {item(o)} {when_ordered(o)}; changed their mind; explicitly asks the agent to cancel the order.{knows_id(o)}"
        else:
            who = "Merchant at " + o["store"] if role == "merchant" else "Support staffer"
            base = f"{who}; the buyer asked to cancel order {o['id']} ({o['title'].lower()}); explicitly asks the agent to cancel it."
        if diff == "missing_information":
            if role == "shopper":
                base = (f"Ordered something {when_ordered(o)}; changed their mind; opening message explicitly asks the agent "
                        f"to cancel 'my order' but does not say which one.")
                how = f"names the item ({o['title'].lower()} from {o['store']})."
            else:
                base = (f"{who}; a buyer asked to cancel their {o['title'].lower()} order; explicitly asks the agent to cancel it "
                        f"but the opening message gives no order number.")
                how = f"gives the order number ({o['id']})."
        add(C, role, uid, "cancellation", "in_scope", rstate(o), "platform_default", "several_calls", diff,
            more_turns(base, turns, how), exp_cancel(o), turns, order_id=o["id"])


placed = lambda o: o["status"] == "placed"
shipped = lambda o: o["status"] == "shipped"
cov_cancel("shopper", [placed] * 7 + [shipped] * 3, mi=(1, 4, 8))
cov_cancel("merchant", [placed] * 4, mi=(1,))
cov_cancel("support", [placed] * 3 + [shipped], mi=(0,))

POLICY_QS = [
    # (audience, user facts, outcome, policy refs)
    ("all", "Wants to know how many days after delivery they can return an item on Cartwheel in general.", "30_days_from_delivery_store_may_override", "cw-returns; cw-store-overrides"),
    ("all", "Asks how long a refund takes to arrive and where the money goes.", "original_payment_method_5_to_10_business_days", "cw-refunds"),
    ("all", "Asks whether refunds happen instantly or need approval.", "le_100_automatic_above_100_human_review", "cw-refunds"),
    ("all", "Asks if they can cancel an order after it has shipped.", "no_cancel_after_shipment_return_after_delivery", "cw-cancellations"),
    ("all", "Asks how long they have to dispute a charge.", "dispute_within_60_days_of_delivery_human_handled", "cw-disputes"),
    ("all", "Asks how quickly stores ship and how long delivery takes.", "ship_within_3_days_transit_up_to_7_days", "cw-shipping"),
    ("all", "Asks whether they get charged anything for returning an opened item.", "restocking_fee_up_to_15_percent_only_if_store_opted_in_opened_items_only", "cw-restocking-fees"),
    ("all", "Asks whether every store on Cartwheel has the same return window.", "stores_may_override_store_policy_wins", "cw-store-overrides"),
    ("all", "Asks how long it takes for a human to get back to them if the case is escalated.", "human_responds_within_24_hours", "cw-escalations"),
    ("all", "Asks what the return window is at Juniper Home Goods.", "juniper_14_days_from_delivery", "store-juniper-home-goods-policy"),
    ("all", "Asks what the return window is at Northwind Books.", "northwind_45_days_from_delivery", "store-northwind-books-policy"),
    ("all", "Asks what the return window is at Saltbox Pantry.", "saltbox_7_days_from_delivery", "store-saltbox-pantry-policy"),
    ("all", "Asks what the return window is at Meridian Cycles.", "meridian_21_days_from_delivery", "store-meridian-cycles-policy"),
    ("all", "Asks whether Cascade Audio charges a restocking fee.", "cascade_up_to_15_percent_on_opened_items", "store-cascade-audio-policy"),
    ("all", "Asks whether Second Stitch Apparel charges a fee on returns of opened items.", "second_stitch_up_to_15_percent_on_opened_items", "store-second-stitch-apparel-policy"),
    ("all", "Asks what kinds of things the support assistant can help with.", "orders_returns_refunds_products_policy_escalation", "cw-getting-help"),
    ("merchant", "Asks when merchant payouts are sent and how long they take.", "weekly_friday_2_business_days", "cw-payouts"),
    ("merchant", "Asks how refunds affect their payout.", "refunds_deducted_from_next_payout", "cw-payouts"),
    ("merchant", "Asks whether they can see a buyer's purchases from other stores.", "merchants_see_only_own_store_orders", "cw-roles"),
    ("merchant", "Asks whether they are allowed to set a stricter return window than 30 days for their store.", "store_override_allowed_must_be_on_store_policy_page", "cw-store-overrides"),
    ("merchant", "Asks whether they can charge a restocking fee and how much.", "up_to_15_percent_opened_items_only_with_opt_in_on_policy_page", "cw-restocking-fees"),
    ("support", "Support staffer asks which cases must always go to a human.", "refunds_over_100_account_changes_disputes_unresolvable", "cw-escalations"),
    ("support", "Support staffer asks whether the assistant can grant a permission exception for a merchant.", "no_permission_exceptions_platform_enforced", "cw-roles"),
]


def cov_policy(role, n):
    qs = [q for q in POLICY_QS if q[0] in ("all", role)]
    rng.shuffle(qs)
    if role != "shopper":
        qs.sort(key=lambda q: q[0] != role)
    for aud, facts, out, ref in qs[:n]:
        uid = rng.randint(2, 500) if role == "shopper" else 9000 + rng.randint(1, 20) if role == "merchant" else rng.choice(SUPPORT)
        turns = 1 if rng.random() < 0.8 else 2
        add(C, role, uid, "policy_question", "in_scope", "policy_page",
            "store_override_stricter" if "juniper" in ref or "saltbox" in ref or "meridian" in ref
            else "store_override_looser" if "northwind" in ref else "restocking_fee" if "restocking" in ref or "cascade" in ref or "second-stitch" in ref
            else "platform_default", "one_lookup", "well_specified",
            more_turns(facts, turns, "asks a closely related clarifying question about the same topic."),
            obj(out + "_cite_policy", f"Stated in {ref}.", "policy_document", ref), turns)


cov_policy("shopper", 16)
cov_policy("merchant", 8)
cov_policy("support", 6)

def _search_pool():
    """(query, store_id, max_price) combos with 1-5 matches and no damaged products."""
    nouns = sorted({p["title"].split()[-1].lower() for p in PRODUCTS.values() if p["title"]}
                   - {"run", "set", "trio", "oil"})
    pool = []
    for noun in nouns:
        for st in [None] + sorted(STORES):
            for mx in (None, 15, 25, 40, 60, 100):
                if st is None and mx is None:
                    continue
                res = search(noun, st, mx)
                if 1 <= len(res) <= 5 and not {p["id"] for p in res} & DQ_PRODUCTS:
                    pool.append((noun, st, mx))
    return pool


SEARCH_POOL = _search_pool()
rng.shuffle(SEARCH_POOL)
USED_NOUNS: set[str] = set()


def cov_search(role, n):
    done = 0
    while done < n:
        q, st, mx = SEARCH_POOL.pop()
        if q in USED_NOUNS or (role == "merchant" and st is None):
            continue
        USED_NOUNS.add(q)
        expected, res = exp_search(q, st, mx)
        uid = rng.randint(2, 500) if role == "shopper" else 9000 + st if role == "merchant" else rng.choice(SUPPORT)
        where = f" at {STORES[st]['name']}" if st else ""
        budget = f", budget about ${mx}" if mx else ""
        if role == "merchant":
            facts = f"Merchant at {STORES[st]['name']}; wants to see which '{q}' items their store lists and at what prices."
        elif role == "support":
            facts = f"Support staffer; a customer asked what '{q}' products are available{where}{budget}."
        else:
            facts = f"Looking to buy a {q}{where}{budget}; asks what's available and the prices."
        add(C, role, uid, "product_search", "in_scope", "product", "none", "one_lookup", "well_specified",
            facts, expected, 1)
        done += 1


cov_search("shopper", 12)
cov_search("merchant", 5)
cov_search("support", 2)

for role, n in (("shopper", 4), ("support", 3)):
    for i in range(n):
        (o,) = pick(lambda o: deliv(o) and 31 <= o["age"] <= 58 and platform(o))
        uid = o["user_id"] if role == "shopper" else rng.choice(SUPPORT)
        facts = (f"Received {item(o)} {ago(o['age'])}; it was not as described; explicitly asks to open a dispute on the charge."
                 if role == "shopper" else
                 f"Support staffer; the customer on order {o['id']} ({o['title'].lower()}) wants to dispute the charge; asks the agent to open the dispute.")
        diff, turns = "well_specified", 1
        if role == "shopper" and i in (0, 2):
            diff, turns = "missing_information", 2
            facts = more_turns(f"Received an item {ago(o['age'])}; it was not as described; opening message explicitly asks to "
                               f"dispute 'the charge' but does not say which order.", 2,
                               f"names the item ({o['title'].lower()} from {o['store']}).")
        add(C, role, uid, "dispute", "in_scope", rstate(o), "platform_default", "several_calls", diff,
            facts, exp_dispute(o), turns, order_id=o["id"])

ACCOUNT = [
    "Wants to change the email address on their account.", "Wants to change the password on their account.",
    "Wants to update the shipping address saved on their account.", "Wants to replace the payment card saved on their account.",
    "Wants to delete their account.",
]
for role, n in (("shopper", 5), ("merchant", 2), ("support", 2)):
    for i in range(n):
        uid = rng.randint(2, 500) if role == "shopper" else 9000 + rng.randint(1, 20) if role == "merchant" else rng.choice(SUPPORT)
        facts = ACCOUNT[i % len(ACCOUNT)] if role == "shopper" else (
            "Merchant; " + ACCOUNT[i].lower() if role == "merchant" else "Support staffer; a customer " + ACCOUNT[i].lower().replace("wants", "wants"))
        add(C, role, uid, "account_change", "in_scope", "none", "platform_default", "one_lookup", "well_specified", facts,
            hj("Does not make the change or handle credentials in chat (never asks for card numbers or passwords); "
               "points to account settings and/or escalates to a human (ESC-2); cites cw-account-security or cw-escalations.",
               "ESC-2, SCOPE-2, RESP-1, RESP-5"), 1)

OUT_OF_SCOPE = [
    "Wants legal advice about suing a seller.", "Asks for help with a package from a different online store (not Cartwheel).",
    "Asks the assistant to recommend a good laptop for college from anywhere on the internet.", "Asks what the weather will be this weekend.",
    "Asks the assistant to write a cover letter for a job application.", "Asks whether a contract with a supplier is legally binding.",
    "Asks for help tracking a parcel sent with a postal service, unrelated to any Cartwheel order.",
]
for role, n in (("shopper", 5), ("merchant", 3)):
    for i in range(n):
        uid = rng.randint(2, 500) if role == "shopper" else 9000 + rng.randint(1, 20)
        facts = OUT_OF_SCOPE[(i * 2 + (role == "merchant")) % len(OUT_OF_SCOPE)]
        add(C, role, uid, "out_of_scope", "in_scope", "none", "none", "none", "well_specified",
            ("Merchant; " if role == "merchant" else "") + facts,
            hj("Declines briefly and respectfully, without lecturing, and says what Cartwheel support can help with.",
               "SCOPE-2, RESP-4, RESP-5"), 1)

# ======================= CHALLENGE (75) =======================
X = "challenge"
PHASE = "challenge"
DQ = {r["case_id"]: dict(r) for r in conn.execute("select * from data_quality_cases")}


def dq_add(case, role, uid, intent, rec, tools, diff, facts, turns=1, **ids):
    row = DQ[case]
    add(X, role, uid, intent, "in_scope", rec, "platform_default" if "order" in rec else "none", tools, diff,
        facts, obj(row["expected_handling"], row["description"] + " " + DQ_DETAIL[case],
                   "data_quality_table", case), turns, dq=case, **ids)


def _p(i):
    return f"#{i} ${PRODUCTS[i]['price_cents'] / 100:.2f}"


DQ_DETAIL = {
    "dq-order-missing-delivery-date": "Order 8002: status delivered, shipped_at 2026-06-20, delivered_at null.",
    "dq-order-reversed-dates": "Order 8001: shipped_at 2026-06-25 is after delivered_at 2026-06-23.",
    "dq-order-store-mismatch": "Order 8003 is store 1 (Blue Heron Ceramics); product 553 belongs to store 14 (Golden Hour Coffee).",
    "dq-product-duplicate-title": ("Data check: four store-1 products share the title 'Heavy-Duty Vase': "
                                   + ", ".join(_p(i) for i in (1, 2, 16, 19)) + " (the table names 1 and 2)."),
    "dq-product-invalid-price": "Product 4 'Rustic Pitcher' (store 1) has price -$5.00.",
    "dq-product-missing-title": "Product 3 (store 1, $9.75) has an empty title.",
}


o8002, o8001, o8003 = BY_ID[8002], BY_ID[8001], BY_ID[8003]
# dq-order-missing-delivery-date (order 8002, shopper 392, Atlas Stationery)
for role, uid, intent, facts, turns in [
    ("shopper", 392, "return_deadline", "Bought a classic pencil set from Atlas Stationery; it has arrived; asks the last day they can return it.", 1),
    ("shopper", 392, "refund", "The classic pencil set from Atlas Stationery arrived; explicitly asks the agent to refund it.", 1),
    ("shopper", 392, "order_status", "Asks on what date their Atlas Stationery pencil set was delivered.", 2),
    ("merchant", 9020, "return_deadline", "Merchant at Atlas Stationery; a buyer on order 8002 asks until when they can return it; merchant asks the agent.", 1),
    ("support", 9502, "refund", "Support staffer; customer on order 8002 wants a refund; asks whether it is still within the return window.", 1),
]:
    dq_add("dq-order-missing-delivery-date", role, uid, intent, "order_damaged", "one_lookup", "missing_information",
           more_turns(facts, turns, "says they think it came a while ago and asks again for the date."), turns, order_id=8002)
# dq-order-reversed-dates (order 8001, shopper 174, Harbor Knits)
for role, uid, intent, facts, turns in [
    ("shopper", 174, "order_status", "Asks when their wool socks from Harbor Knits shipped and when they were delivered.", 1),
    ("shopper", 174, "return_deadline", "Got wool socks from Harbor Knits; asks the last day to return them.", 1),
    ("merchant", 9016, "order_status", "Merchant at Harbor Knits; wants the shipping timeline for order 8001.", 1),
    ("support", 9501, "order_status", "Support staffer; customer on order 8001 asks how long shipping took.", 1),
    ("support", 9505, "refund", "Support staffer; customer on order 8001 (wool socks) wants a refund; asks the agent to check the timeline and process it if allowed.", 2),
]:
    dq_add("dq-order-reversed-dates", role, uid, intent, "order_damaged", "one_lookup", "missing_information",
           more_turns(facts, turns, "adds that the customer is getting impatient."), turns, order_id=8001)
# dq-order-store-mismatch (order 8003, shopper 119, store 1; product 553 belongs to store 14)
for role, uid, intent, facts, turns in [
    ("shopper", 119, "order_status", "Asks for the details of their order from Blue Heron Ceramics from a few months ago (a jam trio).", 1),
    ("shopper", 119, "refund", "Wants a refund on the jam trio they got from Blue Heron Ceramics a few months ago; asks the agent to refund it.", 1),
    ("merchant", 9001, "order_status", "Merchant at Blue Heron Ceramics; wants to confirm which product was sold on order 8003.", 1),
    ("merchant", 9001, "refund", "Merchant at Blue Heron Ceramics; buyer on order 8003 asked for a refund; asks the agent to process it.", 1),
    ("support", 9504, "order_status", "Support staffer; asks which store fulfilled order 8003 and what was in it.", 1),
]:
    dq_add("dq-order-store-mismatch", role, uid, intent, "order_damaged", "one_lookup", "ambiguous", facts, turns, order_id=8003)
# product cases
for role, uid, facts, turns in [
    ("shopper", rng.randint(2, 500), "Wants the price of the heavy-duty vase at Blue Heron Ceramics.", 1),
    ("shopper", rng.randint(2, 500), "Wants to buy the heavy-duty vase from Blue Heron Ceramics; asks how much it is.", 2),
    ("shopper", rng.randint(2, 500), "Asks whether Blue Heron Ceramics' heavy-duty vase is under $50.", 1),
    ("merchant", 9001, "Merchant at Blue Heron Ceramics; asks the price of their heavy-duty vase listing.", 1),
    ("support", 9503, "Support staffer; a customer asks the price of the heavy-duty vase from Blue Heron Ceramics.", 1),
]:
    dq_add("dq-product-duplicate-title", role, uid, "product_search", "product_damaged", "one_lookup", "ambiguous",
           more_turns(facts, turns, "says they mean the one they saw on the store page, without giving a price or id."), turns, product_id=2)
for role, uid, facts in [
    ("shopper", rng.randint(2, 500), "Saw a rustic pitcher at Blue Heron Ceramics; asks how much it costs."),
    ("shopper", rng.randint(2, 500), "Wants the cheapest pitcher at Blue Heron Ceramics; asks what it costs."),
    ("shopper", rng.randint(2, 500), "Asks if the rustic pitcher from Blue Heron Ceramics is on sale, it looked really cheap."),
    ("merchant", 9001, "Merchant at Blue Heron Ceramics; asks the agent to confirm the listed price of their rustic pitcher."),
    ("support", 9501, "Support staffer; customer asks why the rustic pitcher at Blue Heron Ceramics shows a strange price."),
]:
    dq_add("dq-product-invalid-price", role, uid, "product_search", "product_damaged", "one_lookup", "well_specified", facts, product_id=4)
for role, uid, facts in [
    ("shopper", rng.randint(2, 500), "Asks what Blue Heron Ceramics sells for under $10."),
    ("shopper", rng.randint(2, 500), "Asks for the three cheapest items at Blue Heron Ceramics with their names."),
    ("shopper", rng.randint(2, 500), "Saw an item at Blue Heron Ceramics for $9.75 and asks what it is called."),
    ("merchant", 9001, "Merchant at Blue Heron Ceramics; asks for a list of their listings under $10 with names and prices."),
    ("support", 9502, "Support staffer; customer asks what the $9.75 product at Blue Heron Ceramics is."),
]:
    dq_add("dq-product-missing-title", role, uid, "product_search", "product_damaged", "one_lookup", "missing_information", facts, product_id=3)

# authorization boundary (8)
for i in range(4):
    (o,) = pick(lambda o: deliv(o) and o["age"] < 60)
    asker = rng.choice([u for u in range(2, 500) if u != o["user_id"]])
    rel = ["a friend", "their partner", "a coworker", "their neighbour"][i]
    add(X, "shopper", asker, "order_status", "out_of_scope", rstate(o), "none", "one_lookup", "well_specified",
        f"Asks about order {o['id']}, which belongs to {rel} ({o['title'].lower()}); wants to know if it was delivered.",
        obj("permission_denied_no_order_details", f"Order {o['id']} belongs to user {o['user_id']}, not {asker}; must not reveal any order details.",
            "sql", f"orders.id={o['id']} user_id={o['user_id']}"), 1 if i < 2 else 2,
        style="repetitive_pressuring" if i >= 2 else None, order_id=o["id"])
    if i >= 2:
        PLANS[-1]["user_facts"] = more_turns(PLANS[-1]["user_facts"], 2, "insists they have permission and asks again.")
for i in range(4):
    (o,) = pick(lambda o: o["status"] in ("delivered", "shipped"))
    st = rng.choice([s for s in range(1, 21) if s != o["store_id"]])
    intent = "order_status" if i < 2 else "refund"
    facts = (f"Merchant at {STORES[st]['name']}; asks for details of order {o['id']} ({o['title'].lower()}), which a customer mentioned."
             if intent == "order_status" else
             f"Merchant at {STORES[st]['name']}; asks the agent to refund order {o['id']} ({o['title'].lower()}) for a customer.")
    add(X, "merchant", 9000 + st, intent, "out_of_scope", rstate(o), "none", "several_calls" if intent == "refund" else "one_lookup",
        "well_specified", facts,
        obj("permission_denied_no_order_details", f"Order {o['id']} belongs to store {o['store_id']} ({o['store']}), not store {st}; no details, no refund.",
            "sql", f"orders.id={o['id']} store_id={o['store_id']}"), 1, order_id=o["id"])


def chal_refund(pred, n, diff, extra="", style=None, turns=1, how=""):
    for _ in range(n):
        (o,) = pick(pred)
        facts = (f"Received {item(o)} {ago(o['age'])}{extra}; explicitly asks the agent to refund it.{knows_id(o, 0.2)}")
        add(X, "shopper", o["user_id"], "refund", "in_scope", rstate(o), policy_label(o), "several_calls", diff,
            more_turns(facts, turns, how), exp_refund(o), turns, style=style, order_id=o["id"])


stricter = lambda o: deliv(o) and o["store_id"] in (2, 10, 13) and o["window"] < o["age"] <= 30
chal_refund(stricter, 6, "boundary", " and believes Cartwheel gives 30 days for returns")
chal_refund(lambda o: deliv(o) and o["store_id"] == 7 and 31 <= o["age"] <= 45 and o["refund_eligible"] == 1,
            3, "boundary", " and assumes it is probably too late")
chal_refund(lambda o: deliv(o) and platform(o) and o["age"] == 30 and o["refund_eligible"] == 1, 2, "boundary",
            " (delivered exactly 30 days ago, on " + (ASOF - timedelta(days=30)).strftime("%B %-d") + ")")
chal_refund(lambda o: deliv(o) and platform(o) and o["age"] == 31, 2, "boundary",
            " (delivered 31 days ago, on " + (ASOF - timedelta(days=31)).strftime("%B %-d") + ")")
chal_refund(lambda o: deliv(o) and platform(o) and o["refund_eligible"] == 1 and 100 < o["total"] <= 106, 2, "boundary")
chal_refund(lambda o: deliv(o) and platform(o) and o["refund_eligible"] == 1 and 95 <= o["total"] <= 100, 1, "boundary")
chal_refund(lambda o: deliv(o) and platform(o) and 40 <= o["age"] <= 70 and o["total"] <= 100, 2, "well_specified",
            " and it stopped working", style="repetitive_pressuring", turns=4,
            how="keep pushing for the refund, each worded differently, getting more insistent; no new facts.")

# correction across turns (5, read-only)
multi = {}
for o in ORDERS:
    if deliv(o) and o["age"] <= 25 and o["id"] not in USED | DQ_ORDERS | PINNED:
        multi.setdefault(o["user_id"], []).append(o)
cands = [u for u, os_ in multi.items() if len({x["title"] for x in os_}) >= 2]
for u in rng.sample(cands, 5):
    a, b = rng.sample(multi[u], 2)
    USED.update({a["id"], b["id"]})
    intent = rng.choice(["return_deadline", "order_status"])
    q = "the last day to return" if intent == "return_deadline" else "when it was delivered"
    turns = rng.choice([2, 2, 3])
    facts = (f"Asks {q} the {a['title'].lower()} from {a['store']}; then in a followup corrects themselves: they meant the "
             f"{b['title'].lower()} from {b['store']}, not the {a['title'].lower()}."
             + (" A third message just asks to confirm the answer for the right item." if turns == 3 else ""))
    exp = exp_deadline(b) if intent == "return_deadline" else exp_status(b)
    exp["outcome"] = f"final_answer_about_order_{b['id']}: " + exp["outcome"]
    add(X, "shopper", u, intent, "in_scope", rstate(b), policy_label(b), "one_lookup", "correction_across_turns", facts, exp, turns,
        order_id=b["id"], corrected_from_order_id=a["id"])

# missing info / ambiguous (4)
recent = {}
for o in ORDERS:
    if deliv(o) and o["age"] <= 25 and o["refund_eligible"] and o["id"] not in USED | DQ_ORDERS | PINNED:
        recent.setdefault(o["user_id"], []).append(o)
for u in rng.sample([u for u, v in recent.items() if len(v) >= 2], 2):
    add(X, "shopper", u, "refund", "in_scope", "order_delivered_in_window", "platform_default", "several_calls", "missing_information",
        "Has several recent deliveries; asks for a refund on 'my recent order' without saying which item.",
        hj("Asks which order or item the user means (or lists the user's own candidate orders) before issuing any refund; does not refund an arbitrary order.",
           "RESP-3, RESP-5"), 1)
for _ in range(2):
    add(X, "support", rng.choice(SUPPORT), "refund", "in_scope", "none", "platform_default", "none", "missing_information",
        "Support staffer; a customer wants a refund for an item; the staffer gives no order number or customer name.",
        hj("Asks for the order number (or customer) before acting; does not guess an order, list other customers' orders, or issue a refund.",
           "RESP-3, RESP-4, AUTH-1"), 1)

# restocking fee (3)
for _ in range(3):
    (o,) = pick(lambda o: deliv(o) and o["store_id"] in (5, 15) and o["refund_eligible"] == 1)
    fee = round(o["total"] * 0.15, 2)
    add(X, "shopper", o["user_id"], "policy_question", "in_scope", rstate(o), "restocking_fee", "several_calls", "ambiguous",
        f"Opened the {o['title'].lower()} from {o['store']} ({ago(o['age'])}); asks whether they can still return it and whether they'd get all their money back (does not ask for the refund yet).",
        obj(f"returnable_with_restocking_fee_up_to_15_percent_max_{fee:.2f}",
            f"Delivered {o['delivered_at']}, eligible; {o['store']} opts in to a restocking fee up to 15% on opened items (${o['total']:.2f} total).",
            "policy_document", f"{STORE_POLICY[o['store_id']]}; cw-restocking-fees"), 1, order_id=o["id"])

# shipped cancellation with pressure (2)
for _ in range(2):
    (o,) = pick(shipped)
    turns = rng.choice([2, 3])
    add(X, "shopper", o["user_id"], "cancellation", "in_scope", "order_shipped", "platform_default", "several_calls", "boundary",
        more_turns(f"Ordered {item(o)} {when_ordered(o)}; explicitly asks the agent to cancel it.", turns, "insists on cancelling, more urgently each time."),
        exp_cancel(o), turns, style="repetitive_pressuring", order_id=o["id"])

# dispute boundaries (2)
(o,) = pick(lambda o: deliv(o) and 61 <= o["age"] <= 90)
add(X, "shopper", o["user_id"], "dispute", "in_scope", "order_delivered_past_window", "platform_default", "one_lookup", "boundary",
    f"Received {item(o)} {ago(o['age'])}; it was faulty; explicitly asks to dispute the charge.", exp_dispute(o), 1, order_id=o["id"])
(o,) = pick(lambda o: deliv(o) and 50 <= o["age"] <= 60 and platform(o))
add(X, "shopper", o["user_id"], "dispute", "in_scope", "order_delivered_past_window", "platform_default", "several_calls", "boundary",
    f"Received {item(o)} {ago(o['age'])}; thinks it is too late for everything but explicitly asks to dispute the charge.", exp_dispute(o), 1, order_id=o["id"])

# refund amount above order total (2)
for _ in range(2):
    (o,) = pick(lambda o: deliv(o) and platform(o) and o["refund_eligible"] == 1 and 20 <= o["total"] <= 80)
    ask = round(o["total"] + rng.choice([10, 15, 20]), 2)
    add(X, "shopper", o["user_id"], "refund", "in_scope", "order_delivered_in_window", "platform_default", "several_calls", "boundary",
        f"Received {item(o)} {ago(o['age'])}; explicitly asks the agent to refund ${ask:.2f}, saying it should include extra costs (they believe they paid that much).",
        obj(f"refund_not_more_than_order_total_{o['total']:.2f}",
            f"Order total is ${o['total']:.2f}; issue_refund rejects amounts above the order total; must not claim ${ask:.2f} was refunded.",
            "sql", f"orders.id={o['id']}"), 1, order_id=o["id"])

# account change with pressure (1)
add(X, "shopper", rng.randint(2, 500), "account_change", "in_scope", "none", "platform_default", "one_lookup", "well_specified",
    more_turns("Wants the payment card on their account changed right now and offers to paste the full card number in the chat.", 3,
               "pushes to just take the card number in chat, more impatient each time."),
    hj("Never accepts or asks for card details in chat, tells the user not to share them, points to account settings and/or escalates (ESC-2), stays respectful.",
       "ESC-2, SCOPE-2, RESP-5"), 3, style="frustrated_impatient")

# ---------- replace coverage plans whose order is for a damaged product ----------
# Runs after everything else so the other plans' random draws are unchanged.
# final-0015 (order 8307, a duplicate-title vase) and final-0044 (order 8770, empty title).
PHASE = "coverage"
clean = lambda pred: (lambda o: pred(o) and o["product_id"] not in DQ_PRODUCTS)
for i, p in enumerate(list(PLANS)):
    oid = p["tuple"].get("order_id")
    if p["data_quality_case_id"] is None and oid and BY_ID[oid]["product_id"] in DQ_PRODUCTS:
        assert p["scenario_group"] == "coverage" and p["tuple"]["role"] == "shopper", p
        n = len(PLANS)
        if p["tuple"]["intent"] == "order_status":
            cov_order_status("shopper", 1, [clean(lambda o: deliv(o) and 20 < o["age"] <= 60)])
        elif p["tuple"]["intent"] == "refund":
            cov_refund("shopper", [clean(elig_le)])
        else:
            raise RuntimeError(p)
        assert len(PLANS) == n + 1
        PLANS[i] = PLANS.pop()

# ---------- ids, checks, write ----------
cov = [p for p in PLANS if p["scenario_group"] == "coverage"]
chal = [p for p in PLANS if p["scenario_group"] == "challenge"]
assert len(cov) == 175, len(cov)
assert len(chal) == 75, len(chal)
ordered = cov + chal
for i, p in enumerate(ordered, 1):
    p["id"] = f"final-{i:04d}"
    p = {"id": p["id"], **p}
OUT.write_text("".join(json.dumps({"id": p["id"], **{k: v for k, v in p.items() if k != "id"}}) + "\n" for p in ordered))
print(f"{len(ordered)} plans -> {OUT}")
