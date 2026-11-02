"""
Mustang AI Conversations API
Thread management, persistent message storage, truncation, and history querying in SQLite.
"""

import logging
import time
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db.database import get_db

logger = logging.getLogger("mustang.conversations")
router = APIRouter(prefix="/api/conversations", tags=["conversations"])


# --- Schemas ---

class CreateConversationRequest(BaseModel):
    title: str | None = None
    model: str = ""
    system_prompt: str = ""
    profile_id: str = "default"


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    system_prompt: str | None = None
    profile_id: str | None = None
    model: str | None = None


class MessageItem(BaseModel):
    id: str
    conversation_id: str
    role: Literal["system", "user", "assistant"]
    content: str
    token_count: int = 0
    finish_reason: str | None = None
    created_at: float


class AddMessageRequest(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    token_count: int = 0
    finish_reason: str | None = None


class TruncateRequest(BaseModel):
    message_id: str
    inclusive: bool = False


class ConversationSummary(BaseModel):
    id: str
    title: str
    model: str
    system_prompt: str
    profile_id: str
    created_at: float
    updated_at: float
    message_count: int
    last_message: str | None = None


class ConversationDetail(BaseModel):
    id: str
    title: str
    model: str
    system_prompt: str
    profile_id: str
    created_at: float
    updated_at: float
    messages: list[MessageItem]


# --- Endpoints ---

@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Lists conversations ordered by updated_at descending with message counts and last message previews."""
    db = await get_db()
    try:
        query = """
            SELECT 
                c.id, c.title, c.model, c.system_prompt, c.profile_id, c.created_at, c.updated_at,
                COUNT(m.id) AS message_count,
                (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC, rowid DESC LIMIT 1) AS last_message
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
        """
        rows = await db.execute_fetchall(query, (limit, offset))
        result = []
        for r in rows:
            result.append(
                ConversationSummary(
                    id=r["id"],
                    title=r["title"],
                    model=r["model"],
                    system_prompt=r["system_prompt"] or "",
                    profile_id=r["profile_id"] or "default",
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    message_count=r["message_count"],
                    last_message=r["last_message"],
                )
            )
        return result
    finally:
        await db.close()


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
async def create_conversation(req: CreateConversationRequest):
    """Creates a new conversation thread."""
    conv_id = str(uuid.uuid4())
    now = time.time()
    title = req.title.strip() if req.title and req.title.strip() else "New Chat"

    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO conversations (id, title, model, system_prompt, profile_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (conv_id, title, req.model, req.system_prompt, req.profile_id, now, now),
        )
        await db.commit()
        return ConversationSummary(
            id=conv_id,
            title=title,
            model=req.model,
            system_prompt=req.system_prompt,
            profile_id=req.profile_id,
            created_at=now,
            updated_at=now,
            message_count=0,
            last_message=None,
        )
    finally:
        await db.close()


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str):
    """Fetches a conversation thread and its complete ordered history of messages."""
    db = await get_db()
    try:
        conv_row = await db.execute_fetchall(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
        if not conv_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation '{conversation_id}' not found",
            )
        conv = conv_row[0]

        msg_rows = await db.execute_fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC",
            (conversation_id,),
        )
        messages = [
            MessageItem(
                id=m["id"],
                conversation_id=m["conversation_id"],
                role=m["role"],
                content=m["content"],
                token_count=m["token_count"] or 0,
                finish_reason=m["finish_reason"],
                created_at=m["created_at"],
            )
            for m in msg_rows
        ]

        return ConversationDetail(
            id=conv["id"],
            title=conv["title"],
            model=conv["model"],
            system_prompt=conv["system_prompt"] or "",
            profile_id=conv["profile_id"] or "default",
            created_at=conv["created_at"],
            updated_at=conv["updated_at"],
            messages=messages,
        )
    finally:
        await db.close()


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(conversation_id: str, req: UpdateConversationRequest):
    """Updates title, system prompt, profile, or model for a conversation."""
    db = await get_db()
    try:
        conv_row = await db.execute_fetchall(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
        if not conv_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation '{conversation_id}' not found",
            )
        conv = conv_row[0]

        new_title = req.title.strip() if req.title is not None else conv["title"]
        new_prompt = req.system_prompt if req.system_prompt is not None else conv["system_prompt"]
        new_profile = req.profile_id if req.profile_id is not None else conv["profile_id"]
        new_model = req.model if req.model is not None else conv["model"]
        now = time.time()

        await db.execute(
            """
            UPDATE conversations
            SET title = ?, system_prompt = ?, profile_id = ?, model = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_title, new_prompt, new_profile, new_model, now, conversation_id),
        )
        await db.commit()

        # Count messages
        cnt_row = await db.execute_fetchall(
            "SELECT COUNT(id) AS count FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        msg_count = cnt_row[0]["count"] if cnt_row else 0

        last_row = await db.execute_fetchall(
            "SELECT content FROM messages WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        )
        last_msg = last_row[0]["content"] if last_row else None

        return ConversationSummary(
            id=conversation_id,
            title=new_title,
            model=new_model,
            system_prompt=new_prompt,
            profile_id=new_profile,
            created_at=conv["created_at"],
            updated_at=now,
            message_count=msg_count,
            last_message=last_msg,
        )
    finally:
        await db.close()


@router.delete("/{conversation_id}", status_code=status.HTTP_200_OK)
async def delete_conversation(conversation_id: str):
    """Deletes a conversation and all its messages."""
    db = await get_db()
    try:
        conv_row = await db.execute_fetchall(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        )
        if not conv_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation '{conversation_id}' not found",
            )
        await db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        await db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        await db.commit()
        return {"status": "deleted", "id": conversation_id}
    finally:
        await db.close()


@router.post("/{conversation_id}/truncate")
async def truncate_conversation_messages(conversation_id: str, req: TruncateRequest):
    """
    Truncates messages in the conversation relative to the given message_id.
    - If inclusive is False: deletes all messages created AFTER message_id.
    - If inclusive is True: deletes message_id and all messages created AFTER it.
    Used for editing earlier prompts or rolling back for response regeneration.
    """
    db = await get_db()
    try:
        target_row = await db.execute_fetchall(
            "SELECT created_at, rowid FROM messages WHERE id = ? AND conversation_id = ?",
            (req.message_id, conversation_id),
        )
        if not target_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message '{req.message_id}' not found in conversation '{conversation_id}'",
            )
        target_created_at = target_row[0]["created_at"]
        target_rowid = target_row[0]["rowid"]

        # Messages saved within one clock tick share created_at (about 16 ms on Windows):
        # insertion order (rowid) decides which of them come after the target.
        rowid_op = ">=" if req.inclusive else ">"
        await db.execute(
            "DELETE FROM messages WHERE conversation_id = ? "
            f"AND (created_at > ? OR (created_at = ? AND rowid {rowid_op} ?))",
            (conversation_id, target_created_at, target_created_at, target_rowid),
        )

        now = time.time()
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        await db.commit()
        return {"status": "truncated", "conversation_id": conversation_id, "message_id": req.message_id}
    finally:
        await db.close()


@router.post("/{conversation_id}/messages", response_model=MessageItem, status_code=status.HTTP_201_CREATED)
async def add_message(conversation_id: str, req: AddMessageRequest):
    """Appends a single message to a conversation thread."""
    db = await get_db()
    try:
        conv_row = await db.execute_fetchall(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        )
        if not conv_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation '{conversation_id}' not found",
            )

        msg_id = str(uuid.uuid4())
        now = time.time()
        await db.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, token_count, finish_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_id, conversation_id, req.role, req.content, req.token_count, req.finish_reason, now),
        )
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        await db.commit()
        return MessageItem(
            id=msg_id,
            conversation_id=conversation_id,
            role=req.role,
            content=req.content,
            token_count=req.token_count,
            finish_reason=req.finish_reason,
            created_at=now,
        )
    finally:
        await db.close()

