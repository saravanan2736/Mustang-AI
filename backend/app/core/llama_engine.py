"""
Mustang AI llama-server Process Supervisor
Manages process lifecycle, health probes, readiness detection, VRAM parsing, and cancellation.
"""

import asyncio
import gc
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator
import httpx

from app.config import settings
from app.core.engine_base import (
    ChatChunk,
    ChatCompletionRequest,
    EngineConfig,
    EngineState,
    EngineStatus,
    InferenceEngine,
)

logger = logging.getLogger("mustang.engine.llama")

# No console window for llama-server (the packaged desktop app has no console).
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _engine_environment(api_key: str) -> dict[str, str]:
    """
    Environment for llama-server: ours, minus every LLAMA_ARG_* variable. llama-server reads
    any command-line option from LLAMA_ARG_<NAME>, including LLAMA_ARG_TOOLS and
    LLAMA_ARG_AGENT, which enable its experimental built-in tools (shell commands, file writes)
    without folder limits or approval. The desktop supervisor already clears the environment;
    this also covers the browser dev mode, where the backend inherits the user's shell.
    """
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("LLAMA_ARG_")}
    env["LLAMA_API_KEY"] = api_key
    return env


class LlamaServerEngine(InferenceEngine):
    """Encapsulates the llama-server.exe process with verified lifecycle management."""

    def __init__(self, binary_path: Path | None = None):
        self._binary_path = binary_path or (settings.RUNTIME_DIR / "llama-server.exe")
        self._state = EngineState.UNLOADED
        self._config: EngineConfig | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._stderr_buffer: list[str] = []
        
        # Authoritative metrics parsed from runtime output
        self._offloaded_layers: int = 0
        self._total_layers: int = 0
        self._authoritative_vram_mb: float = 0.0
        self._error_message: str | None = None
        self._active_tokens_per_sec: float = 0.0
        
        # Concurrency & lifecycle guards
        self._lifecycle_lock = asyncio.Lock()
        self._gen_lock = asyncio.Lock()
        self._active_client: httpx.AsyncClient | None = None
        # One client per llama-server process, closed when the process stops. A new client per
        # probe or chat left a client, its SSL context and a socket buffer in reference cycles
        # (through exception tracebacks) that only Python's rare full collections freed: about
        # 1 MB of native memory per load-and-chat cycle (M4 Phase G soak).
        self._http: httpx.AsyncClient | None = None
        self._active_response: httpx.Response | None = None
        self._cancel_requested = False

        # Per-process API key for llama-server (passed via the LLAMA_API_KEY environment
        # variable, never argv), so other local processes cannot use the engine port.
        self._api_key: str = ""

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _resolve_binary(self) -> Path:
        """Finds llama-server.exe in the runtime directory (or, outside desktop mode, on PATH)."""
        if self._binary_path.exists():
            return self._binary_path

        # The desktop app runs only its bundled, verified runtime: no PATH lookup that a
        # planted llama-server.exe could satisfy.
        if not settings.DESKTOP:
            system_bin = shutil.which("llama-server") or shutil.which("llama-server.exe")
            if system_bin:
                return Path(system_bin)

        raise FileNotFoundError(
            f"llama-server executable not found at '{self._binary_path}'. "
            "Please run 'python backend/runtime/setup_runtime.py' to download verified Windows CUDA binaries."
        )

    async def start(self, config: EngineConfig) -> bool:
        """
        Thread-safe model loading & switching lifecycle.
        Acquires lifecycle lock, cleans up previous state, aborts active generations,
        spawns process, probes health, and automatically rolls back if the new model fails.
        """
        async with self._lifecycle_lock:
            return await self._execute_safe_transition(config)

    async def _execute_safe_transition(self, config: EngineConfig) -> bool:
        """
        Internal 13-step model switching sequence:
        1. Validate model target existence
        2. Record previous running config for potential rollback
        3. Cancel in-flight generation cleanly
        4. Gracefully terminate previous process
        5. Verify process exit & brief port cooldown
        6. Spawn new process with strict typed arguments
        7. Probe /health endpoint until READY
        8. On failure, execute automatic rollback to previous configuration
        9. On success, update authoritative state and launch health heartbeat
        """
        # Step 1: Validate target model file existence
        if not config.model_path.exists() or not config.model_path.is_file():
            self._error_message = f"Model file not found on disk: {config.model_path}"
            logger.error(self._error_message)
            return False

        # Step 2: Record previous running config for rollback if applicable
        previous_config: EngineConfig | None = None
        if self._state in (EngineState.READY, EngineState.GENERATING) and self._config is not None:
            previous_config = self._config

        # Step 3: Handle in-flight generation (abort stream and allow SQLite persistence to finalize)
        if self._state == EngineState.GENERATING or self._gen_lock.locked():
            logger.info("Active generation in progress during model transition. Cancelling slot...")
            await self.cancel_generation()
            for _ in range(25):
                if not self._gen_lock.locked():
                    break
                await asyncio.sleep(0.1)

        # Step 4: Gracefully stop existing process
        if self._process is not None:
            logger.info(f"Stopping previous model process PID {self._process.pid}...")
            await self._stop_process_internal()

        # Step 5: no cooldown before respawning. Our connections are closed before the old server
        # is stopped, so the port can be bound again at once (55 back-to-back switches on one port,
        # some mid-generation: no bind failure). A delay also made CUDA start-up slower: llama-server
        # started >= 0.3 s after the previous one exited took ~600 ms to its first output line
        # instead of ~190 ms (M4 Phase G).

        # Step 6 & 7: Spawn new process and wait for readiness
        success = await self._spawn_and_verify(config)
        if success:
            self._config = config
            self._state = EngineState.READY
            self._error_message = None
            self._health_task = asyncio.create_task(self._health_loop())
            logger.info(f"Model successfully loaded: {config.model_path.name}")
            return True

        # Step 8: On failure, attempt automatic rollback if previous model was running
        fail_msg = self._error_message or "llama-server process failed readiness verification."
        await self._stop_process_internal()

        if previous_config is not None:
            logger.warning(
                f"Model switch to '{config.model_path.name}' failed ({fail_msg}). "
                f"Initiating automatic rollback to previous configuration '{previous_config.model_path.name}'..."
            )
            await asyncio.sleep(0.3)
            rollback_ok = await self._spawn_and_verify(previous_config)
            if rollback_ok:
                self._config = previous_config
                self._state = EngineState.READY
                self._health_task = asyncio.create_task(self._health_loop())
                self._error_message = (
                    f"Switch to '{config.model_path.name}' failed: {fail_msg}. "
                    f"Successfully rolled back to '{previous_config.model_path.name}'."
                )
                logger.info(self._error_message)
                return False
            else:
                self._state = EngineState.ERROR
                self._error_message = (
                    f"Switch to '{config.model_path.name}' failed: {fail_msg}. "
                    f"Automatic rollback to '{previous_config.model_path.name}' also failed."
                )
                logger.error(self._error_message)
                return False
        else:
            self._state = EngineState.ERROR
            self._error_message = f"Failed to start model '{config.model_path.name}': {fail_msg}"
            logger.error(self._error_message)
            return False

    async def _spawn_and_verify(self, config: EngineConfig) -> bool:
        """Executes llama-server and waits for /health readiness probe."""
        self._state = EngineState.STARTING
        self._error_message = None
        self._stderr_buffer.clear()
        self._offloaded_layers = 0
        self._total_layers = 0
        self._authoritative_vram_mb = 0.0

        try:
            exe_path = self._resolve_binary()
        except FileNotFoundError as e:
            self._error_message = str(e)
            return False

        # Build command with strict typed arguments (shell=False)
        cmd = [
            str(exe_path),
            "-m", str(config.model_path),
            "-c", str(config.n_ctx),
            "-ngl", str(config.n_gpu_layers),
            "-b", str(config.n_batch),
            "--host", config.host,
            "--port", str(config.port),
            "--no-webui",   # the engine port serves the API only
            "--offline",    # never fetch models or other data from the network
            "--no-agent",   # keep llama-server's own experimental tools (shell, file write) off
            "-v",
        ]
        if config.flash_attention:
            cmd.extend(["--flash-attn", "on"])

        logger.info(f"Spawning llama-server: {' '.join(cmd)}")

        await self._close_http()
        self._http = httpx.AsyncClient(timeout=120.0, trust_env=False)

        try:
            self._api_key = secrets.token_hex(32)
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=_engine_environment(self._api_key),
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as err:
            self._error_message = f"Failed to execute process: {err}"
            logger.error(self._error_message)
            return False

        # Start stdout/stderr monitoring task
        ready_event = asyncio.Event()
        self._stdout_task = asyncio.create_task(self._monitor_output(ready_event))

        # Wait for readiness probe or timeout
        deadline = time.time() + settings.ENGINE_STARTUP_TIMEOUT_SECONDS
        listen_fallback = time.time() + 5.0
        is_ready = False

        while time.time() < deadline:
            # Check if process died during startup
            if self._process.returncode is not None:
                tail_err = "\n".join(self._stderr_buffer[-15:])
                self._error_message = f"llama-server crashed during startup (code {self._process.returncode}):\n{tail_err}"
                logger.error(self._error_message)
                return False

            # Probe only once llama-server reports that it is listening (or after a fallback
            # delay, in case a future version words it differently): every failed connect
            # attempt leaves a connection object and traceback behind for the garbage collector.
            if not ready_event.is_set() and time.time() < listen_fallback:
                try:
                    await asyncio.wait_for(ready_event.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
                continue

            # Probe /health endpoint. On Windows a connect to a loopback port that is not
            # listening yet is retried for about a second instead of being refused, so keep
            # the connect timeout short; a listening port accepts in well under 1 ms.
            try:
                resp = await self._http.get(
                    f"http://{config.host}:{config.port}/health", timeout=httpx.Timeout(1.0, connect=0.05)
                )
                if resp.status_code == 200:
                    is_ready = True
                    break
            except Exception:
                pass

            await asyncio.sleep(0.1)

        if not is_ready:
            self._error_message = f"llama-server startup timed out after {settings.ENGINE_STARTUP_TIMEOUT_SECONDS}s."
            logger.error(self._error_message)
            return False

        logger.info(f"llama-server is READY on http://{config.host}:{config.port} (PID: {self._process.pid})")
        return True

    async def _monitor_output(self, ready_event: asyncio.Event):
        """Reads stdout/stderr stream, parses VRAM metrics, and catches crash messages."""
        if not self._process or not self._process.stdout:
            return

        layer_pattern = re.compile(r"(?:llm_)?load_tensors:\s+offloaded\s+(\d+)/(\d+)\s+layers to GPU", re.IGNORECASE)
        vram_pattern = re.compile(r"(?:(?:llm_load_tensors:\s+VRAM required:)|(?:CUDA\d*\s+model buffer size\s+=))\s+([\d\.]+)\s+MiB", re.IGNORECASE)

        try:
            while True:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                self._stderr_buffer.append(line)
                if len(self._stderr_buffer) > 200:
                    self._stderr_buffer.pop(0)

                # Parse layer offloading
                m_layer = layer_pattern.search(line)
                if m_layer:
                    self._offloaded_layers = int(m_layer.group(1))
                    self._total_layers = int(m_layer.group(2))
                    logger.info(f"Authoritative GPU Layers: {self._offloaded_layers}/{self._total_layers}")

                # Parse authoritative VRAM
                m_vram = vram_pattern.search(line)
                if m_vram:
                    self._authoritative_vram_mb = float(m_vram.group(1))
                    logger.info(f"Authoritative VRAM Required: {self._authoritative_vram_mb} MiB")

                # Detect readiness sentinel
                if "HTTP server listening" in line or "server is listening on" in line or "listening on http" in line:
                    ready_event.set()

                # Detect OOM
                if "CUDA error: out of memory" in line or "failed to allocate" in line:
                    self._error_message = f"CUDA Out of Memory: {line}"
                    logger.error(self._error_message)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Output monitor stopped: {e}")

    async def _health_loop(self):
        """Periodic heartbeat confirming process and HTTP endpoint are responsive."""
        while self._state in (EngineState.READY, EngineState.GENERATING):
            await asyncio.sleep(settings.ENGINE_HEALTH_INTERVAL_SECONDS)
            if not self._process or self._process.returncode is not None:
                self._state = EngineState.ERROR
                exit_code = self._process.returncode if self._process else "None"
                tail = "\n".join(self._stderr_buffer[-10:])
                self._error_message = f"llama-server terminated unexpectedly (code {exit_code}):\n{tail}"
                logger.error(self._error_message)
                break

            try:
                resp = await self._http.get(f"http://{self._config.host}:{self._config.port}/health", timeout=1.5)
                if resp.status_code != 200:
                    logger.warning(f"Healthcheck returned non-200: {resp.status_code}")
            except Exception as e:
                logger.warning(f"Health probe error: {e}")

    async def _stop_process_internal(self) -> bool:
        """Internal process termination helper called by lifecycle supervisor."""
        self._state = EngineState.UNLOADING
        if self._health_task:
            self._health_task.cancel()
            self._health_task = None
        if self._stdout_task:
            self._stdout_task.cancel()
            self._stdout_task = None
        # Close our connections before the server goes away, so TIME_WAIT stays on our
        # ephemeral ports and the engine port can be bound again at once.
        await self._close_http()

        if self._process:
            if self._process.returncode is None:
                logger.info(f"Stopping llama-server PID {self._process.pid}...")
                try:
                    # Try graceful termination
                    self._process.terminate()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        logger.warning("llama-server unresponsive to terminate, issuing kill...")
                        self._process.kill()
                        await self._process.wait()
                except Exception as e:
                    logger.error(f"Error terminating process: {e}")

            # Drain residual stdout stream to prevent pipe handle leaks on Windows
            if self._process.stdout:
                try:
                    while not self._process.stdout.at_eof():
                        data = await asyncio.wait_for(self._process.stdout.read(4096), timeout=0.1)
                        if not data:
                            break
                except Exception:
                    pass

        self._process = None
        self._state = EngineState.UNLOADED
        self._config = None
        # llama-server drops the connection after every streamed reply, and httpcore leaves each
        # dropped connection (with its socket buffers) in a reference cycle that only a full
        # collection frees; Python 3.11 runs those rarely. One pass per unload/switch (tens of ms)
        # keeps a session with many loads flat (M4 Phase G soak).
        gc.collect()
        logger.info("llama-server stopped and unloaded.")
        return True

    async def _close_http(self) -> None:
        client, self._http = self._http, None
        if client is not None:
            try:
                await client.aclose()
            except Exception as e:
                logger.debug(f"HTTP client close note: {e}")

    async def stop(self) -> bool:
        """Controlled thread-safe shutdown acquiring lifecycle lock."""
        async with self._lifecycle_lock:
            return await self._stop_process_internal()

    async def get_status(self) -> EngineStatus:
        if self._state == EngineState.UNLOADED:
            return EngineStatus(state=EngineState.UNLOADED)

        return EngineStatus(
            state=self._state,
            model_name=self._config.model_path.name if self._config else None,
            model_path=str(self._config.model_path) if self._config else None,
            gpu_layers_requested=self._config.n_gpu_layers if self._config else 0,
            gpu_layers_offloaded=self._offloaded_layers,
            context_size=self._config.n_ctx if self._config else 0,
            vram_allocated_mb=self._authoritative_vram_mb,
            pid=self._process.pid if self._process else None,
            port=self._config.port if self._config else None,
            error_message=self._error_message,
            active_tokens_per_sec=self._active_tokens_per_sec,
        )

    async def stream_chat(self, request: ChatCompletionRequest) -> AsyncIterator[ChatChunk]:
        """Streams tokens from llama-server's native OpenAI-compatible completions API."""
        if self._state != EngineState.READY:
            raise RuntimeError(f"Engine is not in READY state (current: {self._state})")

        # Reject concurrent request if already generating
        if self._gen_lock.locked():
            raise RuntimeError("Concurrent request rejected: Engine is actively generating. Wait or cancel stream.")

        async with self._gen_lock:
            self._state = EngineState.GENERATING
            url = f"http://{self._config.host}:{self._config.port}/v1/chat/completions"
            
            payload = {
                "model": self._config.model_path.name,
                "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                "temperature": request.temperature,
                "top_p": request.top_p,
                "top_k": request.top_k,
                "max_tokens": request.max_tokens,
                "repeat_penalty": request.repeat_penalty,
                "stream": True,
            }

            start_time = time.time()
            token_count = 0
            self._cancel_requested = False

            try:
                client = self._http
                self._active_client = client
                async with client.stream("POST", url, json=payload, headers=self._auth_headers()) as resp:
                    self._active_response = resp
                    if resp.status_code != 200:
                        err_body = await resp.aread()
                        raise RuntimeError(f"llama-server error {resp.status_code}: {err_body.decode('utf-8')}")

                    async for line in resp.aiter_lines():
                        if self._cancel_requested:
                            break
                        if not line or not line.startswith("data:"):
                            continue

                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break

                        try:
                            chunk_json = json.loads(data_str)
                            choices = chunk_json.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            content = delta.get("content") or ""
                            finish_reason = choices[0].get("finish_reason")

                            if content:
                                token_count += 1
                                elapsed = max(0.01, time.time() - start_time)
                                self._active_tokens_per_sec = round(token_count / elapsed, 1)

                            yield ChatChunk(
                                id=chunk_json.get("id", "chunk"),
                                model=self._config.model_path.name,
                                delta_content=content,
                                finish_reason=finish_reason,
                            )
                        except json.JSONDecodeError:
                            continue
            except Exception:
                # Closing the upstream stream (cancel_generation) surfaces as a read error here.
                if not self._cancel_requested:
                    raise
            finally:
                self._active_response = None
                self._active_client = None
                self._state = EngineState.READY

            if self._cancel_requested:
                logger.info("Generation cancelled; upstream stream closed")
                yield ChatChunk(id="cancelled", model=self._config.model_path.name, delta_content="", finish_reason="stop")

    async def cancel_generation(self) -> bool:
        """
        Stops the in-flight generation by closing the upstream HTTP stream: llama-server
        cancels a task when its client disconnects (verified on b12000, slot idle within
        ~0.15 s). There is no slot "cancel" action; /slots/{id}?action=cancel returns 501.
        """
        if not self._config:
            return False

        response = self._active_response
        if response is not None:
            logger.info("Cancelling active generation (closing upstream stream)...")
            self._cancel_requested = True
            try:
                await response.aclose()
            except Exception as e:
                logger.debug(f"Upstream stream close note: {e}")
        return True
