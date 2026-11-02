"""
Mustang AI Assistant Profiles Router
Manage persona profiles, system prompt presets, and customized instructions.
"""

import logging
import time
import uuid
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.db.database import get_db

logger = logging.getLogger("mustang.profiles")
router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileSchema(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    is_builtin: bool
    created_at: float


class CreateProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    system_prompt: str = Field(min_length=1, max_length=10000)


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=10000)


@router.get("", response_model=list[ProfileSchema])
async def list_profiles():
    """Lists all available assistant profiles (built-in and custom)."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM profiles ORDER BY is_builtin DESC, created_at ASC"
        )
        return [
            ProfileSchema(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                system_prompt=r["system_prompt"],
                is_builtin=bool(r["is_builtin"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/{profile_id}", response_model=ProfileSchema)
async def get_profile(profile_id: str):
    """Retrieves a specific assistant profile."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile '{profile_id}' not found",
            )
        r = rows[0]
        return ProfileSchema(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            system_prompt=r["system_prompt"],
            is_builtin=bool(r["is_builtin"]),
            created_at=r["created_at"],
        )
    finally:
        await db.close()


@router.post("", response_model=ProfileSchema, status_code=status.HTTP_201_CREATED)
async def create_profile(req: CreateProfileRequest):
    """Creates a custom assistant profile."""
    profile_id = f"custom-{uuid.uuid4().hex[:8]}"
    now = time.time()
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO profiles (id, name, description, system_prompt, is_builtin, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (profile_id, req.name.strip(), req.description.strip(), req.system_prompt.strip(), now),
        )
        await db.commit()
        return ProfileSchema(
            id=profile_id,
            name=req.name.strip(),
            description=req.description.strip(),
            system_prompt=req.system_prompt.strip(),
            is_builtin=False,
            created_at=now,
        )
    finally:
        await db.close()


@router.patch("/{profile_id}", response_model=ProfileSchema)
async def update_profile(profile_id: str, req: UpdateProfileRequest):
    """Updates a custom assistant profile. Built-in profiles cannot be modified."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile '{profile_id}' not found",
            )
        r = rows[0]
        if r["is_builtin"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Built-in assistant profiles cannot be modified. Create a custom profile instead.",
            )

        new_name = req.name.strip() if req.name is not None else r["name"]
        new_desc = req.description.strip() if req.description is not None else r["description"]
        new_prompt = req.system_prompt.strip() if req.system_prompt is not None else r["system_prompt"]

        await db.execute(
            """
            UPDATE profiles
            SET name = ?, description = ?, system_prompt = ?
            WHERE id = ?
            """,
            (new_name, new_desc, new_prompt, profile_id),
        )
        await db.commit()

        return ProfileSchema(
            id=profile_id,
            name=new_name,
            description=new_desc,
            system_prompt=new_prompt,
            is_builtin=False,
            created_at=r["created_at"],
        )
    finally:
        await db.close()


@router.delete("/{profile_id}", status_code=status.HTTP_200_OK)
async def delete_profile(profile_id: str):
    """Deletes a custom assistant profile. Built-in profiles cannot be deleted."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT is_builtin FROM profiles WHERE id = ?", (profile_id,)
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile '{profile_id}' not found",
            )
        if rows[0]["is_builtin"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Built-in assistant profiles cannot be deleted.",
            )

        await db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        await db.commit()
        return {"status": "deleted", "id": profile_id}
    finally:
        await db.close()

