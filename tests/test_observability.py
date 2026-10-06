"""Homework 2 authentication tests. Offline: no Langfuse, Docker, or model key."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from server import app as server_app


@pytest.fixture
def server(world: dict) -> object:
    server_app._SESSIONS.clear()
    return server_app


def test_create_session_rejects_role_that_differs_from_database(server) -> None:
    # User 9002 is a merchant in the seeded world; claiming shopper must fail.
    with pytest.raises(HTTPException) as exc:
        server.create_session(server.SessionCreate(user_id=9002, role="shopper"))

    assert exc.value.status_code == 403
    assert server._SESSIONS == {}


def test_token_for_one_session_cannot_authorize_another(server, monkeypatch) -> None:
    async def must_not_run(*args, **kwargs):
        raise AssertionError("agent ran for an unauthorized request")

    monkeypatch.setattr(server.Runner, "run", must_not_run)
    first = server.create_session(server.SessionCreate(user_id=1, role="shopper"))
    second = server.create_session(server.SessionCreate(user_id=9002, role="merchant"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            server.post_message(
                second["session_id"],
                server.MessageIn(message="Show my recent orders."),
                authorization=f"Bearer {first['token']}",
            )
        )

    assert exc.value.status_code == 403
