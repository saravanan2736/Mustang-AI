"""
Messages saved within one clock tick share created_at (time.time() advances in ~16 ms steps on
Windows with Python 3.11). Found in M4 Phase G as an intermittent failure of
test_conversation_crud_and_messages: truncate kept a reply it should have deleted, and an
inclusive truncate (regenerate) could also delete the user's prompt.
"""

import time

import pytest
from httpx import AsyncClient


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)


async def _conversation_with_three_messages(client: AsyncClient) -> tuple[str, list[str]]:
    conv_id = (await client.post("/api/conversations", json={"title": "Same tick"})).json()["id"]
    ids = []
    for role, content in [("user", "first"), ("assistant", "second"), ("user", "third")]:
        res = await client.post(f"/api/conversations/{conv_id}/messages", json={"role": role, "content": content})
        assert res.status_code == 201
        ids.append(res.json()["id"])
    return conv_id, ids


async def _message_ids(client: AsyncClient, conv_id: str) -> list[str]:
    return [m["id"] for m in (await client.get(f"/api/conversations/{conv_id}")).json()["messages"]]


@pytest.mark.asyncio
async def test_same_timestamp_messages_keep_insertion_order(async_client: AsyncClient, frozen_clock):
    conv_id, ids = await _conversation_with_three_messages(async_client)
    assert await _message_ids(async_client, conv_id) == ids


@pytest.mark.asyncio
async def test_truncate_after_message_with_same_timestamp(async_client: AsyncClient, frozen_clock):
    conv_id, ids = await _conversation_with_three_messages(async_client)
    res = await async_client.post(f"/api/conversations/{conv_id}/truncate", json={"message_id": ids[0], "inclusive": False})
    assert res.status_code == 200
    assert await _message_ids(async_client, conv_id) == ids[:1]


@pytest.mark.asyncio
async def test_inclusive_truncate_keeps_earlier_message_with_same_timestamp(async_client: AsyncClient, frozen_clock):
    conv_id, ids = await _conversation_with_three_messages(async_client)
    res = await async_client.post(f"/api/conversations/{conv_id}/truncate", json={"message_id": ids[1], "inclusive": True})
    assert res.status_code == 200
    assert await _message_ids(async_client, conv_id) == ids[:1]
