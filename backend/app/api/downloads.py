"""
Mustang AI Downloads API Router
Provides endpoints for enqueueing, monitoring, pausing, resuming, cancelling,
retrying downloads, and streaming real-time progress via Server-Sent Events (SSE).
"""

import asyncio
import json
import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core import security
from app.core.download_manager import get_download_manager

logger = logging.getLogger("mustang.api.downloads")

router = APIRouter(prefix="/api/downloads", tags=["Download Manager"])


class CreateDownloadRequest(BaseModel):
    repo_id: str = Field(..., description="Hugging Face repository ID (e.g. Qwen/Qwen2.5-0.5B-Instruct-GGUF)")
    filename: str = Field(..., description="Target .gguf filename")
    revision: str = Field("main", description="Target repository revision/branch/commit SHA")
    expected_sha256: Optional[str] = Field(None, description="Optional expected SHA-256 hash")
    total_bytes: Optional[int] = Field(None, description="Optional expected total file size in bytes")


class DownloadItemResponse(BaseModel):
    id: str
    repo_id: str
    filename: str
    canonical_url: str
    destination_path: str
    total_bytes: int
    downloaded_bytes: int
    status: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    error_message: Optional[str] = None
    sha256_expected: Optional[str] = None
    sha256_actual: Optional[str] = None
    integrity_status: Optional[str] = None
    speed_bps: float
    eta_seconds: float
    created_at: float
    updated_at: float


@router.get("", response_model=list[DownloadItemResponse])
async def list_downloads():
    """Returns all queued, active, and completed model downloads ordered by created date."""
    dm = get_download_manager()
    downloads = await dm.get_all_downloads()
    return downloads


@router.get("/progress")
async def stream_download_progress(request: Request):
    """
    Streams download progress and state transitions via Server-Sent Events (SSE).
    Supports client reconnection and includes regular keepalive pings.
    """
    dm = get_download_manager()
    queue = dm.subscribe_sse()

    async def event_generator():
        try:
            # Initial connection confirmation comment
            yield ": connected\n\n"

            # Send initial state snapshot of all downloads
            all_current = await dm.get_all_downloads()
            for d in all_current:
                yield f"event: progress\ndata: {json.dumps(d)}\n\n"

            while True:
                # Disconnect guard
                if await request.is_disconnected():
                    break

                try:
                    # Wait for next event or send keepalive ping every 10s
                    task_data = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield f"event: progress\ndata: {json.dumps(task_data)}\n\n"
                except (asyncio.TimeoutError, TimeoutError):
                    yield ": ping\n\n"

        except asyncio.CancelledError:
            pass
        finally:
            dm.unsubscribe_sse(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{download_id}", response_model=DownloadItemResponse)
async def get_download_item(download_id: str):
    """Retrieves metadata and current progress for a specific download task."""
    dm = get_download_manager()
    task = await dm.get_download(download_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Download task '{download_id}' not found.",
        )
    return task


@router.post("", response_model=DownloadItemResponse, status_code=status.HTTP_201_CREATED)
async def create_download_task(
    req: CreateDownloadRequest,
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Validates and enqueues a new model download from Hugging Face Hub.
    Requires X-Mustang-Token authentication.
    """
    dm = get_download_manager()
    task = await dm.create_download(
        repo_id=req.repo_id,
        filename=req.filename,
        revision=req.revision,
        expected_sha256=req.expected_sha256,
        total_bytes=req.total_bytes,
    )
    return task


@router.post("/{download_id}/pause")
async def pause_download_task(
    download_id: str,
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Pauses an active or queued download task. Retains partial download file on disk.
    Requires X-Mustang-Token authentication.
    """
    dm = get_download_manager()
    task = await dm.get_download(download_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Download task '{download_id}' not found.",
        )
    await dm.pause_download(download_id)
    return {"status": "PAUSED", "id": download_id}


@router.post("/{download_id}/resume")
async def resume_download_task(
    download_id: str,
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Resumes a paused download task using HTTP 206 Range requests.
    Requires X-Mustang-Token authentication.
    """
    dm = get_download_manager()
    task = await dm.get_download(download_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Download task '{download_id}' not found.",
        )
    await dm.resume_download(download_id)
    return {"status": "QUEUED", "id": download_id}


@router.post("/{download_id}/cancel")
async def cancel_download_task(
    download_id: str,
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Cancels a download, aborts streaming network connection, and purges staging file.
    Requires X-Mustang-Token authentication.
    """
    dm = get_download_manager()
    task = await dm.get_download(download_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Download task '{download_id}' not found.",
        )
    await dm.cancel_download(download_id)
    return {"status": "CANCELLED", "id": download_id}


@router.post("/{download_id}/retry")
async def retry_download_task(
    download_id: str,
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Retries a failed or cancelled download task from the beginning.
    Requires X-Mustang-Token authentication.
    """
    dm = get_download_manager()
    task = await dm.get_download(download_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Download task '{download_id}' not found.",
        )
    await dm.retry_download(download_id)
    return {"status": "QUEUED", "id": download_id}
