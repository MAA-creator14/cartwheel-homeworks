"""Tests for list_customer_orders, the support-only tool added in Homework 1
(no LLM, no API keys)."""

from __future__ import annotations

from agent import db, tools
from agent.agent import TOOLS_BY_ROLE
from agent.auth import AuthContext

SHOPPER_1 = AuthContext(user_id=1, role="shopper")
MERCHANT_STORE_2 = AuthContext(user_id=9002, role="merchant", store_id=2)
SUPPORT = AuthContext(user_id=9501, role="support")


def test_support_lists_a_customers_orders_newest_first(world: dict) -> None:
    result = tools.list_customer_orders(SUPPORT, 1)
    assert result["ok"] is True
    assert result["customer_user_id"] == 1
    assert result["count"] == len(result["orders"]) == tools.DEFAULT_ORDER_LIMIT
    with db.connection() as conn:
        expected = [o.to_public_dict() for o in db.list_orders_for_user(conn, 1)]
    assert result["orders"] == expected
    assert {4127, 3980, 4455} <= {o["order_id"] for o in result["orders"]}


def test_reports_when_the_list_is_capped(world: dict) -> None:
    with db.connection() as conn:
        total = len(db.list_order_search_candidates(conn, user_id=1))
    assert total > tools.DEFAULT_ORDER_LIMIT  # shopper 1 has more than 20 orders
    result = tools.list_customer_orders(SUPPORT, 1)
    assert result["total_orders"] == total
    assert result["truncated"] is True


def test_short_history_is_not_truncated(world: dict) -> None:
    with db.connection() as conn:
        user_id = conn.execute(
            "SELECT u.id FROM users u JOIN orders o ON o.user_id = u.id "
            "WHERE u.role = 'shopper' GROUP BY u.id HAVING COUNT(*) < ? LIMIT 1",
            (tools.DEFAULT_ORDER_LIMIT,),
        ).fetchone()[0]
    result = tools.list_customer_orders(SUPPORT, user_id)
    assert result["ok"] is True
    assert result["count"] == result["total_orders"] < tools.DEFAULT_ORDER_LIMIT
    assert result["truncated"] is False


def test_shopper_and_merchant_are_denied(world: dict) -> None:
    for ctx in (SHOPPER_1, MERCHANT_STORE_2):
        result = tools.list_customer_orders(ctx, 1)
        assert result["ok"] is False
        assert result["error"] == "permission_denied"
        assert "orders" not in result


def test_unknown_user_is_not_found(world: dict) -> None:
    result = tools.list_customer_orders(SUPPORT, 999_999)
    assert result["ok"] is False
    assert result["error"] == "not_found"


def test_non_shopper_user_is_invalid_argument(world: dict) -> None:
    for staff_id in (9002, 9501):
        result = tools.list_customer_orders(SUPPORT, staff_id)
        assert result["ok"] is False
        assert result["error"] == "invalid_argument"


def test_registered_for_support_only() -> None:
    def names(role: str) -> set[str]:
        return {tool.name for tool in TOOLS_BY_ROLE[role]}

    assert "list_customer_orders" in names("support")
    assert "list_customer_orders" not in names("shopper")
    assert "list_customer_orders" not in names("merchant")
