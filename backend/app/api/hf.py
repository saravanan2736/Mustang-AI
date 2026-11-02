"""
Mustang AI Hugging Face API Router
Endpoints for searching Hugging Face GGUF models and exploring repository file trees.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.hf_client import get_hf_client

logger = logging.getLogger("mustang.api.hf")

router = APIRouter(prefix="/api/hf", tags=["Hugging Face Hub"])


class HFSearchResultItem(BaseModel):
    repo_id: str
    author: str
    repo_name: str
    description: str
    likes: int
    downloads: int
    last_modified: Optional[str] = None
    license: Optional[str] = None
    is_gguf: bool
    pipeline_tag: Optional[str] = None
    repo_url: str


class HFSearchResponse(BaseModel):
    query: str
    limit: int
    total: int
    results: list[HFSearchResultItem]


class HFGGUFFileItem(BaseModel):
    filename: str
    path: str
    size_bytes: Optional[int] = None
    size_gb: Optional[float] = None
    sha256: Optional[str] = None
    quantization: str
    download_url: str


class HFRepositoryDetailResponse(BaseModel):
    repo_id: str
    author: str
    repo_name: str
    description: str
    model_card: str
    license: Optional[str] = None
    likes: int
    downloads: int
    last_modified: Optional[str] = None
    revision: str = "main"
    total_files: int
    gguf_files_count: int
    available_gguf_files: list[HFGGUFFileItem]
    repo_url: str


@router.get("/search", response_model=HFSearchResponse)
async def search_hf_models(
    query: str = Query(..., min_length=1, max_length=100, description="Model search query"),
    limit: int = Query(20, ge=1, le=50, description="Max results to return"),
    sort: str = Query("downloads", description="Sort field: downloads, likes, or lastModified"),
    direction: int = Query(-1, description="Sort direction: -1 descending, 1 ascending"),
    cursor: Optional[str] = Query(None, description="Optional pagination cursor"),
):
    """
    Searches public Hugging Face repositories filtered for GGUF-compatible weights.
    Returns structured metadata including likes, downloads, license, and repository URLs.
    """
    res = await get_hf_client().search_repositories(
        query=query,
        limit=limit,
        sort=sort,
        direction=direction,
        cursor=cursor,
    )
    return res


@router.get("/models/{repo_id:path}", response_model=HFRepositoryDetailResponse)
async def get_hf_repository_details(
    repo_id: str,
    fetch_readme: bool = Query(True, description="Whether to include README.md model card summary"),
):
    """
    Retrieves repository metadata, explores the file tree, extracts all .gguf files,
    classifies quantization variants, and provides safe model card information.
    """
    res = await get_hf_client().get_repository_details(
        repo_id=repo_id,
        fetch_readme=fetch_readme,
    )
    return res
