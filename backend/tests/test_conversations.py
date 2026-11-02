"""
Tests for Mustang AI Conversation Persistence, History, Truncation, Profiles, and Settings
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_conversation_crud_and_messages(async_client: AsyncClient):
    # 1. Create a conversation
    create_resp = await async_client.post(
        "/api/conversations",
        json={"title": "Test Chat", "model": "test-model.gguf", "system_prompt": "You are a test assistant.", "profile_id": "default"},
    )
    assert create_resp.status_code == 201
    conv_data = create_resp.json()
    conv_id = conv_data["id"]
    assert conv_data["title"] == "Test Chat"
    assert conv_data["message_count"] == 0

    # 2. Add messages
    msg1 = await async_client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "user", "content": "What is Python?"},
    )
    assert msg1.status_code == 201
    m1_id = msg1.json()["id"]

    msg2 = await async_client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "assistant", "content": "Python is a high-level programming language."},
    )
    assert msg2.status_code == 201
    m2_id = msg2.json()["id"]

    msg3 = await async_client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "user", "content": "Tell me more."},
    )
    assert msg3.status_code == 201
    m3_id = msg3.json()["id"]

    # 3. Retrieve conversation detail
    get_resp = await async_client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert len(detail["messages"]) == 3
    assert detail["messages"][0]["id"] == m1_id
    assert detail["messages"][1]["id"] == m2_id
    assert detail["messages"][2]["id"] == m3_id

    # 4. Truncate messages after m1 (non-inclusive should delete m2 and m3)
    trunc_resp = await async_client.post(
        f"/api/conversations/{conv_id}/truncate",
        json={"message_id": m1_id, "inclusive": False},
    )
    assert trunc_resp.status_code == 200

    after_trunc = await async_client.get(f"/api/conversations/{conv_id}")
    assert len(after_trunc.json()["messages"]) == 1
    assert after_trunc.json()["messages"][0]["id"] == m1_id

    # 5. Patch conversation title
    patch_resp = await async_client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "Renamed Chat Title"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["title"] == "Renamed Chat Title"

    # 6. Delete conversation
    del_resp = await async_client.delete(f"/api/conversations/{conv_id}")
    assert del_resp.status_code == 200

    get_deleted = await async_client.get(f"/api/conversations/{conv_id}")
    assert get_deleted.status_code == 404


@pytest.mark.asyncio
async def test_profiles_api(async_client: AsyncClient):
    # 1. List profiles (built-in profiles should be present)
    resp = await async_client.get("/api/profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    assert len(profiles) >= 4
    ids = [p["id"] for p in profiles]
    assert "default" in ids
    assert "coder" in ids
    assert "unrestricted" in ids

    # 2. Prevent deleting built-in profile
    del_builtin = await async_client.delete("/api/profiles/default")
    assert del_builtin.status_code == 400

    # 3. Create custom profile
    create_resp = await async_client.post(
        "/api/profiles",
        json={
            "name": "Custom Math Tutor",
            "description": "Explains math step-by-step",
            "system_prompt": "You are a patient math tutor.",
        },
    )
    assert create_resp.status_code == 201
    custom_id = create_resp.json()["id"]
    assert create_resp.json()["is_builtin"] is False

    # 4. Delete custom profile
    del_custom = await async_client.delete(f"/api/profiles/{custom_id}")
    assert del_custom.status_code == 200


@pytest.mark.asyncio
async def test_settings_api(async_client: AsyncClient):
    # 1. Get settings
    get_resp = await async_client.get("/api/settings")
    assert get_resp.status_code == 200
    cfg = get_resp.json()
    assert "temperature" in cfg
    assert "top_p" in cfg

    # 2. Update settings
    update_resp = await async_client.put(
        "/api/settings",
        json={
            "temperature": 0.85,
            "top_p": 0.95,
            "top_k": 50,
            "repeat_penalty": 1.15,
            "max_tokens": 1024,
            "context_size": 8192,
            "default_profile": "coder",
            "custom_system_prompt": "Always be concise.",
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["temperature"] == 0.85
    assert update_resp.json()["context_size"] == 8192


@pytest.mark.asyncio
async def test_chat_stream_conversation_persistence_and_regenerate(async_client: AsyncClient, mock_engine):
    from pathlib import Path
    from app.core.engine_base import EngineConfig

    await mock_engine.start(EngineConfig(model_path=Path("S:/Mustang AI/models/mock.gguf")))

    # 1. Create a conversation
    conv_resp = await async_client.post(
        "/api/conversations",
        json={"title": "New Chat", "model": "mock-model", "system_prompt": "Test system prompt"},
    )
    conv_id = conv_resp.json()["id"]

    # 2. Send prompt via /api/chat/completions
    async with async_client.stream(
        "POST",
        "/api/chat/completions",
        json={
            "conversation_id": conv_id,
            "messages": [{"role": "user", "content": "Explain quantum computing in one sentence."}],
        },
    ) as response:
        assert response.status_code == 200
        async for _ in response.aiter_lines():
            pass

    # 3. Verify that both user message and assistant response were persisted to DB
    conv_detail = (await async_client.get(f"/api/conversations/{conv_id}")).json()
    assert len(conv_detail["messages"]) == 2
    assert conv_detail["messages"][0]["role"] == "user"
    assert conv_detail["messages"][0]["content"] == "Explain quantum computing in one sentence."
    assert conv_detail["messages"][1]["role"] == "assistant"
    assert len(conv_detail["messages"][1]["content"]) > 0
    # Auto-titling should have updated "New Chat" to the user prompt preview
    assert conv_detail["title"].startswith("Explain quantum computing")

    # 4. Test Regenerate response
    async with async_client.stream(
        "POST",
        "/api/chat/regenerate",
        json={"conversation_id": conv_id, "temperature": 0.7},
    ) as regen_resp:
        assert regen_resp.status_code == 200
        async for _ in regen_resp.aiter_lines():
            pass

    # Verify conversation still has exactly 2 messages (old assistant replaced)
    regen_detail = (await async_client.get(f"/api/conversations/{conv_id}")).json()
    assert len(regen_detail["messages"]) == 2
    assert regen_detail["messages"][0]["role"] == "user"
    assert regen_detail["messages"][1]["role"] == "assistant"

