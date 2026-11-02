"""
Mustang AI Chat & SSE Streaming Router
Handles real-time token streaming with client disconnection, thread persistence,
conversation auto-titling, and response regeneration.
"""

import json
import logging
import time
import uuid
from typing import AsyncIterator
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.models import get_engine
from app.core import security
from app.core.engine_base import (
    ChatCompletionRequest,
    ChatMessage,
    EngineState,
    InferenceEngine,
)
from app.db.database import get_db

logger = logging.getLogger("mustang.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


class RegenerateRequest(BaseModel):
    conversation_id: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    top_k: int = Field(default=40, ge=1, le=200)
    max_tokens: int = Field(default=2048, ge=1, le=16384)
    repeat_penalty: float = Field(default=1.1, ge=0.0, le=2.0)


async def _stream_chat_response(
    engine: InferenceEngine,
    chat_req: ChatCompletionRequest,
    conv_id: str,
    request: Request,
    model_name: str,
) -> StreamingResponse:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    full_response_text: list[str] = []

    async def sse_token_generator() -> AsyncIterator[str]:
        persisted = False

        async def _save_assistant_message():
            nonlocal persisted
            if full_response_text and not persisted:
                persisted = True
                try:
                    assembled = "".join(full_response_text)
                    db = await get_db()
                    msg_id = str(uuid.uuid4())
                    now = time.time()
                    await db.execute(
                        """
                        INSERT INTO messages (id, conversation_id, role, content, token_count, finish_reason, created_at)
                        VALUES (?, ?, 'assistant', ?, ?, 'stop', ?)
                        """,
                        (msg_id, conv_id, assembled, len(full_response_text), now),
                    )
                    await db.execute(
                        "UPDATE conversations SET updated_at = ? WHERE id = ?",
                        (now, conv_id),
                    )
                    await db.commit()
                    await db.close()
                    logger.info(f"Persisted assistant message {msg_id} ({len(full_response_text)} tokens) for conversation {conv_id}")
                except Exception as db_err:
                    logger.error(f"Persistence error: {db_err}")

        try:
            async for chunk in engine.stream_chat(chat_req):
                if await request.is_disconnected():
                    logger.info("Client disconnected during stream. Aborting generation.")
                    await engine.cancel_generation()
                    break

                if chunk.delta_content:
                    full_response_text.append(chunk.delta_content)

                payload = {
                    "id": completion_id,
                    "created": created_ts,
                    "model": chunk.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": chunk.delta_content},
                            "finish_reason": chunk.finish_reason,
                        }
                    ],
                }
                yield f"data: {json.dumps(payload)}\n\n"

            # Save immediately before yielding [DONE] so client disconnect never interrupts persistence
            await _save_assistant_message()
            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.error(f"Streaming error: {exc}")
            err_payload = {
                "id": completion_id,
                "error": str(exc),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
            }
            yield f"data: {json.dumps(err_payload)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # Shielded fallback in case generation was aborted or stopped mid-flight
            if not persisted and full_response_text:
                import asyncio
                try:
                    await asyncio.shield(_save_assistant_message())
                except Exception as cancel_err:
                    logger.debug(f"Shielded persist note: {cancel_err}")

    return StreamingResponse(
        sse_token_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/completions")
async def chat_completions(
    chat_req: ChatCompletionRequest,
    request: Request,
    engine: InferenceEngine = Depends(get_engine),
):
    """
    Streams LLM completion tokens over Server-Sent Events (SSE).
    Monitors client disconnect and aborts generation cleanly if connection drops.
    Persists user prompt and assistant stream to the active conversation in SQLite.
    """
    status_obj = await engine.get_status()
    if status_obj.state != EngineState.READY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot generate tokens: Engine state is '{status_obj.state}'. Please load a model first.",
        )

    model_name = status_obj.model_name or "local-model"
    conv_id = chat_req.conversation_id or "default"
    now = time.time()

    # Ensure conversation exists and store user turn if needed
    db = await get_db()
    try:
        conv_row = await db.execute_fetchall(
            "SELECT id, title FROM conversations WHERE id = ?", (conv_id,)
        )
        if not conv_row:
            initial_title = "New Chat"
            # If user message is available, generate title preview
            user_msgs = [m for m in chat_req.messages if m.role == "user"]
            if user_msgs:
                first_text = user_msgs[-1].content.strip().split("\n")[0]
                if len(first_text) > 38:
                    first_text = first_text[:35] + "..."
                if first_text:
                    initial_title = first_text

            await db.execute(
                """
                INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at)
                VALUES (?, ?, ?, '', ?, ?)
                """,
                (conv_id, initial_title, model_name, now, now),
            )
            await db.commit()
        else:
            # Check if title needs auto-titling from first user message
            title = conv_row[0]["title"]
            if title == "New Chat":
                user_msgs = [m for m in chat_req.messages if m.role == "user"]
                if user_msgs:
                    first_text = user_msgs[-1].content.strip().split("\n")[0]
                    if len(first_text) > 38:
                        first_text = first_text[:35] + "..."
                    if first_text:
                        await db.execute(
                            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                            (first_text, now, conv_id),
                        )
                        await db.commit()

        # Check if the latest message is a user message and not already recorded in DB
        if chat_req.messages and chat_req.messages[-1].role == "user":
            last_msg = chat_req.messages[-1]
            last_db_rows = await db.execute_fetchall(
                "SELECT content FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
                (conv_id,),
            )
            already_recorded = bool(last_db_rows and last_db_rows[0]["content"] == last_msg.content)
            if not already_recorded:
                user_msg_id = str(uuid.uuid4())
                await db.execute(
                    """
                    INSERT INTO messages (id, conversation_id, role, content, token_count, finish_reason, created_at)
                    VALUES (?, ?, 'user', ?, 0, 'stop', ?)
                    """,
                    (user_msg_id, conv_id, last_msg.content, now),
                )
                await db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conv_id),
                )
                await db.commit()
    finally:
        await db.close()

    return await _stream_chat_response(engine, chat_req, conv_id, request, model_name)


@router.post("/regenerate")
async def regenerate_response(
    regen_req: RegenerateRequest,
    request: Request,
    engine: InferenceEngine = Depends(get_engine),
):
    """
    Regenerates the last response in the specified conversation:
    - Removes the last assistant message from SQLite.
    - Assembles the prior conversation context.
    - Streams a fresh assistant response.
    """
    status_obj = await engine.get_status()
    if status_obj.state != EngineState.READY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot generate tokens: Engine state is '{status_obj.state}'. Please load a model first.",
        )

    conv_id = regen_req.conversation_id
    db = await get_db()
    try:
        conv_row = await db.execute_fetchall(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        )
        if not conv_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation '{conv_id}' not found",
            )
        conv = conv_row[0]

        # Get all messages
        msg_rows = await db.execute_fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        )
        if not msg_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot regenerate: conversation has no messages.",
            )

        # If the last message is an assistant message, delete it
        if msg_rows[-1]["role"] == "assistant":
            last_id = msg_rows[-1]["id"]
            await db.execute("DELETE FROM messages WHERE id = ?", (last_id,))
            await db.commit()
            msg_rows = msg_rows[:-1]

        if not msg_rows or msg_rows[-1]["role"] != "user":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot regenerate: no preceding user prompt found.",
            )

        # Assemble prompt history
        messages: list[ChatMessage] = []
        if conv["system_prompt"] and conv["system_prompt"].strip():
            messages.append(ChatMessage(role="system", content=conv["system_prompt"].strip()))

        for m in msg_rows:
            messages.append(ChatMessage(role=m["role"], content=m["content"]))

    finally:
        await db.close()

    chat_req = ChatCompletionRequest(
        conversation_id=conv_id,
        messages=messages,
        temperature=regen_req.temperature,
        top_p=regen_req.top_p,
        top_k=regen_req.top_k,
        max_tokens=regen_req.max_tokens,
        repeat_penalty=regen_req.repeat_penalty,
        stream=True,
    )

    model_name = status_obj.model_name or "local-model"
    return await _stream_chat_response(engine, chat_req, conv_id, request, model_name)


@router.post("/stop")
async def stop_generation(
    engine: InferenceEngine = Depends(get_engine),
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Halts active generation immediately in-flight.
    Authenticated with X-Mustang-Token.
    """
    success = await engine.cancel_generation()
    return {"status": "stopped", "success": success}
