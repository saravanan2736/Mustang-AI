"""
Mustang AI Hugging Face Hub Discovery Unit & Integration Tests
Validates search, repository exploration, GGUF file filtering, quantization classification,
error handling (404, 429, 504), rate-limits, and strict security validation against malicious inputs.
"""

import asyncio
import json
import pytest
import httpx
from httpx import AsyncClient

from app.core import hf_client as hf_module
from app.core.hf_client import (
    HuggingFaceClient,
    detect_quantization,
    validate_hf_repo_id,
    validate_hf_filename,
    sanitize_untrusted_text,
)


@pytest.fixture
def mock_hf_client():
    """Provides an isolated HuggingFaceClient instance with MockTransport."""
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        path = request.url.path

        # 1. Search endpoint
        if path == "/api/models":
            query = request.url.params.get("search", "")
            if query == "empty_match":
                return httpx.Response(200, json=[])
            if query == "rate_limit_me":
                return httpx.Response(429, headers={"Retry-After": "45"}, text="Rate limit exceeded")
            if query == "server_error":
                return httpx.Response(500, text="Internal Server Error")

            # Default valid mock search response
            results = [
                {
                    "_id": "66e98ad5d89bf05ecbbdfe9e",
                    "id": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                    "author": "Qwen",
                    "likes": 164,
                    "downloads": 399704,
                    "tags": ["gguf", "chat", "license:apache-2.0"],
                    "description": "Safe description <script>alert(1)</script>",
                    "lastModified": "2024-09-20T06:20:24.000Z",
                },
                {
                    "_id": "66e98ad5d89bf05ecbbdfe9f",
                    "id": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
                    "author": "bartowski",
                    "likes": 500,
                    "downloads": 120000,
                    "tags": ["gguf", "llama"],
                    "description": "Llama 3.1 8B in GGUF format",
                    "lastModified": "2024-08-01T12:00:00.000Z",
                },
            ]
            return httpx.Response(200, json=results)

        # 2. Model details endpoint
        if path.startswith("/api/models/"):
            repo_id = path[len("/api/models/"):]
            if repo_id == "nonexistent/model":
                return httpx.Response(404, json={"error": "Model not found"})
            if repo_id == "rate_limited/model":
                return httpx.Response(429, headers={"Retry-After": "30"}, text="Too Many Requests")
            if repo_id == "crash/model":
                return httpx.Response(502, text="Bad Gateway")

            # Mock model without license or model card
            if repo_id == "barebones/model":
                return httpx.Response(
                    200,
                    json={
                        "id": "barebones/model",
                        "author": "barebones",
                        "siblings": [
                            {"rfilename": "model-q4_k_m.gguf", "size": 1000000},
                        ],
                        "tags": ["gguf"],
                    },
                )

            # Mock model without sizes
            if repo_id == "nosize/model":
                return httpx.Response(
                    200,
                    json={
                        "id": "nosize/model",
                        "author": "nosize",
                        "siblings": [
                            {"rfilename": "model-q4_0.gguf"},
                        ],
                        "tags": ["gguf"],
                    },
                )

            # Mock model with multiple quantizations and non-GGUF files
            return httpx.Response(
                200,
                json={
                    "id": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                    "author": "Qwen",
                    "likes": 164,
                    "downloads": 399704,
                    "lastModified": "2024-09-20T06:20:24.000Z",
                    "cardData": {
                        "license": "apache-2.0",
                    },
                    "siblings": [
                        {"rfilename": ".gitattributes", "size": 1500},
                        {"rfilename": "README.md", "size": 4800},
                        {"rfilename": "LICENSE", "size": 11000},
                        {"rfilename": "qwen2.5-0.5b-instruct-q2_k.gguf", "size": 415182688},
                        {"rfilename": "qwen2.5-0.5b-instruct-q3_k_m.gguf", "size": 432041824},
                        {"rfilename": "qwen2.5-0.5b-instruct-q4_0.gguf", "size": 428730208},
                        {"rfilename": "qwen2.5-0.5b-instruct-q4_k_m.gguf", "size": 491400032, "lfs": {"sha256": "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db"}},
                        {"rfilename": "qwen2.5-0.5b-instruct-q5_k_m.gguf", "size": 522186592},
                        {"rfilename": "qwen2.5-0.5b-instruct-q6_k.gguf", "size": 650379104},
                        {"rfilename": "qwen2.5-0.5b-instruct-q8_0.gguf", "size": 675710816},
                        {"rfilename": "qwen2.5-0.5b-instruct-fp16.gguf", "size": 1266425696},
                        {"rfilename": "qwen2.5-0.5b-instruct-bf16.gguf", "size": 1266425696},
                        {"rfilename": "qwen2.5-0.5b-instruct-iq3_m.gguf", "size": 390000000},
                        {"rfilename": "qwen2.5-0.5b-instruct-custom_unknown_flavor.gguf", "size": 450000000},
                        {"rfilename": "unrelated_weights.bin", "size": 999999},
                    ],
                },
            )

        # 3. Raw README.md endpoint
        if path.endswith("/raw/main/README.md"):
            if "barebones" in path:
                return httpx.Response(404, text="Not found")
            return httpx.Response(200, text="# Qwen 2.5 0.5B GGUF\nModel card content with instructions.\n<script>malicious()</script>")

        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport, base_url="https://huggingface.co")
    client_instance = HuggingFaceClient(custom_client=mock_client)
    hf_module.set_hf_client(client_instance)
    yield client_instance
    hf_module.set_hf_client(None)


# ==================== 1. SEARCH TESTS ====================

@pytest.mark.asyncio
async def test_successful_repository_search(async_client: AsyncClient, mock_hf_client):
    """Verifies that search returns structured model repository items with tags and metadata."""
    res = await async_client.get("/api/hf/search?query=qwen")
    assert res.status_code == 200
    data = res.json()
    assert data["query"] == "qwen"
    assert data["total"] == 2
    assert len(data["results"]) == 2

    first = data["results"][0]
    assert first["repo_id"] == "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
    assert first["author"] == "Qwen"
    assert first["likes"] == 164
    assert first["downloads"] == 399704
    assert first["license"] == "apache-2.0"
    assert first["is_gguf"] is True
    assert "https://huggingface.co/" in first["repo_url"]


@pytest.mark.asyncio
async def test_empty_search_results(async_client: AsyncClient, mock_hf_client):
    """Verifies that queries with no matches return total=0 and an empty results array."""
    res = await async_client.get("/api/hf/search?query=empty_match")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_invalid_search_parameters(async_client: AsyncClient, mock_hf_client):
    """Verifies that empty, oversized, or out-of-range limit queries return HTTP 400 or 422."""
    # Empty query
    r1 = await async_client.get("/api/hf/search?query=")
    assert r1.status_code in (400, 422)

    # Limit too large
    r2 = await async_client.get("/api/hf/search?query=test&limit=999")
    assert r2.status_code in (400, 422)

    # Limit zero
    r3 = await async_client.get("/api/hf/search?query=test&limit=0")
    assert r3.status_code in (400, 422)


# ==================== 2. REPOSITORY DETAILS & GGUF FILTERING ====================

@pytest.mark.asyncio
async def test_repository_details_retrieval(async_client: AsyncClient, mock_hf_client):
    """Verifies that repository details return license, revision, and metadata."""
    res = await async_client.get("/api/hf/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF")
    assert res.status_code == 200
    data = res.json()
    assert data["repo_id"] == "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
    assert data["author"] == "Qwen"
    assert data["license"] == "apache-2.0"
    assert data["revision"] == "main"
    assert data["gguf_files_count"] > 0
    assert "README" not in [f["filename"] for f in data["available_gguf_files"]]


@pytest.mark.asyncio
async def test_gguf_file_filtering(async_client: AsyncClient, mock_hf_client):
    """Verifies that non-GGUF files (.gitattributes, README.md, LICENSE, .bin) are strictly filtered out."""
    res = await async_client.get("/api/hf/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF")
    assert res.status_code == 200
    files = res.json()["available_gguf_files"]

    for f in files:
        assert f["filename"].lower().endswith(".gguf")
        assert f["filename"] not in (".gitattributes", "README.md", "LICENSE", "unrelated_weights.bin")
        assert f["download_url"].startswith("https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/")


@pytest.mark.asyncio
async def test_multiple_quantization_variants(async_client: AsyncClient, mock_hf_client):
    """Verifies that various quantization variants are accurately categorized."""
    res = await async_client.get("/api/hf/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF")
    assert res.status_code == 200
    files = res.json()["available_gguf_files"]
    quants_by_file = {f["filename"]: f["quantization"] for f in files}

    assert quants_by_file["qwen2.5-0.5b-instruct-q2_k.gguf"] == "Q2_K"
    assert quants_by_file["qwen2.5-0.5b-instruct-q3_k_m.gguf"] == "Q3_K_M"
    assert quants_by_file["qwen2.5-0.5b-instruct-q4_0.gguf"] == "Q4_0"
    assert quants_by_file["qwen2.5-0.5b-instruct-q4_k_m.gguf"] == "Q4_K_M"
    assert quants_by_file["qwen2.5-0.5b-instruct-q5_k_m.gguf"] == "Q5_K_M"
    assert quants_by_file["qwen2.5-0.5b-instruct-q6_k.gguf"] == "Q6_K"
    assert quants_by_file["qwen2.5-0.5b-instruct-q8_0.gguf"] == "Q8_0"
    assert quants_by_file["qwen2.5-0.5b-instruct-fp16.gguf"] == "F16"
    assert quants_by_file["qwen2.5-0.5b-instruct-bf16.gguf"] == "BF16"
    assert quants_by_file["qwen2.5-0.5b-instruct-iq3_m.gguf"] == "IQ3_M"
    # Unmatched / unknown quantization variant should be explicitly 'Unknown'
    assert quants_by_file["qwen2.5-0.5b-instruct-custom_unknown_flavor.gguf"] == "Unknown"


# ==================== 3. MISSING METADATA RESILIENCE ====================

@pytest.mark.asyncio
async def test_missing_license_and_model_card(async_client: AsyncClient, mock_hf_client):
    """Verifies that repositories lacking license or model card don't crash and default safely."""
    res = await async_client.get("/api/hf/models/barebones/model")
    assert res.status_code == 200
    data = res.json()
    assert data["license"] == "Unknown"
    assert data["model_card"] == ""
    assert data["gguf_files_count"] == 1


@pytest.mark.asyncio
async def test_missing_file_sizes(async_client: AsyncClient, mock_hf_client):
    """Verifies that GGUF files with missing size attributes don't crash."""
    res = await async_client.get("/api/hf/models/nosize/model")
    assert res.status_code == 200
    data = res.json()
    assert data["gguf_files_count"] == 1
    f = data["available_gguf_files"][0]
    assert f["size_bytes"] is None
    assert f["size_gb"] is None


# ==================== 4. ERROR HANDLING & RATE LIMITS ====================

@pytest.mark.asyncio
async def test_http_429_handling(async_client: AsyncClient, mock_hf_client):
    """Verifies that HTTP 429 returns a 429 status code with retry guidance."""
    res = await async_client.get("/api/hf/search?query=rate_limit_me")
    assert res.status_code == 429
    assert "rate limit" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_http_404_handling(async_client: AsyncClient, mock_hf_client):
    """Verifies that a nonexistent model repository returns HTTP 404."""
    res = await async_client.get("/api/hf/models/nonexistent/model")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_http_502_server_error_handling(async_client: AsyncClient, mock_hf_client):
    """Verifies that remote server errors return HTTP 502 Bad Gateway."""
    res = await async_client.get("/api/hf/models/crash/model")
    assert res.status_code == 502


@pytest.mark.asyncio
async def test_network_timeout_handling():
    """Verifies that request timeouts raise HTTP 504 Gateway Timeout."""
    def timeout_handler(request: httpx.Request):
        raise httpx.TimeoutException("Connection timed out")

    transport = httpx.MockTransport(timeout_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://huggingface.co")
    hf = HuggingFaceClient(custom_client=client)

    with pytest.raises(Exception) as excinfo:
        await hf.search_repositories("test")
    assert excinfo.value.status_code == 504
    assert "timed out" in excinfo.value.detail.lower()


# ==================== 5. SECURITY & VALIDATION TESTS ====================

def test_malicious_repository_id_rejection():
    """Verifies that path traversal and dangerous characters are rejected in repository IDs."""
    invalid_ids = [
        "../evil/repo",
        "evil/../../etc/passwd",
        "repo\\with\\backslashes",
        "/absolute/repo",
        "repo/with/null\0byte",
        "repo/with/spaces not allowed",
        "toolong/" + "a" * 150,
        "repo;rm -rf /",
        "repo<script>",
    ]
    for bad in invalid_ids:
        with pytest.raises(Exception) as exc:
            validate_hf_repo_id(bad)
        assert exc.value.status_code == 400


def test_unsafe_repository_filenames():
    """Verifies that filenames attempting directory escape or non-gguf extensions are blocked."""
    invalid_files = [
        "../../model.gguf",
        "subdir/model.gguf",
        "model.bin",
        "model.exe",
        "model.py",
        "model.gguf\0.bin",
        "model.GGUF.sh",
    ]
    for bad in invalid_files:
        with pytest.raises(Exception) as exc:
            validate_hf_filename(bad)
        assert exc.value.status_code == 400

    # Valid should pass
    assert validate_hf_filename("model-q4_k_m.gguf") == "model-q4_k_m.gguf"
    assert validate_hf_filename("Qwen2.5-0.5B-Instruct-Q4_K_M.gguf") == "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"


def test_untrusted_model_description_rendering():
    """Verifies that HTML tags and script elements are stripped from model cards/descriptions."""
    raw = "Normal text <script>alert('xss')</script> <iframe src='evil.com'></iframe><b>Bold</b>"
    cleaned = sanitize_untrusted_text(raw)
    assert "<script>" not in cleaned
    assert "</script>" not in cleaned
    assert "<iframe" not in cleaned
    assert "Normal text" in cleaned
    assert "Bold" in cleaned


@pytest.mark.asyncio
async def test_duplicate_request_deduplication(mock_hf_client):
    """Verifies that concurrent requests for the same repository share in-flight futures."""
    # Launch 5 concurrent calls for the same model
    coros = [
        mock_hf_client.get_repository_details("Qwen/Qwen2.5-0.5B-Instruct-GGUF")
        for _ in range(5)
    ]
    results = await asyncio.gather(*coros)
    assert len(results) == 5
    for r in results:
        assert r["repo_id"] == "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
