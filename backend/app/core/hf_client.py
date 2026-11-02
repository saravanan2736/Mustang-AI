"""
Mustang AI Hugging Face Hub API Client
Asynchronous, cached, and security-validated client for searching and inspecting
GGUF model repositories on Hugging Face Hub without executing remote code or weights.
"""

import asyncio
import logging
from pathlib import Path
import re
import time
from typing import Any, Optional
import httpx
from fastapi import HTTPException, status

logger = logging.getLogger("mustang.hf_client")

# Strict regex for Hugging Face repository IDs:
# Allowed: "org/repo_name" or "repo_name", characters [a-zA-Z0-9_.-]
# Strictly forbids path traversal ('..'), absolute paths, protocols, control chars
HF_REPO_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+(?:\/[a-zA-Z0-9_\-\.]+)?$")

# Strict regex for filenames
HF_SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

# Windows reserved device names that cause kernel hangs or device mapping escapes
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

# Quantization pattern recognition:
# Matches common GGUF quantization naming standards with delimiter boundaries
QUANT_PATTERN = re.compile(
    r"(?i)(?:^|[-._])(Q2_K_S|Q2_K|Q3_K_S|Q3_K_M|Q3_K_L|Q3_K|Q4_0|Q4_1|Q4_K_S|Q4_K_M|Q4_K|Q5_0|Q5_1|Q5_K_S|Q5_K_M|Q5_K|Q6_K|Q8_0|Q8_1|BF16|FP16|F16|FP32|F32|IQ1_S|IQ1_M|IQ2_XXS|IQ2_XS|IQ2_S|IQ2_M|IQ3_XXS|IQ3_XS|IQ3_S|IQ3_M|IQ4_XS|IQ4_NL)(?:[-._]|$)"
)


def validate_hf_repo_id(repo_id: str) -> str:
    """
    Validates that a repository ID is safe and matches Hugging Face naming conventions.
    Rejects any path traversal, control characters, or malicious syntax.
    """
    if not repo_id or not isinstance(repo_id, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository ID must be a non-empty string.",
        )

    cleaned = repo_id.strip()

    if len(cleaned) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository ID exceeds maximum allowed length of 128 characters.",
        )

    # Disallow path traversal indicators
    if ".." in cleaned or "\\" in cleaned or "\0" in cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security violation: Repository ID contains illegal path traversal characters.",
        )

    parts = cleaned.split("/")
    if len(parts) > 2 or any(not p or p.startswith(".") or p.startswith("-") for p in parts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed repository ID. Expected format: 'owner/model_name' or 'model_name'.",
        )

    if not HF_REPO_ID_REGEX.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid characters in repository ID. Only alphanumeric, '_', '-', and '.' are allowed.",
        )

    return cleaned


def validate_hf_filename(filename: str) -> str:
    """
    Validates that a filename inside a repository is safe and has a .gguf extension.
    """
    if not filename or not isinstance(filename, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename must be a non-empty string.",
        )

    cleaned = filename.strip()

    if ".." in cleaned or "\\" in cleaned or "/" in cleaned or "\0" in cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security violation: Filename contains illegal path traversal characters.",
        )

    if not cleaned.lower().endswith(".gguf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security violation: File '{cleaned}' is not a GGUF file. Only .gguf files are allowed.",
        )

    # Check for reserved Windows device names
    base_stem = Path(cleaned).stem.upper()
    if base_stem in WINDOWS_RESERVED_NAMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security violation: File '{cleaned}' uses reserved Windows device name '{base_stem}'.",
        )

    if not HF_SAFE_FILENAME_REGEX.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename contains unsupported or unsafe characters.",
        )

    return cleaned


def detect_quantization(filename: str) -> str:
    """
    Identifies quantization variant from GGUF filename conventions.
    Returns standard uppercase format (e.g. 'Q4_K_M', 'Q8_0', 'BF16') or 'Unknown'.
    Never invents details from incomplete names.
    """
    if not filename:
        return "Unknown"

    name_without_ext = filename
    if name_without_ext.lower().endswith(".gguf"):
        name_without_ext = name_without_ext[:-5]

    match = QUANT_PATTERN.search(name_without_ext)
    if match:
        quant = match.group(1).upper()
        # Canonicalize aliases
        if quant == "FP16":
            return "F16"
        if quant == "FP32":
            return "F32"
        return quant

    return "Unknown"


def sanitize_untrusted_text(text: Optional[str], max_length: int = 5000) -> str:
    """
    Strips raw HTML tags, dangerous control characters, and truncates excessive lengths
    from untrusted external strings.
    """
    if not text:
        return ""

    # Strip control characters except newline and tab
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text))
    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", "", clean)
    # Bounded length
    if len(clean) > max_length:
        clean = clean[:max_length] + "..."

    return clean.strip()


def extract_license_from_tags(tags: list[Any]) -> Optional[str]:
    """Extracts license name from Hugging Face tags list (e.g. ['license:apache-2.0'])."""
    if not tags or not isinstance(tags, list):
        return None
    for tag in tags:
        if isinstance(tag, str) and tag.lower().startswith("license:"):
            return tag.split(":", 1)[1].strip()
    return None


class SimpleTTLCache:
    """Thread-safe bounded in-memory cache with time-to-live expiration."""

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 128):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get(self, key: str) -> Optional[Any]:
        async with self._get_lock():
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, val = entry
            if time.time() - ts > self.ttl:
                del self._cache[key]
                return None
            return val

    async def set(self, key: str, val: Any) -> None:
        async with self._get_lock():
            # Evict oldest if full
            if len(self._cache) >= self.max_entries:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
            self._cache[key] = (time.time(), val)

    async def clear(self) -> None:
        async with self._get_lock():
            self._cache.clear()


class HuggingFaceClient:
    """
    Asynchronous client for querying Hugging Face Hub metadata with strict
    SSRF/URL protections, bounded timeouts, and deduplicated requests.
    """

    HF_API_BASE = "https://huggingface.co"

    def __init__(
        self,
        timeout: float = 8.0,
        cache_ttl_seconds: float = 300.0,
        custom_client: Optional[httpx.AsyncClient] = None,
    ):
        self.timeout = timeout
        self._client = custom_client
        self._search_cache = SimpleTTLCache(ttl_seconds=cache_ttl_seconds, max_entries=100)
        self._model_cache = SimpleTTLCache(ttl_seconds=cache_ttl_seconds, max_entries=100)
        # In-flight request deduplication map: key -> asyncio.Future
        self._in_flight: dict[str, asyncio.Future] = {}
        self._in_flight_lock: Optional[asyncio.Lock] = None

    def _get_in_flight_lock(self) -> asyncio.Lock:
        if self._in_flight_lock is None:
            self._in_flight_lock = asyncio.Lock()
        return self._in_flight_lock

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._client and not self._client.is_closed:
            return self._client
        self._client = httpx.AsyncClient(
            base_url=self.HF_API_BASE,
            timeout=httpx.Timeout(self.timeout, connect=4.0),
            headers={
                "User-Agent": "MustangAI/0.1.0 (Windows NT 10.0; Win64; x64; Local-First)",
                "Accept": "application/json",
            },
            follow_redirects=False,  # Strict: no arbitrary automatic redirect following
        )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def search_repositories(
        self,
        query: str,
        limit: int = 20,
        sort: str = "downloads",
        direction: int = -1,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Searches public Hugging Face model repositories with GGUF compatibility.
        """
        # Parameter validation
        if not query or not isinstance(query, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search query cannot be empty.",
            )

        cleaned_query = query.strip()
        if len(cleaned_query) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search query exceeds maximum length of 100 characters.",
            )

        if limit < 1 or limit > 50:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit must be between 1 and 50.",
            )

        valid_sorts = {"downloads", "likes", "lastModified"}
        if sort not in valid_sorts:
            sort = "downloads"

        cache_key = f"search:{cleaned_query.lower()}:{limit}:{sort}:{direction}:{cursor or ''}"
        cached = await self._search_cache.get(cache_key)
        if cached:
            return cached

        # Deduplicate simultaneous identical queries
        async with self._get_in_flight_lock():
            if cache_key in self._in_flight:
                future = self._in_flight[cache_key]
                # Await in-flight search
                return await future

            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._in_flight[cache_key] = future

        try:
            client = self._get_http_client()
            params: dict[str, Any] = {
                "search": cleaned_query,
                "filter": "gguf",
                "sort": sort,
                "direction": str(direction),
                "limit": limit,
                "full": "false",
                "config": "false",
            }

            resp = await client.get("/api/models", params=params)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Hugging Face API rate limit reached. Retry in {retry_after}s.",
                )

            if resp.status_code >= 500:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Hugging Face service currently unavailable.",
                )

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Hugging Face search error (HTTP {resp.status_code}).",
                )

            data = resp.json()
            if not isinstance(data, list):
                data = []

            results = []
            for item in data:
                raw_id = item.get("id") or item.get("modelId")
                if not raw_id:
                    continue

                tags = item.get("tags") or []
                is_gguf = any("gguf" in str(t).lower() for t in tags)
                parts = raw_id.split("/")
                author = parts[0] if len(parts) > 1 else (item.get("author") or "Community")
                repo_name = parts[-1]

                results.append(
                    {
                        "repo_id": raw_id,
                        "author": sanitize_untrusted_text(author, max_length=64),
                        "repo_name": sanitize_untrusted_text(repo_name, max_length=128),
                        "description": sanitize_untrusted_text(item.get("description", ""), max_length=500),
                        "likes": int(item.get("likes") or 0),
                        "downloads": int(item.get("downloads") or 0),
                        "last_modified": item.get("lastModified") or item.get("createdAt"),
                        "license": extract_license_from_tags(tags),
                        "is_gguf": is_gguf,
                        "pipeline_tag": item.get("pipeline_tag"),
                        "repo_url": f"https://huggingface.co/{raw_id}",
                    }
                )

            res = {
                "query": cleaned_query,
                "limit": limit,
                "total": len(results),
                "results": results,
            }

            await self._search_cache.set(cache_key, res)
            if not future.done():
                future.set_result(res)
            return res

        except HTTPException as http_err:
            if not future.done():
                future.set_exception(http_err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise
        except (httpx.TimeoutException, TimeoutError):
            err = HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Hugging Face API request timed out. Please try again.",
            )
            if not future.done():
                future.set_exception(err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise err
        except httpx.RequestError as net_err:
            err = HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error communicating with Hugging Face: {str(net_err)[:100]}",
            )
            if not future.done():
                future.set_exception(err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise err
        except Exception as unexpected:
            logger.error(f"Unexpected error in Hugging Face search: {unexpected}")
            err = HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Internal error processing Hugging Face search: {str(unexpected)[:100]}",
            )
            if not future.done():
                future.set_exception(err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise err
        finally:
            async with self._get_in_flight_lock():
                self._in_flight.pop(cache_key, None)

    async def get_repository_details(self, repo_id: str, fetch_readme: bool = True) -> dict[str, Any]:
        """
        Retrieves detailed repository metadata, file tree, identified .gguf files,
        quantization variants, and safe model card information.
        """
        clean_repo_id = validate_hf_repo_id(repo_id)

        cache_key = f"repo:{clean_repo_id.lower()}:{fetch_readme}"
        cached = await self._model_cache.get(cache_key)
        if cached:
            return cached

        # Deduplicate simultaneous identical requests
        async with self._get_in_flight_lock():
            if cache_key in self._in_flight:
                future = self._in_flight[cache_key]
                return await future

            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._in_flight[cache_key] = future

        try:
            client = self._get_http_client()
            url = f"/api/models/{clean_repo_id}"
            params = {"blobs": "true"}

            resp = await client.get(url, params=params)

            if resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Repository '{clean_repo_id}' not found on Hugging Face Hub.",
                )

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Hugging Face API rate limit reached. Retry in {retry_after}s.",
                )

            if resp.status_code >= 500:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Hugging Face service currently unavailable.",
                )

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to fetch repository metadata (HTTP {resp.status_code}).",
                )

            data = resp.json()
            if not isinstance(data, dict):
                data = {}

            siblings = data.get("siblings") or []
            card_data = data.get("cardData") or {}
            tags = data.get("tags") or []

            # Extract license
            lic = card_data.get("license") or extract_license_from_tags(tags) or "Unknown"

            parts = clean_repo_id.split("/")
            author = parts[0] if len(parts) > 1 else (data.get("author") or "Community")
            repo_name = parts[-1]

            # Filter GGUF files and extract quantization and sizes
            available_gguf_files = []
            has_readme = False

            for sibling in siblings:
                if not isinstance(sibling, dict):
                    continue
                rfilename = sibling.get("rfilename", "")
                if not rfilename:
                    continue

                if rfilename.lower() == "readme.md":
                    has_readme = True

                if not rfilename.lower().endswith(".gguf"):
                    continue

                # File size: inspect 'size' attribute or 'lfs.size'
                size_bytes = sibling.get("size")
                lfs_info = sibling.get("lfs") or {}
                if not size_bytes and isinstance(lfs_info, dict):
                    size_bytes = lfs_info.get("size")

                sha256_hash = lfs_info.get("sha256") if isinstance(lfs_info, dict) else None

                quant = detect_quantization(rfilename)
                size_gb = round(size_bytes / (1024**3), 2) if (size_bytes and size_bytes > 0) else None

                available_gguf_files.append(
                    {
                        "filename": rfilename,
                        "path": rfilename,
                        "size_bytes": size_bytes,
                        "size_gb": size_gb,
                        "sha256": sha256_hash,
                        "quantization": quant,
                        "download_url": f"{self.HF_API_BASE}/{clean_repo_id}/resolve/main/{rfilename}",
                    }
                )

            # Sort GGUF files by filename
            available_gguf_files.sort(key=lambda f: f["filename"].lower())

            # Fetch README.md summary if requested and present
            model_card_text = ""
            if fetch_readme and has_readme:
                try:
                    readme_resp = await client.get(
                        f"/{clean_repo_id}/raw/main/README.md",
                        timeout=5.0,
                    )
                    if readme_resp.status_code == 200:
                        model_card_text = sanitize_untrusted_text(readme_resp.text, max_length=12000)
                except Exception as readme_err:
                    logger.debug(f"Could not retrieve README.md for {clean_repo_id}: {readme_err}")

            description = sanitize_untrusted_text(
                data.get("description") or (model_card_text[:300] if model_card_text else ""),
                max_length=500,
            )

            res = {
                "repo_id": clean_repo_id,
                "author": sanitize_untrusted_text(author, max_length=64),
                "repo_name": sanitize_untrusted_text(repo_name, max_length=128),
                "description": description,
                "model_card": model_card_text,
                "license": lic,
                "likes": int(data.get("likes") or 0),
                "downloads": int(data.get("downloads") or 0),
                "last_modified": data.get("lastModified") or data.get("createdAt"),
                "revision": "main",
                "total_files": len(siblings),
                "gguf_files_count": len(available_gguf_files),
                "available_gguf_files": available_gguf_files,
                "repo_url": f"https://huggingface.co/{clean_repo_id}",
            }

            await self._model_cache.set(cache_key, res)
            if not future.done():
                future.set_result(res)
            return res

        except HTTPException as http_err:
            if not future.done():
                future.set_exception(http_err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise
        except (httpx.TimeoutException, TimeoutError):
            err = HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Hugging Face API request timed out. Please try again.",
            )
            if not future.done():
                future.set_exception(err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise err
        except httpx.RequestError as net_err:
            err = HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error communicating with Hugging Face: {str(net_err)[:100]}",
            )
            if not future.done():
                future.set_exception(err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise err
        except Exception as unexpected:
            logger.error(f"Unexpected error in Hugging Face get_repository_details: {unexpected}")
            err = HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Internal error processing Hugging Face details: {str(unexpected)[:100]}",
            )
            if not future.done():
                future.set_exception(err)
                try:
                    future.exception()
                except Exception:
                    pass
            raise err
            if not future.done():
                future.set_exception(err)
            raise err
        finally:
            async with self._get_in_flight_lock():
                self._in_flight.pop(cache_key, None)


# Global singleton client instance management
_hf_client_instance: Optional[HuggingFaceClient] = None


def get_hf_client() -> HuggingFaceClient:
    """Returns the active HuggingFaceClient singleton."""
    global _hf_client_instance
    if _hf_client_instance is None:
        _hf_client_instance = HuggingFaceClient()
    return _hf_client_instance


def set_hf_client(client: Optional[HuggingFaceClient]) -> None:
    """Injects or resets the active HuggingFaceClient (useful for testing)."""
    global _hf_client_instance
    _hf_client_instance = client


hf_client = get_hf_client()

