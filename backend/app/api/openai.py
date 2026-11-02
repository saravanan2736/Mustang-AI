"""
Mustang AI OpenAI Compatible API Endpoints
Implements standard /v1/models and /v1/chat/completions for local third-party SDK compatibility.
"""

import json
import time
import uuid
from typing import AsyncIterator
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.models import get_engine
from app.config import settings
from app.core.engine_base import (
    ChatCompletionRequest,
    EngineState,
    InferenceEngine,
)

router = APIRouter(prefix="/v1", tags=["openai"])


@router.get("/models")
async def list_openai_models():
    """Lists available local models in standard OpenAI JSON format."""
    data = []
    if settings.MODELS_DIR.exists():
        for f in settings.MODELS_DIR.glob("*.gguf"):
            stat = f.stat()
            data.append(
                {
                    "id": f.name,
                    "object": "model",
                    "created": int(stat.st_mtime),
                    "owned_by": "local-mustang",
                    "permission": [],
                    "root": f.stem,
                    "parent": None,
                }
            )

    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def openai_chat_completions(
    req: ChatCompletionRequest,
    raw_request: Request,
    engine: InferenceEngine = Depends(get_engine),
):
    """OpenAI-compatible chat completion endpoint supporting streaming and non-streaming modes."""
    status_obj = await engine.get_status()
    if status_obj.state != EngineState.READY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Engine is not ready ({status_obj.state}). Load a model first via /api/models/load.",
        )

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    model_name = status_obj.model_name or "local-model"

    if req.stream:
        async def sse_generator() -> AsyncIterator[str]:
            try:
                async for chunk in engine.stream_chat(req):
                    if await raw_request.is_disconnected():
                        await engine.cancel_generation()
                        break

                    payload = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": chunk.delta_content},
                                "finish_reason": chunk.finish_reason,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"

                yield "data: [DONE]\n\n"
            except Exception as e:
                err = {
                    "error": {
                        "message": str(e),
                        "type": "server_error",
                        "code": 500,
                    }
                }
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # Non-streaming collection
    content_parts = []
    finish_reason = "stop"
    async for chunk in engine.stream_chat(req):
        if chunk.delta_content:
            content_parts.append(chunk.delta_content)
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason

    full_content = "".join(content_parts)
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": full_content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content_parts),
            "total_tokens": len(content_parts),
        },
    }

