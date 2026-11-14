"""
Mustang AI Asynchronous Model Download Manager
Manages background GGUF model downloads, HTTP Range resume (RFC 7233), rolling speed/ETA,
anti-SSRF redirect validation, disk space pre-flight checks, SHA-256 integrity verification,
atomic filesystem finalization, and automatic Local Model Library registration.
"""

import asyncio
from collections import deque
import hashlib
import ipaddress
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import time
from typing import Any, Optional
import urllib.parse

from fastapi import HTTPException, status
import httpcore
from httpcore._backends.anyio import AnyIOBackend
import httpx

from app.config import settings
from app.core.gguf_parser import parse_gguf_metadata
from app.core.hf_client import validate_hf_filename, validate_hf_repo_id
from app.core.model_library import generate_stable_model_id, scan_models_directory
from app.db.database import get_db

logger = logging.getLogger("mustang.downloads")

# Configuration Constants
CHUNK_SIZE_BYTES = 1024 * 1024  # 1 MiB chunk streaming
MAX_ACTIVE_DOWNLOADS = 1
MAX_QUEUED_DOWNLOADS = 10
DISK_SAFETY_MARGIN_BYTES = 200 * 1024 * 1024  # 200 MiB safety headroom
SPEED_WINDOW_SECONDS = 3.0
MAX_REDIRECT_HOPS = 5

VALID_STATES = {
    "QUEUED",
    "DOWNLOADING",
    "PAUSED",
    "VERIFYING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
}

VALID_TRANSITIONS = {
    "QUEUED": {"DOWNLOADING", "PAUSED", "CANCELLED", "FAILED"},
    "DOWNLOADING": {"PAUSED", "VERIFYING", "FAILED", "CANCELLED"},
    "PAUSED": {"QUEUED", "CANCELLED"},
    "VERIFYING": {"COMPLETED", "FAILED"},
    "FAILED": {"QUEUED", "CANCELLED"},
    "CANCELLED": {"QUEUED"},
    "COMPLETED": set(),
}


def can_transition(current: str, target: str) -> bool:
    """Verifies whether a state machine transition is valid."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


DISALLOWED_CIDRS = [
    # IPv4
    ipaddress.ip_network("0.0.0.0/8"),          # Current network
    ipaddress.ip_network("10.0.0.0/8"),          # RFC 1918 Private
    ipaddress.ip_network("100.64.0.0/10"),       # RFC 6598 Carrier-Grade NAT
    ipaddress.ip_network("127.0.0.0/8"),         # RFC 1122 Loopback
    ipaddress.ip_network("169.254.0.0/16"),      # RFC 3927 Link-Local
    ipaddress.ip_network("172.16.0.0/12"),       # RFC 1918 Private
    ipaddress.ip_network("192.0.0.0/24"),        # RFC 6890 IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),        # RFC 5737 Documentation (TEST-NET-1)
    ipaddress.ip_network("192.168.0.0/16"),      # RFC 1918 Private
    ipaddress.ip_network("198.18.0.0/15"),       # RFC 2544 Benchmarking
    ipaddress.ip_network("198.51.100.0/24"),     # RFC 5737 Documentation (TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),      # RFC 5737 Documentation (TEST-NET-3)
    ipaddress.ip_network("224.0.0.0/4"),         # RFC 5771 Multicast
    ipaddress.ip_network("240.0.0.0/4"),         # RFC 1112 Reserved / Class E
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    # IPv6
    ipaddress.ip_network("::1/128"),             # Loopback
    ipaddress.ip_network("::/128"),              # Unspecified
    ipaddress.ip_network("64:ff9b::/96"),        # IPv4-IPv6 Translation
    ipaddress.ip_network("100::/64"),            # Discard-Only Prefix
    ipaddress.ip_network("2001:db8::/32"),       # Documentation
    ipaddress.ip_network("fc00::/7"),            # Unique Local Unicast (ULA)
    ipaddress.ip_network("fe80::/10"),           # Link-Local Unicast
    ipaddress.ip_network("ff00::/8"),            # Multicast
]


def is_ip_allowed(ip_str: str) -> bool:
    """Verifies that an IP string represents a globally routable, public address."""
    try:
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
        for net in DISALLOWED_CIDRS:
            if ip in net:
                return False
        return True
    except ValueError:
        return False


class PinningSafeNetworkBackend(AnyIOBackend):
    """
    Enforces anti-SSRF and DNS rebinding elimination at socket connection time.
    Resolves hostnames, validates all returned addresses against prohibited ranges,
    and opens TCP connection directly to the verified public IP.
    SNI and TLS certificate verification remain bound to the target hostname.
    """
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Any] = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            ip_obj = ipaddress.ip_address(host)
            if not is_ip_allowed(str(ip_obj)):
                raise httpcore.ConnectError(f"Blocked SSRF attempt: {host} is a prohibited IP.")
            target_ip = str(ip_obj)
        except ValueError:
            # It's a hostname, resolve via getaddrinfo
            try:
                addr_info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            except Exception as e:
                raise httpcore.ConnectError(f"DNS resolution failed for {host}: {e}") from e

            if not addr_info:
                raise httpcore.ConnectError(f"No DNS records found for host '{host}'")

            valid_ips: list[str] = []
            for _, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                if not is_ip_allowed(ip_str):
                    raise httpcore.ConnectError(
                        f"Blocked SSRF / DNS rebinding: host '{host}' resolved to prohibited IP '{ip_str}'"
                    )
                valid_ips.append(ip_str)

            if not valid_ips:
                raise httpcore.ConnectError(f"No public routable IPs for host '{host}'")
            target_ip = valid_ips[0]

        return await super().connect_tcp(
            target_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class SafeAsyncTransport(httpx.AsyncHTTPTransport):
    """Custom HTTPX transport enforcing socket-level IP pinning and anti-SSRF guards."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool._network_backend = PinningSafeNetworkBackend()


def is_safe_public_destination(url_str: str) -> bool:
    """
    Strict Anti-SSRF / URL Pre-Flight Guard.
    Ensures URL uses HTTPS and resolves strictly to public routable IP addresses.
    Rejects private, loopback, link-local, multicast, or internal host destinations.
    """
    try:
        parsed = urllib.parse.urlparse(url_str)
        if parsed.scheme.lower() != "https":
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        lower_host = hostname.lower()
        if lower_host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False

        # If hostname is a raw IP literal
        try:
            ip_obj = ipaddress.ip_address(lower_host)
            return is_ip_allowed(str(ip_obj))
        except ValueError:
            pass  # It is a domain name, resolve via DNS

        # Resolve host to IP addresses
        addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        if not addr_info:
            return False

        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if not is_ip_allowed(ip_str):
                logger.warning(
                    f"Blocked SSRF attempt: host '{hostname}' resolved to prohibited IP {ip_str}"
                )
                return False

        return True
    except Exception as err:
        logger.warning(f"SSRF validation exception for '{url_str}': {err}")
        return False


def safe_atomic_move(src: Path, dest: Path) -> None:
    """
    Atomically moves a file from src to dest.
    If on the same filesystem, os.replace is used directly (atomic on POSIX and NTFS).
    If across different filesystems or drives (WinError 17), uses a safe two-phase strategy:
    1. Stream to a temporary file on the destination filesystem (.tmp_{nonce}).
    2. Explicit flush and fsync.
    3. Verify destination temporary file size matches source file size.
    4. Atomically rename on destination filesystem using os.replace.
    5. Unlink source file only after successful atomic rename.
    """
    try:
        os.replace(src, dest)
    except OSError as err:
        if getattr(err, "winerror", None) == 17 or "different disk drive" in str(err).lower():
            temp_dest = dest.with_suffix(f"{dest.suffix}.tmp_{time.time_ns()}")
            try:
                with open(src, "rb") as f_src, open(temp_dest, "wb") as f_dst:
                    while chunk := f_src.read(1024 * 1024):
                        f_dst.write(chunk)
                    f_dst.flush()
                    os.fsync(f_dst.fileno())

                src_size = src.stat().st_size
                dst_size = temp_dest.stat().st_size
                if src_size != dst_size:
                    temp_dest.unlink(missing_ok=True)
                    raise IOError(f"Cross-drive move verification failed: {src_size} bytes != {dst_size} bytes")

                # Atomic rename on destination filesystem
                os.replace(temp_dest, dest)
                # Cleanup source only after destination atomic replace succeeded
                src.unlink(missing_ok=True)
            except Exception:
                temp_dest.unlink(missing_ok=True)
                raise
        else:
            raise


class DownloadManager:
    """
    Centralized download supervisor with asynchronous worker loop, persistent SQLite
    queue, HTTP Range resumption, and SSE progress broadcasting.
    """

    def __init__(
        self,
        downloads_dir: Optional[Path] = None,
        models_dir: Optional[Path] = None,
        custom_client: Optional[httpx.AsyncClient] = None,
    ):
        self.downloads_dir = downloads_dir or settings.DOWNLOADS_DIR
        self.models_dir = models_dir or settings.MODELS_DIR
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._client = custom_client

        # Worker synchronization
        self._active_task_id: Optional[str] = None
        self._pause_events: dict[str, asyncio.Event] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._speed_trackers: dict[str, deque[tuple[float, int]]] = {}
        self._sse_listeners: set[asyncio.Queue] = set()

        self._worker_task: Optional[asyncio.Task] = None
        self._wake_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._is_running = False

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._client and not self._client.is_closed:
            return self._client
        transport = SafeAsyncTransport(
            verify=True,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=6.0, read=30.0),
            headers={
                "User-Agent": "MustangAI/0.1.0 (Windows NT 10.0; Win64; x64; Local-First)",
            },
            follow_redirects=False,  # Manual redirect validation for anti-SSRF
        )
        return self._client

    async def start(self) -> None:
        """Starts the background worker loop and recovers stale download tasks."""
        if self._is_running:
            return
        self._is_running = True
        await self.recover_stale_tasks()
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Mustang AI Download Manager worker started.")

    async def stop(self) -> None:
        """Gracefully cancels active downloads and terminates the worker task."""
        self._is_running = False
        self._wake_event.set()
        if self._active_task_id:
            await self.pause_download(self._active_task_id)
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        logger.info("Mustang AI Download Manager worker stopped.")

    def subscribe_sse(self) -> asyncio.Queue:
        """Subscribes an SSE client queue to receive progress event updates."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._sse_listeners.add(q)
        return q

    def unsubscribe_sse(self, q: asyncio.Queue) -> None:
        """Removes a disconnected SSE client queue."""
        self._sse_listeners.discard(q)

    async def broadcast_progress(self, task_data: dict[str, Any]) -> None:
        """Dispatches real-time progress events to all connected SSE clients."""
        if not self._sse_listeners:
            return
        dead: list[asyncio.Queue] = []
        for q in self._sse_listeners:
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(task_data)
            except Exception:
                dead.append(q)
        for d in dead:
            self._sse_listeners.discard(d)

    # ---------------- State Management & Persistence ----------------

    async def update_task_state(
        self,
        task_id: str,
        target_state: str,
        error_message: Optional[str] = None,
        **extra_fields: Any,
    ) -> None:
        """Enforces state machine transitions and updates SQLite."""
        db = await get_db()
        try:
            row = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task_id,))
            if not row:
                return
            current_state = row[0]["status"]
            if current_state != target_state and not can_transition(current_state, target_state):
                logger.warning(
                    f"Invalid download state transition: '{current_state}' -> '{target_state}' for task {task_id}"
                )
                return

            now = time.time()
            set_clauses = ["status = ?", "updated_at = ?"]
            params: list[Any] = [target_state, now]

            if error_message is not None:
                set_clauses.append("error_message = ?")
                params.append(error_message)

            for col, val in extra_fields.items():
                set_clauses.append(f"{col} = ?")
                params.append(val)

            params.append(task_id)
            query = f"UPDATE downloads SET {', '.join(set_clauses)} WHERE id = ?;"
            await db.execute(query, tuple(params))
            await db.commit()

            # Broadcast update
            updated_rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task_id,))
            if updated_rows:
                await self.broadcast_progress(dict(updated_rows[0]))
        finally:
            await db.close()

    async def recover_stale_tasks(self) -> None:
        """
        Reconciles interrupted downloads, verifies incomplete tasks, and recovers state
        after an unexpected application restart.
        """
        db = await get_db()
        try:
            # 1. Recover interrupted DOWNLOADING tasks -> PAUSED
            await db.execute(
                """
                UPDATE downloads
                SET status = 'PAUSED', error_message = 'Interrupted by application restart'
                WHERE status = 'DOWNLOADING';
                """
            )

            # 2. Reconcile stale VERIFYING tasks
            verifying_rows = [
                dict(r) for r in await db.execute_fetchall("SELECT * FROM downloads WHERE status = 'VERIFYING';")
            ]
            await db.commit()
        finally:
            await db.close()

        # Process stale verifying tasks with clean transactions
        for task_dict in verifying_rows:
            dest = Path(task_dict["destination_path"])
            part = self.downloads_dir / f"{task_dict['id']}.part"
            expected_sha = (task_dict.get("sha256_expected") or "").strip().lower()

            if dest.exists() and dest.stat().st_size > 0:
                def hash_dest():
                    h = hashlib.sha256()
                    with open(dest, "rb") as f:
                        while c := f.read(1024 * 1024):
                            h.update(c)
                    return h.hexdigest().lower()

                dest_sha = await asyncio.to_thread(hash_dest)
                if expected_sha and dest_sha != expected_sha:
                    dest.unlink(missing_ok=True)
                    await self.update_task_state(
                        task_dict["id"],
                        "FAILED",
                        integrity_status="FAILED_CHECKSUM_MISMATCH",
                        error_message="Checksum mismatch on recovered destination file",
                    )
                    continue

                integrity_status = "VERIFIED" if expected_sha else "UNVERIFIED_NO_CHECKSUM"
                registered = await self._register_completed_model({
                    **task_dict,
                    "sha256_actual": dest_sha,
                })
                if registered:
                    await self.update_task_state(
                        task_dict["id"],
                        "COMPLETED",
                        integrity_status=integrity_status,
                        sha256_actual=dest_sha,
                        downloaded_bytes=dest.stat().st_size,
                        total_bytes=dest.stat().st_size,
                        speed_bps=0,
                        eta_seconds=0,
                    )
                else:
                    await self.update_task_state(
                        task_dict["id"],
                        "FAILED",
                        error_message="Failed to parse GGUF metadata on recovered destination file",
                    )

            elif part.exists() and part.stat().st_size > 0:
                await self.update_task_state(
                    task_dict["id"],
                    "PAUSED",
                    error_message="Verification interrupted. Resume to verify.",
                )
            else:
                await self.update_task_state(
                    task_dict["id"],
                    "FAILED",
                    error_message="Staging file missing after restart",
                )

    # ---------------- Task Creation & Queuing ----------------

    async def create_download(
        self,
        repo_id: str,
        filename: str,
        revision: str = "main",
        expected_sha256: Optional[str] = None,
        total_bytes: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Validates request parameters, checks disk space & duplicate tasks,
        and enqueues a new model download task.
        """
        clean_repo_id = validate_hf_repo_id(repo_id)
        clean_filename = validate_hf_filename(filename)

        clean_revision = (revision or "main").strip()
        if not clean_revision or ".." in clean_revision or "/" in clean_revision or "\\" in clean_revision:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid revision identifier.",
            )

        # Construct canonical Hugging Face URL
        canonical_url = f"https://huggingface.co/{clean_repo_id}/resolve/{clean_revision}/{clean_filename}"
        if not is_safe_public_destination(canonical_url):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Security violation: Canonical URL failed public destination checks.",
            )

        # Destination path: models/{repo_folder}/{filename}
        repo_folder = clean_repo_id.replace("/", "--")
        destination_dir = self.models_dir / repo_folder
        destination_path = destination_dir / clean_filename

        db = await get_db()
        try:
            # 1. Check if model is already indexed or exists on disk
            if destination_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Model '{clean_filename}' already exists on disk at {destination_path.name}.",
                )

            # 2. Check for duplicate queued or downloading task
            existing = await db.execute_fetchall(
                """
                SELECT id, status FROM downloads
                WHERE repo_id = ? AND filename = ? AND status IN ('QUEUED', 'DOWNLOADING', 'PAUSED');
                """,
                (clean_repo_id, clean_filename),
            )
            if existing:
                task_match = existing[0]
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Download task '{task_match['id']}' for this model is already {task_match['status']}.",
                )

            # 3. Check queue capacity
            active_or_queued = await db.execute_fetchall(
                "SELECT COUNT(id) AS count FROM downloads WHERE status = 'QUEUED';"
            )
            queued_count = active_or_queued[0]["count"] if active_or_queued else 0
            if queued_count >= MAX_QUEUED_DOWNLOADS:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Download queue is full (maximum {MAX_QUEUED_DOWNLOADS} queued tasks).",
                )

            # 4. Check available storage space
            usage = shutil.disk_usage(str(settings.MODELS_DIR))
            free_bytes = usage.free
            required_bytes = (total_bytes or 500 * 1024 * 1024) + DISK_SAFETY_MARGIN_BYTES
            if free_bytes < required_bytes:
                free_mb = free_bytes // (1024 * 1024)
                req_mb = required_bytes // (1024 * 1024)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient disk space. Available: {free_mb} MB, Required: {req_mb} MB.",
                )

            # Generate task ID
            task_id = f"dl-{hashlib.sha256(f'{clean_repo_id}/{clean_filename}/{time.time()}'.encode()).hexdigest()[:12]}"
            now = time.time()

            await db.execute(
                """
                INSERT INTO downloads (
                    id, repo_id, filename, canonical_url, destination_path,
                    total_bytes, downloaded_bytes, status, etag, last_modified,
                    error_message, sha256_expected, sha256_actual, speed_bps,
                    eta_seconds, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 'QUEUED', NULL, NULL, NULL, ?, NULL, 0, 0, ?, ?);
                """,
                (
                    task_id,
                    clean_repo_id,
                    clean_filename,
                    canonical_url,
                    str(destination_path),
                    total_bytes or 0,
                    expected_sha256,
                    now,
                    now,
                ),
            )
            await db.commit()

            rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task_id,))
            created_task = dict(rows[0])
        finally:
            await db.close()

        # Wake worker loop
        self._wake_event.set()
        await self.broadcast_progress(created_task)
        return created_task

    # ---------------- Worker & Streaming Implementation ----------------

    async def _worker_loop(self) -> None:
        """Main worker loop that sequentially processes queued download tasks."""
        while self._is_running:
            try:
                # Find oldest QUEUED download
                db = await get_db()
                try:
                    rows = await db.execute_fetchall(
                        "SELECT * FROM downloads WHERE status = 'QUEUED' ORDER BY created_at ASC LIMIT 1;"
                    )
                finally:
                    await db.close()

                if not rows:
                    self._wake_event.clear()
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=3.0)
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                    continue

                task = dict(rows[0])
                task_id = task["id"]

                # Acquire active download lock
                async with self._lock:
                    self._active_task_id = task_id
                    self._pause_events[task_id] = asyncio.Event()
                    self._cancel_events[task_id] = asyncio.Event()
                    self._speed_trackers[task_id] = deque(maxlen=20)

                await self.update_task_state(task_id, "DOWNLOADING")
                try:
                    await self._stream_and_write(task)
                except Exception as stream_err:
                    logger.error(f"Download worker encountered error on task {task_id}: {stream_err}")
                    await self.update_task_state(task_id, "FAILED", error_message=str(stream_err)[:300])
                finally:
                    async with self._lock:
                        self._active_task_id = None
                        self._pause_events.pop(task_id, None)
                        self._cancel_events.pop(task_id, None)
                        self._speed_trackers.pop(task_id, None)

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                logger.error(f"Error in download manager worker loop: {loop_err}")
                await asyncio.sleep(2.0)

    async def _stream_and_write(self, task: dict[str, Any]) -> None:
        """
        Executes HTTP GET with HTTP 206 Range resumption, chunked writes,
        periodic speed/ETA calculation, and atomic handoff to verification.
        """
        task_id = task["id"]
        # Ensure task is marked DOWNLOADING in SQLite before streaming starts
        await self.update_task_state(task_id, "DOWNLOADING")

        if task_id not in self._cancel_events:
            self._cancel_events[task_id] = asyncio.Event()
        if task_id not in self._pause_events:
            self._pause_events[task_id] = asyncio.Event()

        staging_file = self.downloads_dir / f"{task_id}.part"
        pause_event = self._pause_events.get(task_id)
        cancel_event = self._cancel_events.get(task_id)

        # Check existing partial size
        existing_size = staging_file.stat().st_size if staging_file.exists() else 0
        downloaded_bytes = existing_size
        total_bytes = task.get("total_bytes") or 0

        # Construct request headers
        headers: dict[str, str] = {
            "Accept-Encoding": "identity",  # Direct raw bytes without gzip overhead
        }
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"
            if task.get("etag"):
                headers["If-Range"] = task["etag"]

        # Follow redirects with strict SSRF validation
        current_url = task["canonical_url"]
        client = self._get_http_client()
        response: Optional[httpx.Response] = None

        for hop in range(MAX_REDIRECT_HOPS):
            if not is_safe_public_destination(current_url):
                await self.update_task_state(
                    task_id,
                    "FAILED",
                    error_message=f"SSRF violation: Redirect hop {hop} targeted unsafe destination {current_url}",
                )
                return

            req = client.build_request("GET", current_url, headers=headers)
            resp = await client.send(req, stream=True)

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                await resp.aclose()
                if not location:
                    await self.update_task_state(task_id, "FAILED", error_message="Malformed redirect location.")
                    return
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            else:
                response = resp
                break

        if not response:
            await self.update_task_state(task_id, "FAILED", error_message="Exceeded maximum redirect hops.")
            return

        # Inspect response status
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            await response.aclose()
            await self.update_task_state(
                task_id,
                "FAILED",
                error_message=f"Hugging Face rate limit (HTTP 429). Retry in {retry_after}s.",
            )
            return

        if response.status_code == 416:  # Range not satisfiable
            await response.aclose()
            if total_bytes > 0 and existing_size == total_bytes:
                # File already fully staged!
                await self._finalize_and_verify(task, staging_file)
                return
            # Truncate and restart from zero
            staging_file.write_bytes(b"")
            downloaded_bytes = 0
            existing_size = 0
            headers.pop("Range", None)
            return await self._stream_and_write(task)

        if response.status_code not in (200, 206):
            status_c = response.status_code
            await response.aclose()
            await self.update_task_state(
                task_id,
                "FAILED",
                error_message=f"Server returned HTTP {status_c}.",
            )
            return

        # Determine file size & mode
        is_partial = response.status_code == 206
        open_mode = "ab" if is_partial else "wb"

        etag = response.headers.get("ETag") or task.get("etag")
        last_modified = response.headers.get("Last-Modified") or task.get("last_modified")

        if is_partial:
            # Check validator consistency between sessions
            new_etag = response.headers.get("ETag")
            if task.get("etag") and new_etag and new_etag.strip() != task["etag"].strip():
                logger.warning(
                    f"Remote ETag changed ({task['etag']} -> {new_etag}). Remote file was modified! Restarting download from 0."
                )
                await response.aclose()
                staging_file.write_bytes(b"")
                return await self._stream_and_write({**task, "downloaded_bytes": 0, "etag": new_etag, "last_modified": last_modified})

            new_last_modified = response.headers.get("Last-Modified")
            if task.get("last_modified") and new_last_modified and new_last_modified.strip() != task["last_modified"].strip():
                logger.warning(
                    f"Remote Last-Modified changed. Remote file was modified! Restarting download from 0."
                )
                await response.aclose()
                staging_file.write_bytes(b"")
                return await self._stream_and_write({**task, "downloaded_bytes": 0, "etag": etag, "last_modified": new_last_modified})

            # Strict RFC 7233 Content-Range validation
            crange = response.headers.get("Content-Range", "").strip()
            crange_match = re.match(r"^bytes\s+(\d+)-(\d+)/((\d+)|\*)$", crange, re.IGNORECASE)
            if not crange_match:
                logger.warning(f"Malformed or missing Content-Range '{crange}'. Restarting download from 0.")
                await response.aclose()
                staging_file.write_bytes(b"")
                return await self._stream_and_write({**task, "downloaded_bytes": 0, "etag": None, "last_modified": None})

            range_start = int(crange_match.group(1))
            range_end = int(crange_match.group(2))
            total_size_str = crange_match.group(3)

            if range_start != existing_size:
                logger.warning(f"Content-Range start {range_start} != expected offset {existing_size}. Restarting download from 0.")
                await response.aclose()
                staging_file.write_bytes(b"")
                return await self._stream_and_write({**task, "downloaded_bytes": 0, "etag": None, "last_modified": None})

            if range_end < range_start:
                logger.warning(f"Content-Range end {range_end} < start {range_start}. Restarting download from 0.")
                await response.aclose()
                staging_file.write_bytes(b"")
                return await self._stream_and_write({**task, "downloaded_bytes": 0, "etag": None, "last_modified": None})

            if total_size_str != "*":
                crange_total = int(total_size_str)
                if crange_total <= range_end:
                    logger.warning(f"Content-Range total {crange_total} <= end {range_end}. Restarting download from 0.")
                    await response.aclose()
                    staging_file.write_bytes(b"")
                    return await self._stream_and_write({**task, "downloaded_bytes": 0, "etag": None, "last_modified": None})
                total_bytes = crange_total

        else:
            # Server sent 200 OK: safely truncate staging file and restart from 0
            if existing_size > 0:
                logger.info(f"Server returned HTTP 200 instead of 206 for task {task_id}. Restarting staging file from byte 0.")
            staging_file.write_bytes(b"")
            downloaded_bytes = 0
            open_mode = "wb"
            clength = response.headers.get("Content-Length")
            if clength and clength.isdigit():
                total_bytes = int(clength)

        # Stream chunks directly to disk
        last_sync_time = time.time()
        speed_tracker = self._speed_trackers.get(task_id, deque(maxlen=20))
        speed_tracker.append((time.time(), downloaded_bytes))

        try:
            # Open file in thread pool
            f = open(staging_file, open_mode)
            try:
                async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE_BYTES):
                    # Check pause / cancel events
                    if pause_event and pause_event.is_set():
                        await asyncio.to_thread(f.flush)
                        await self.update_task_state(
                            task_id,
                            "PAUSED",
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=total_bytes,
                            etag=etag,
                            last_modified=last_modified,
                            speed_bps=0,
                            eta_seconds=0,
                        )
                        return

                    if cancel_event and cancel_event.is_set():
                        await asyncio.to_thread(f.flush)
                        f.close()
                        if staging_file.exists():
                            staging_file.unlink()
                        await self.update_task_state(
                            task_id,
                            "CANCELLED",
                            error_message="Cancelled by user.",
                        )
                        return

                    if not chunk:
                        continue

                    # Write chunk
                    await asyncio.to_thread(f.write, chunk)
                    downloaded_bytes += len(chunk)

                    now = time.time()
                    speed_tracker.append((now, downloaded_bytes))

                    # Calculate rolling speed & ETA
                    speed_bps = 0.0
                    eta_seconds = 0.0
                    if len(speed_tracker) >= 2:
                        old_time, old_bytes = speed_tracker[0]
                        dt = now - old_time
                        if dt >= 0.5:
                            speed_bps = max(0.0, (downloaded_bytes - old_bytes) / dt)
                            if speed_bps > 0 and total_bytes > downloaded_bytes:
                                eta_seconds = round((total_bytes - downloaded_bytes) / speed_bps, 1)

                    # Periodic SQLite sync
                    if now - last_sync_time >= 1.0:
                        # Check disk headroom
                        usage = shutil.disk_usage(str(settings.MODELS_DIR))
                        if usage.free < (100 * 1024 * 1024):  # Under 100MB remaining
                            await self.update_task_state(
                                task_id,
                                "FAILED",
                                error_message="Disk full. Not enough free space remaining.",
                            )
                            return

                        await self.update_task_state(
                            task_id,
                            "DOWNLOADING",
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=total_bytes,
                            speed_bps=round(speed_bps, 1),
                            eta_seconds=eta_seconds,
                            etag=etag,
                            last_modified=last_modified,
                        )
                        last_sync_time = now

            finally:
                if not f.closed:
                    try:
                        await asyncio.to_thread(f.flush)
                    except ValueError:
                        pass
                    try:
                        await asyncio.to_thread(f.close)
                    except ValueError:
                        pass

        finally:
            await response.aclose()

        # Download completed: transition to verification
        expected_total = task.get("total_bytes") or 0
        await self._finalize_and_verify(
            task={
                **task,
                "total_bytes": expected_total if expected_total > 0 else total_bytes,
                "downloaded_bytes": downloaded_bytes,
            },
            staging_file=staging_file,
        )

    # ---------------- Verification & Finalization ----------------

    async def _finalize_and_verify(self, task: dict[str, Any], staging_file: Path) -> None:
        """
        Calculates SHA-256 incrementally, verifies size & checksum, moves atomically
        to destination path, and registers model into SQLite.
        """
        task_id = task["id"]
        total_bytes = task.get("total_bytes") or 0
        expected_sha = (task.get("sha256_expected") or "").strip().lower()

        await self.update_task_state(task_id, "VERIFYING", speed_bps=0, eta_seconds=0)

        # 1. Size verification
        actual_size = staging_file.stat().st_size if staging_file.exists() else 0
        if total_bytes > 0 and actual_size != total_bytes:
            staging_file.unlink(missing_ok=True)
            await self.update_task_state(
                task_id,
                "FAILED",
                integrity_status="FAILED_SIZE_MISMATCH",
                error_message=f"File size mismatch: downloaded {actual_size} bytes, expected {total_bytes} bytes.",
            )
            return

        # 2. Incremental SHA-256 calculation
        # Open staging_file in "rb" mode and hash all bytes from offset 0 through EOF
        hasher = hashlib.sha256()

        def compute_sha256():
            with open(staging_file, "rb") as sf:
                while chunk := sf.read(1024 * 1024):
                    hasher.update(chunk)
            return hasher.hexdigest().lower()

        actual_sha = await asyncio.to_thread(compute_sha256)

        # 3. Checksum comparison & trust classification
        if expected_sha:
            if actual_sha != expected_sha:
                # Quarantined / remove corrupted partial
                staging_file.unlink(missing_ok=True)
                await self.update_task_state(
                    task_id,
                    "FAILED",
                    sha256_actual=actual_sha,
                    integrity_status="FAILED_CHECKSUM_MISMATCH",
                    error_message=f"SHA-256 integrity check failed. Expected {expected_sha}, got {actual_sha}.",
                )
                return
            integrity_status = "VERIFIED"
        else:
            integrity_status = "UNVERIFIED_NO_CHECKSUM"

        # 4. Atomic file move
        final_dest = Path(task["destination_path"]).resolve()
        final_dest.parent.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(safe_atomic_move, staging_file, final_dest)

        # 5. Parse GGUF and register in models database
        registered = await self._register_completed_model({
            **task,
            "destination_path": str(final_dest),
            "sha256_actual": actual_sha,
        })

        if not registered:
            await self.update_task_state(
                task_id,
                "FAILED",
                error_message="Failed to parse GGUF metadata or register model.",
            )
            return

        # 6. Mark COMPLETED
        await self.update_task_state(
            task_id,
            "COMPLETED",
            sha256_actual=actual_sha,
            integrity_status=integrity_status,
            downloaded_bytes=actual_size,
            total_bytes=actual_size,
            speed_bps=0,
            eta_seconds=0,
        )
        logger.info(f"Model download '{task['filename']}' successfully verified and completed.")

    async def _register_completed_model(self, task: dict[str, Any]) -> bool:
        """Parses newly completed GGUF file and registers it in SQLite models table."""
        final_path = Path(task["destination_path"])
        if not final_path.exists():
            return False

        try:
            meta = await asyncio.to_thread(parse_gguf_metadata, final_path)
            model_id = generate_stable_model_id(final_path, self.models_dir)
            file_size = final_path.stat().st_size
            now = time.time()

            db = await get_db()
            try:
                await db.execute(
                    """
                    INSERT INTO models (
                        id, canonical_path, filename, repo_id, file_size_bytes, sha256,
                        architecture, quantization, parameter_count, context_length,
                        embedding_dim, block_count, head_count, head_count_kv,
                        download_origin, last_scanned_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'huggingface', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        file_size_bytes = excluded.file_size_bytes,
                        sha256 = excluded.sha256,
                        last_scanned_at = excluded.last_scanned_at;
                    """,
                    (
                        model_id,
                        str(final_path),
                        final_path.name,
                        task.get("repo_id") or "huggingface",
                        file_size,
                        task.get("sha256_actual"),
                        meta.architecture,
                        meta.quantization,
                        meta.parameter_count,
                        meta.context_length,
                        meta.embedding_dim,
                        meta.block_count,
                        meta.head_count,
                        meta.head_count_kv,
                        now,
                        now,
                    ),
                )
                await db.commit()
                return True
            finally:
                await db.close()

        except Exception as err:
            logger.error(f"Error registering completed model {final_path.name}: {err}")
            return False

    # ---------------- Actions: Pause, Resume, Cancel, Retry ----------------

    async def pause_download(self, task_id: str) -> None:
        """Pauses a queued or active download."""
        async with self._lock:
            if self._active_task_id == task_id:
                event = self._pause_events.get(task_id)
                if event:
                    event.set()
                return

        # If queued, update directly
        await self.update_task_state(task_id, "PAUSED", speed_bps=0, eta_seconds=0)

    async def resume_download(self, task_id: str) -> None:
        """Resumes a paused download by placing it back into QUEUED status."""
        await self.update_task_state(task_id, "QUEUED", error_message=None)
        self._wake_event.set()

    async def cancel_download(self, task_id: str) -> None:
        """Cancels a download, terminates active stream, and purges partial staging files."""
        async with self._lock:
            if self._active_task_id == task_id or task_id in self._cancel_events:
                event = self._cancel_events.get(task_id)
                if event:
                    event.set()
                return

        # Clean up staging file if exists and not open
        staging_file = self.downloads_dir / f"{task_id}.part"
        try:
            staging_file.unlink(missing_ok=True)
        except OSError:
            pass
        await self.update_task_state(task_id, "CANCELLED", error_message="Cancelled by user.")

    async def retry_download(self, task_id: str) -> None:
        """Retries a failed or cancelled download from zero."""
        staging_file = self.downloads_dir / f"{task_id}.part"
        staging_file.unlink(missing_ok=True)
        await self.update_task_state(
            task_id,
            "QUEUED",
            downloaded_bytes=0,
            speed_bps=0,
            eta_seconds=0,
            error_message=None,
        )
        self._wake_event.set()

    async def get_all_downloads(self) -> list[dict[str, Any]]:
        """Retrieves all downloads from database ordered by created_at DESC."""
        db = await get_db()
        try:
            rows = await db.execute_fetchall("SELECT * FROM downloads ORDER BY created_at DESC;")
            return [dict(r) for r in rows]
        finally:
            await db.close()

    async def get_download(self, task_id: str) -> Optional[dict[str, Any]]:
        """Retrieves a single download task by ID."""
        db = await get_db()
        try:
            rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task_id,))
            return dict(rows[0]) if rows else None
        finally:
            await db.close()


# Global singleton instance management
_download_manager_instance: Optional[DownloadManager] = None


def get_download_manager() -> DownloadManager:
    """Returns the active DownloadManager singleton."""
    global _download_manager_instance
    if _download_manager_instance is None:
        _download_manager_instance = DownloadManager()
    return _download_manager_instance


def set_download_manager(manager: Optional[DownloadManager]) -> None:
    """Injects or resets the active DownloadManager (useful for testing)."""
    global _download_manager_instance
    _download_manager_instance = manager


download_manager = get_download_manager()
