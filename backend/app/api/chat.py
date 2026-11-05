"""
Mustang AI Chat & SSE Streaming Router
Handles real-time token streaming with client disconnection, thread persistence,
conversation auto-titling, and response regeneration.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncIterator
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.changes import ChangeError, list_changes, undo_change
from app.agent.loop import DEFAULT_MAX_STEPS, cancel_all as cancel_agent_loops, resolve_approval, run_agent
from app.agent.registry import ToolContext
from app.agent.workspace import Workspace, WorkspaceError
from app.api.models import get_engine
from app.core import security
from app.core.engine_base import (
    ChatCompletionRequest,
    ChatMessage,
    EngineState,
    InferenceEngine,
    ToolCall,
)
from app.db.database import get_db

logger = logging.getLogger("mustang.chat")

def _describe(exc: BaseException) -> str:
    """An error as text the user can read: some exceptions (timeouts, dropped connections) have none."""
    return str(exc).strip() or f"{type(exc).__name__}: the model engine stopped responding. Try again."


router = APIRouter(prefix="/api/chat", tags=["chat"])


class AgentOptions(BaseModel):
    """Agent mode for one reply: the model may call the built-in read-only tools."""
    workspace: str | None = Field(default=None, max_length=1024)  # folder the file tools may read
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=60)
    auto_approve: bool = False  # the user chose "approve all changes in this chat" (the UI keeps it per chat)


class UIChatRequest(ChatCompletionRequest):
    agent: AgentOptions | None = None


class RegenerateRequest(BaseModel):
    conversation_id: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    top_k: int = Field(default=40, ge=1, le=200)
    max_tokens: int = Field(default=2048, ge=1, le=16384)
    repeat_penalty: float = Field(default=1.1, ge=0.0, le=2.0)
    agent: AgentOptions | None = None


def _agent_context(options: AgentOptions, conv_id: str) -> ToolContext:
    try:
        workspace = Workspace(options.workspace) if options.workspace else None
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Workspace: {e}")
    return ToolContext(workspace=workspace, conversation_id=conv_id)


def _history_message(row) -> ChatMessage:
    """A stored message as a chat turn, including agent-mode tool fields (schema v2)."""
    calls = None
    if row["tool_calls"]:
        try:
            calls = [ToolCall.model_validate(c) for c in json.loads(row["tool_calls"])]
        except (ValueError, TypeError):
            calls = None
    return ChatMessage(role=row["role"], content=row["content"], tool_calls=calls or None,
                       tool_call_id=row["tool_call_id"] if row["role"] == "tool" else None)


async def _insert_message(conv_id: str, role: str, content: str, *, tool_calls=None, tool_call_id=None,
                          token_count: int = 0, finish_reason: str | None = "stop") -> None:
    db = await get_db()
    try:
        now = time.time()
        await db.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, token_count, finish_reason, created_at, tool_calls, tool_call_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), conv_id, role, content, token_count, finish_reason, now,
             json.dumps([c.model_dump() for c in tool_calls]) if tool_calls else None, tool_call_id),
        )
        await db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))
        await db.commit()
    finally:
        await db.close()


async def _stream_agent_response(
    engine: InferenceEngine,
    chat_req: ChatCompletionRequest,
    ctx: ToolContext,
    options: AgentOptions,
    conv_id: str,
    request: Request,
) -> StreamingResponse:
    """
    Agent mode over SSE. Text arrives as usual (choices[0].delta.content); tool steps arrive as
    extra chunks with an "agent" object ({"event": "tool_call" | "tool_result" | "done", ...}),
    which a client without agent support ignores. Tool steps are saved as they happen; the final
    answer is saved like a normal reply.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    model_name = chat_req.model or "local-model"

    def frame(delta: dict, finish_reason=None, agent: dict | None = None) -> str:
        payload = {"id": completion_id, "created": created_ts, "model": model_name,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
        if agent is not None:
            payload["agent"] = agent
        return f"data: {json.dumps(payload)}\n\n"

    async def generator() -> AsyncIterator[str]:
        cancel = asyncio.Event()
        answer: list[str] = []
        saved_answer = False

        async def save_answer():
            nonlocal saved_answer
            if answer and not saved_answer:
                saved_answer = True
                await _insert_message(conv_id, "assistant", "".join(answer), token_count=len(answer))

        try:
            async for ev in run_agent(engine, chat_req, ctx, max_steps=options.max_steps, cancel=cancel,
                                     auto_approve=options.auto_approve):
                if await request.is_disconnected():
                    logger.info("Client disconnected during agent reply. Stopping.")
                    cancel.set()
                    await engine.cancel_generation()
                    break
                if ev.kind == "content":
                    answer.append(ev.text)
                    yield frame({"content": ev.text})
                elif ev.kind == "reasoning":
                    yield frame({"reasoning": ev.text})
                elif ev.kind == "assistant_turn":
                    # The text so far belonged to a tool-calling step, not the final answer.
                    answer.clear()
                    await _insert_message(conv_id, "assistant", ev.message.content, tool_calls=ev.message.tool_calls,
                                          finish_reason="tool_calls")
                elif ev.kind == "tool_turn":
                    await _insert_message(conv_id, "tool", ev.message.content, tool_call_id=ev.message.tool_call_id)
                elif ev.kind == "tool_call":
                    yield frame({}, agent={"event": "tool_call", "step": ev.step, "call_id": ev.call_id,
                                           "name": ev.name, "arguments": ev.arguments})
                elif ev.kind == "approval":
                    yield frame({}, agent={"event": "approval_required", "step": ev.step, "call_id": ev.call_id,
                                           "name": ev.name, "arguments": ev.arguments, "summary": ev.text,
                                           "diff": ev.extra.get("diff", ""), "auto": bool(ev.extra.get("auto"))})
                elif ev.kind == "tool_result":
                    yield frame({}, agent={"event": "tool_result", "step": ev.step, "call_id": ev.call_id,
                                           "name": ev.name, "ok": ev.ok, "content": ev.text[:2000]})
                elif ev.kind == "done":
                    await save_answer()
                    yield frame({}, finish_reason=ev.finish_reason,
                                agent={"event": "done", "step": ev.step, "reason": ev.reason})
            await save_answer()
            yield "data: [DONE]\n\n"
        except Exception as exc:
            logger.error(f"Agent streaming error: {_describe(exc)}", exc_info=True)
            err = {"id": completion_id, "error": _describe(exc), "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}]}
            yield f"data: {json.dumps(err)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if not saved_answer and answer:
                try:
                    await asyncio.shield(save_answer())
                except Exception as e:
                    logger.debug(f"Shielded persist note: {e}")

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


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
                            "delta": ({"content": chunk.delta_content, "reasoning": chunk.delta_reasoning}
                                      if chunk.delta_reasoning else {"content": chunk.delta_content}),
                            "finish_reason": chunk.finish_reason,
                        }
                    ],
                }
                yield f"data: {json.dumps(payload)}\n\n"

            # Save immediately before yielding [DONE] so client disconnect never interrupts persistence
            await _save_assistant_message()
            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.error(f"Streaming error: {_describe(exc)}", exc_info=True)
            err_payload = {
                "id": completion_id,
                "error": _describe(exc),
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
    chat_req: UIChatRequest,
    request: Request,
    engine: InferenceEngine = Depends(get_engine),
):
    """
    Streams LLM completion tokens over Server-Sent Events (SSE).
    Monitors client disconnect and aborts generation cleanly if connection drops.
    Persists user prompt and assistant stream to the active conversation in SQLite.
    """
    if chat_req.uses_tools:
        # Clients can't define tools or send tool turns here: agent mode ("agent") offers the
        # built-in tools only. OpenAI-compatible clients can bring their own tools on /v1.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom tools are not accepted on this endpoint; use 'agent' for the built-in tools, or /v1/chat/completions.",
        )

    status_obj = await engine.get_status()
    if status_obj.state != EngineState.READY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot generate tokens: Engine state is '{status_obj.state}'. Please load a model first.",
        )

    model_name = status_obj.model_name or "local-model"
    conv_id = chat_req.conversation_id or "default"
    now = time.time()
    agent_ctx = _agent_context(chat_req.agent, conv_id) if chat_req.agent else None

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
                "SELECT content FROM messages WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
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

    if agent_ctx is not None:
        engine_req = ChatCompletionRequest(**chat_req.model_dump(exclude={"agent"}))
        engine_req.model = model_name
        return await _stream_agent_response(engine, engine_req, agent_ctx, chat_req.agent, conv_id, request)
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
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC",
            (conv_id,),
        )
        if not msg_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot regenerate: conversation has no messages.",
            )

        # Delete the last reply: the assistant message and, in agent mode, its tool steps.
        removed = 0
        while msg_rows and msg_rows[-1]["role"] in ("assistant", "tool"):
            await db.execute("DELETE FROM messages WHERE id = ?", (msg_rows[-1]["id"],))
            msg_rows = msg_rows[:-1]
            removed += 1
        if removed:
            await db.commit()

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
            messages.append(_history_message(m))

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
    if regen_req.agent:
        chat_req.model = model_name
        agent_ctx = _agent_context(regen_req.agent, conv_id)
        return await _stream_agent_response(engine, chat_req, agent_ctx, regen_req.agent, conv_id, request)
    return await _stream_chat_response(engine, chat_req, conv_id, request, model_name)


class ApprovalDecision(BaseModel):
    call_id: str = Field(min_length=1, max_length=128)
    approved: bool
    approve_all: bool = False  # also approve the later changes of this reply


class UndoRequest(BaseModel):
    conversation_id: str
    call_id: str = Field(min_length=1, max_length=128)


async def _conversation_workspace(conv_id: str) -> Workspace | None:
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT workspace FROM conversations WHERE id = ?", (conv_id,))
    finally:
        await db.close()
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Conversation '{conv_id}' not found")
    if not rows[0]["workspace"]:
        return None
    try:
        return Workspace(rows[0]["workspace"])
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Workspace: {e}")


@router.post("/approve")
async def approve_change(decision: ApprovalDecision):
    """The user's answer to a file change proposed by agent mode (shown as a diff)."""
    if not resolve_approval(decision.call_id, decision.approved, decision.approve_all):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No change with that id is waiting for approval.")
    return {"call_id": decision.call_id, "approved": decision.approved}


@router.get("/changes")
async def get_changes(conversation_id: str):
    """File changes agent mode made in this chat's workspace, with whether Undo is possible."""
    ws = await _conversation_workspace(conversation_id)
    if ws is None:
        return []
    try:
        return await asyncio.to_thread(list_changes, ws, conversation_id)
    except ChangeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/undo")
async def undo_agent_change(req: UndoRequest):
    """Put back the previous version of a file agent mode changed (or remove a file it created),
    if the file is still exactly what the agent wrote."""
    ws = await _conversation_workspace(req.conversation_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This chat has no workspace folder.")
    try:
        entry = await asyncio.to_thread(undo_change, ws, req.call_id, req.conversation_id)
    except (ChangeError, WorkspaceError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return {"call_id": entry.call_id, "path": entry.path, "kind": entry.kind, "undone": True}


@router.post("/stop")
async def stop_generation(
    engine: InferenceEngine = Depends(get_engine),
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Halts active generation immediately in-flight.
    Authenticated with X-Mustang-Token.
    """
    cancel_agent_loops()
    success = await engine.cancel_generation()
    return {"status": "stopped", "success": success}
