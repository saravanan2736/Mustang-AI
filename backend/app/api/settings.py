"""
Mustang AI Settings Router
Persistent user preferences, sampling parameters, and generation configuration.
"""

import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings as app_cfg
from app.db.database import get_db

logger = logging.getLogger("mustang.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


class AppSettingsSchema(BaseModel):
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    top_k: int = Field(default=40, ge=1, le=200)
    repeat_penalty: float = Field(default=1.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=64, le=16384)
    context_size: int = Field(default=4096, ge=512, le=65536)
    default_profile: str = "default"
    custom_system_prompt: str = ""


@router.get("", response_model=AppSettingsSchema)
async def get_settings():
    """Retrieves current application and generation settings from SQLite."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT key, value FROM app_settings")
        d = {r["key"]: r["value"] for r in rows}

        return AppSettingsSchema(
            temperature=float(d.get("temperature", app_cfg.DEFAULT_TEMPERATURE)),
            top_p=float(d.get("top_p", app_cfg.DEFAULT_TOP_P)),
            top_k=int(d.get("top_k", app_cfg.DEFAULT_TOP_K)),
            repeat_penalty=float(d.get("repeat_penalty", app_cfg.DEFAULT_REPEAT_PENALTY)),
            max_tokens=int(d.get("max_tokens", app_cfg.DEFAULT_MAX_TOKENS)),
            context_size=int(d.get("context_size", app_cfg.DEFAULT_CONTEXT_SIZE)),
            default_profile=d.get("default_profile", "default"),
            custom_system_prompt=d.get("custom_system_prompt", ""),
        )
    finally:
        await db.close()


@router.put("", response_model=AppSettingsSchema)
async def update_settings(new_settings: AppSettingsSchema):
    """Updates persistent generation settings in SQLite."""
    db = await get_db()
    try:
        items = [
            ("temperature", str(new_settings.temperature)),
            ("top_p", str(new_settings.top_p)),
            ("top_k", str(new_settings.top_k)),
            ("repeat_penalty", str(new_settings.repeat_penalty)),
            ("max_tokens", str(new_settings.max_tokens)),
            ("context_size", str(new_settings.context_size)),
            ("default_profile", new_settings.default_profile),
            ("custom_system_prompt", new_settings.custom_system_prompt),
        ]
        for k, v in items:
            await db.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (k, v),
            )
        await db.commit()
        return new_settings
    finally:
        await db.close()

