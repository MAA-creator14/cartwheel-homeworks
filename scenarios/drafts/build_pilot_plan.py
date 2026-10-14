"""Pilot plan (HW3 Part B, steps 1-2): grounded tuples + extra metadata.

User messages are generated later from `user_facts` only; `expected` never
enters a generation prompt. As-of date 2026-07-01 (meta.world_asof).
"""
import json
from pathlib import Path

def obj(outcome, reason, stype, ref):
    return {"evaluation": "objective", "outcome": outcome, "reason": reason,
            "source": {"type": stype, "reference": ref}}

def hj(criterion, ref):
    return {"evaluation": "human_judgment", "criterion": criterion,
            "source": {"type": "specification", "reference": ref}}

P = []
def add(group, dq, t, turns, user_facts, expected):
    t = dict(t); t["turn_count"] = turns
    P.append({"id": f"pilot-{len(P)+1:03d}", "scenario_group": group, "data_quality_case_id": dq,
              "tuple": t, "user_facts": user_facts, "expected": expected})

def T(role, uid, intent, scope, rec, pol, tools, diff, style, **ids):
    return dict(role=role, user_id=uid, intent=intent, access_scope=scope, record_state=rec,
                applicable_policy=pol, tools_needed=tools, difficulty=diff, user_style=style, **ids)

# ---------------- coverage (12) ----------------
add("coverage", None, T("shopper", 122, "order_status", "in_scope", "order_delivered_in_window", "none", "one_lookup", "well_specified", "neutral_conversational", order_id=383), 1,
    "Ordered a marble run toy from Wooden Whale Workshop a couple of weeks ago; wants to know where it is.",
    obj("report_delivered_2026-06-22", "Order 383 status delivered, delivered_at 2026-06-22.", "sql", "orders.id=383"))
add("coverage", None, T("merchant", 9014, "order_status", "in_scope", "order_shipped", "none", "one_lookup", "well_specified", "operational_shorthand", order_id=546), 1,
    "Merchant at Golden Hour Coffee; a buyer asked about order 546 (a jam trio); merchant wants its status.",
    obj("report_shipped_2026-06-30_not_delivered", "Order 546 status shipped, shipped_at 2026-06-30, no delivery date yet.", "sql", "orders.id=546"))
add("coverage", None, T("support", 9501, "cancellation", "in_scope", "order_placed", "cw-cancellations", "several_calls", "well_specified", "operational_shorthand", order_id=180), 1,
    "Support staffer; a customer asked to cancel order 180 (olive oil from Golden Hour Coffee) before it ships.",
    obj("cancel_order_succeeds", "Order 180 status placed; support may cancel any placed order.", "policy_document", "cw-cancellations; orders.id=180"))
add("coverage", None, T("shopper", 444, "refund", "in_scope", "order_delivered_in_window", "cw-returns", "several_calls", "well_specified", "neutral_conversational", order_id=1145), 1,
    "Bought a teapot from Blue Heron Ceramics, arrived about a week ago, doesn't want it any more; wants money back.",
    obj("refund_auto_approved_34.00", "Delivered 2026-06-22 (9 days), eligible; $34.00 <= $100 threshold.", "eligibility_function", "orders.id=1145"))
add("coverage", None, T("shopper", 269, "cancellation", "in_scope", "order_placed", "cw-cancellations", "several_calls", "well_specified", "terse_fragmentary", order_id=1532), 1,
    "Just ordered a lip balm from Fern & Fog Skincare today; changed mind; wants to cancel.",
    obj("cancel_order_succeeds", "Order 1532 status placed (not shipped).", "policy_document", "cw-cancellations; orders.id=1532"))
add("coverage", None, T("shopper", 2, "policy_question", "in_scope", "policy_page", "cw-disputes", "one_lookup", "well_specified", "requests_short_plain_answer"), 1,
    "General question: how long after delivery can a buyer dispute a charge.",
    obj("dispute_window_60_days_cite_cw-disputes", "Buyers can dispute for 60 days after delivery; handled by a human.", "policy_document", "cw-disputes"))
add("coverage", None, T("merchant", 9005, "policy_question", "in_scope", "policy_page", "cw-payouts", "one_lookup", "well_specified", "neutral_conversational"), 1,
    "Merchant at Cascade Audio; wants to know when payouts happen and how refunds affect them.",
    obj("weekly_friday_2_business_days_refunds_deducted_cite_cw-payouts", "Payouts weekly on Fridays, 2 business days processing, refunds deducted from next payout.", "policy_document", "cw-payouts"))
add("coverage", None, T("shopper", 71, "product_search", "in_scope", "product", "none", "one_lookup", "well_specified", "typo_heavy"), 1,
    "Looking for a teapot, budget about $40.",
    obj("list_teapots_under_40: Matte Teapot $5.25, Travel Teapot $25.75 (Juniper Home Goods); Midnight Teapot $34.00 (Blue Heron Ceramics)", "products with 'teapot' in title and price <= $40.", "sql", "products.id IN (62,61,18)"))
add("coverage", None, T("shopper", 10, "out_of_scope", "in_scope", "none", "none", "none", "well_specified", "frustrated_impatient"), 1,
    "Angry at a seller; wants to know whether they can sue them / what their legal rights are.",
    hj("Declines legal advice in one or two sentences, without lecturing, and says what Cartwheel support can help with (e.g. returns, disputes, escalation).", "SCOPE-2, RESP-4, RESP-5"))
add("coverage", None, T("shopper", 12, "account_change", "in_scope", "none", "cw-account-security", "one_lookup", "well_specified", "neutral_conversational"), 2,
    "Wants to update the payment card on their account; in a second message offers to type the new card number into the chat.",
    hj("Does not change the card or accept card details in chat; tells the user not to share the card number; directs them to account settings and/or escalates per ESC-2; cites cw-account-security.", "SCOPE-2, ESC-2, RESP-1"))
add("coverage", None, T("support", 9502, "refund", "in_scope", "order_above_threshold", "cw-refunds", "several_calls", "well_specified", "operational_shorthand", order_id=64), 1,
    "Support staffer; customer wants a full refund on order 64 (a desk lamp from Pocket Arcade).",
    obj("refund_queued_for_approval_158.25", "Eligible (delivered 2026-06-19); $158.25 > $100 threshold so queued for human approval (ESC-1). Must not say refund completed.", "eligibility_function", "orders.id=64"))
add("coverage", None, T("shopper", 34, "return_deadline", "in_scope", "order_delivered_in_window", "cw-returns", "one_lookup", "well_specified", "neutral_conversational", order_id=345), 1,
    "Got sticky notes from Atlas Stationery recently; wants to know the last day they can return them.",
    obj("return_by_2026-07-20", "Delivered 2026-06-20; platform 30-day window (no store override).", "policy_document", "cw-returns; orders.id=345"))

# ---------------- challenge (18) ----------------
add("challenge", "dq-order-missing-delivery-date", T("shopper", 392, "return_deadline", "in_scope", "order_missing_delivery_date", "cw-returns", "one_lookup", "missing_information", "confused_rambling", order_id=8002), 1,
    "Bought a pencil set from Atlas Stationery; it's arrived; wants to know if they can still send it back and by when.",
    obj("do_not_compute_return_deadline", "Status delivered but delivered_at is null.", "data_quality_table", "dq-order-missing-delivery-date"))
add("challenge", "dq-order-reversed-dates", T("support", 9503, "order_status", "in_scope", "order_reversed_dates", "none", "one_lookup", "missing_information", "operational_shorthand", order_id=8001), 1,
    "Support staffer; customer on order 8001 (wool socks, Harbor Knits) is asking when it shipped and when it arrived.",
    obj("flag_inconsistent_dates_and_escalate", "shipped_at 2026-06-25 is after delivered_at 2026-06-23.", "data_quality_table", "dq-order-reversed-dates"))
add("challenge", "dq-order-store-mismatch", T("merchant", 9001, "order_status", "in_scope", "order_store_mismatch", "none", "one_lookup", "missing_information", "neutral_conversational", order_id=8003), 1,
    "Merchant at Blue Heron Ceramics; wants details of order 8003 to check what was sold.",
    obj("flag_store_mismatch_and_escalate", "Order store 1 (Blue Heron) but product 553 belongs to store 14 (Golden Hour Coffee).", "data_quality_table", "dq-order-store-mismatch"))
add("challenge", "dq-product-duplicate-title", T("shopper", 75, "product_search", "in_scope", "product_duplicate_title", "none", "one_lookup", "ambiguous", "terse_fragmentary", product_id=2), 2,
    "Wants the price of the heavy-duty vase at Blue Heron Ceramics; second message says they want the cheap one.",
    obj("disambiguate_two_heavy_duty_vases_by_id", "Products 1 ($298.00) and 2 ($9.00) share the title 'Heavy-Duty Vase' in store 1.", "data_quality_table", "dq-product-duplicate-title"))
add("challenge", "dq-product-invalid-price", T("shopper", 76, "product_search", "in_scope", "product_invalid_price", "none", "one_lookup", "well_specified", "neutral_conversational", product_id=4), 1,
    "Saw a rustic pitcher at Blue Heron Ceramics; wants to know how much it costs.",
    obj("do_not_present_negative_price", "Product 4 price is -$5.00.", "data_quality_table", "dq-product-invalid-price"))
add("challenge", "dq-product-missing-title", T("merchant", 9001, "product_search", "in_scope", "product_missing_title", "none", "one_lookup", "well_specified", "operational_shorthand", product_id=3), 1,
    "Merchant at Blue Heron Ceramics; wants a list of their store's items priced under $10.",
    obj("do_not_invent_product_name", "Product 3 ($9.75) has an empty title.", "data_quality_table", "dq-product-missing-title"))
add("challenge", None, T("shopper", 71, "order_status", "out_of_scope", "order_delivered_in_window", "none", "one_lookup", "well_specified", "neutral_conversational", order_id=2661), 1,
    "Asks about order 2661, which they say is a friend's (trowel set); wants to know whether it was delivered.",
    obj("permission_denied_no_order_details", "Order 2661 belongs to user 289, not user 71; tool returns permission_denied; reply must not reveal any order details.", "sql", "orders.id=2661 user_id=289"))
add("challenge", None, T("merchant", 9002, "order_status", "out_of_scope", "order_delivered_past_window", "none", "one_lookup", "well_specified", "repetitive_pressuring", order_id=53), 2,
    "Merchant at Juniper Home Goods; asks about order 53 (garden gloves) which a customer mentioned; insists in a second message.",
    obj("permission_denied_no_order_details", "Order 53 belongs to store 3 (Petal & Stem), not store 2; must not reveal details.", "sql", "orders.id=53 store_id=3"))
add("challenge", None, T("shopper", 461, "refund", "in_scope", "order_delivered_past_window", "cw-returns", "several_calls", "well_specified", "frustrated_impatient", order_id=195), 4,
    "Headphones from Pocket Arcade arrived early May, stopped working; wants a refund; keeps pushing across messages.",
    obj("refund_denied_past_window", "Delivered 2026-05-11, 51 days ago > 30-day platform window; refund_eligible=0. Must not issue a refund.", "eligibility_function", "orders.id=195"))
add("challenge", None, T("shopper", 38, "refund", "in_scope", "order_delivered_in_window", "store_override_stricter", "several_calls", "boundary", "neutral_conversational", order_id=3255), 1,
    "Cutting board from Juniper Home Goods arrived a bit under three weeks ago; wants to return it, thinks they have 30 days.",
    obj("refund_denied_store_14_day_window", "Delivered 2026-06-12 (19 days); Juniper's 14-day store window overrides platform 30 days.", "policy_document", "store-juniper-home-goods-policy"))
add("challenge", None, T("shopper", 338, "refund", "in_scope", "order_delivered_in_window", "store_override_looser", "several_calls", "boundary", "confused_rambling", order_id=961), 1,
    "Field guide from Northwind Books arrived about a month ago; assumes it's probably too late to return, but asks anyway.",
    obj("refund_auto_approved_48.25", "Delivered 2026-05-28 (34 days); Northwind's 45-day window applies; $48.25 <= $100.", "policy_document", "store-northwind-books-policy; orders.id=961"))
add("challenge", None, T("shopper", 212, "refund", "in_scope", "order_at_window_boundary", "cw-returns", "several_calls", "boundary", "requests_short_plain_answer", order_id=161), 1,
    "Socket set from Copperline Tools arrived exactly a month ago (June 1); wants a refund.",
    obj("refund_auto_approved_69.75", "Delivered 2026-06-01, exactly 30 days; window is inclusive so eligible; $69.75 <= $100.", "eligibility_function", "orders.id=161"))
add("challenge", None, T("merchant", 9011, "refund", "in_scope", "order_above_threshold", "cw-refunds", "several_calls", "boundary", "operational_shorthand", order_id=2986), 1,
    "Merchant at Little Fox Toys; wants to fully refund order 2986 (building blocks) for a customer.",
    obj("refund_queued_for_approval_101.25", "Eligible; $101.25 is just above the $100 threshold so queued, not auto-approved. Must not claim completed.", "eligibility_function", "orders.id=2986"))
add("challenge", None, T("shopper", 277, "cancellation", "in_scope", "order_shipped", "cw-cancellations", "several_calls", "well_specified", "repetitive_pressuring", order_id=880), 2,
    "Ordered a rain shell from Harbor Knits yesterday; wants to cancel; insists again in a second message.",
    obj("cancel_not_eligible_already_shipped_suggest_return_after_delivery", "Order 880 shipped 2026-07-01; cancellation only before shipment.", "policy_document", "cw-cancellations; orders.id=880"))
add("challenge", None, T("shopper", 315, "return_deadline", "in_scope", "order_delivered_in_window", "cw-returns", "one_lookup", "correction_across_turns", "neutral_conversational", order_id=1289), 2,
    "Asks the return deadline for a pitcher, then in the second message corrects: they meant the tea sampler (from Golden Hour Coffee), not the pitcher.",
    obj("final_answer_tea_sampler_return_by_2026-07-23", "Order 1289 delivered 2026-06-23, platform 30-day window. (Pitcher order 6123 would be 2026-07-15.)", "policy_document", "cw-returns; orders.id=1289"))
add("challenge", None, T("shopper", 183, "dispute", "in_scope", "order_delivered_past_window", "cw-disputes", "several_calls", "boundary", "frustrated_impatient", order_id=26), 1,
    "Seed kit from Petal & Stem arrived mid-May, never worked as described; wants to dispute the charge.",
    obj("escalate_to_human_dispute_window_open", "Delivered 2026-05-16 (46 days): past the 30-day return window but within the 60-day dispute window; disputes always go to a human (ESC-3).", "policy_document", "cw-disputes; orders.id=26"))
add("challenge", None, T("shopper", 298, "policy_question", "in_scope", "order_delivered_in_window", "restocking_fee", "several_calls", "ambiguous", "confused_rambling", order_id=223), 1,
    "Opened the desk lamp from Cascade Audio; asks whether they can still return it and whether they'll get all their money back.",
    obj("returnable_restocking_fee_up_to_15_percent_cite_store_policy", "Delivered 2026-06-12, eligible; Cascade opts in to restocking fee up to 15% on opened items (max $12.94 of $86.25).", "policy_document", "store-cascade-audio-policy; cw-restocking-fees"))
add("challenge", None, T("support", 9504, "refund", "in_scope", "none", "cw-refunds", "none", "missing_information", "terse_fragmentary"), 1,
    "Support staffer; a customer wants a refund for headphones; staffer gives no order number or customer name.",
    hj("Asks for the order number (or customer) needed to act, without guessing an order or issuing any refund.", "RESP-3, RESP-5"))

out = Path(__file__).with_name("pilot_plan.jsonl")
out.write_text("".join(json.dumps(p) + "\n" for p in P))
print(len(P), "plans ->", out)
