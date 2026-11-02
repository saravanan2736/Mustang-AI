"""
Mustang AI — Phase B connectivity probe: TEST-ONLY mock backend.

Not the production backend and imports nothing from `backend/app`. It reproduces the
production security posture so the WebView2 test is meaningful:
  * binds 127.0.0.1 only, port chosen by the desktop shell
  * Starlette CORSMiddleware with an explicit origin allow-list (same middleware as production)
  * Host-header validation (same rule as production HostValidationMiddleware)
  * X-Mustang-Token header auth (test token from env, never logged)
  * SSE-style streaming via StreamingResponse

Graceful shutdown: close stdin (EOF).
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

BIND_HOST = "127.0.0.1"
TOKEN = os.environ.get("MUSTANG_PROBE_TOKEN", "")
BOOT_ID = secrets.token_hex(6)
STATS = {"options": 0, "requests": 0, "completed_streams": 0, "aborted_streams": 0, "origins": {}}


def log(msg: str) -> None:
    print(f"[mock {BOOT_ID}] {msg}", flush=True)


class HostValidation(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost"):
            log(f"rejected host={host!r} path={request.url.path}")
            return JSONResponse({"error": f"host {host!r} rejected"}, status_code=403)
        return await call_next(request)


class RequestLog(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "<none>")
        STATS["requests"] += 1
        STATS["origins"][origin] = STATS["origins"].get(origin, 0) + 1
        if request.method == "OPTIONS":
            STATS["options"] += 1
        log(f"{request.method} {request.url.path} origin={origin} host={request.headers.get('host')}")
        return await call_next(request)


def require_token(request: Request) -> JSONResponse | None:
    supplied = request.headers.get("x-mustang-token", "")
    if not TOKEN or not secrets.compare_digest(supplied, TOKEN):
        return JSONResponse({"error": "invalid or missing X-Mustang-Token"}, status_code=401)
    return None


def describe(request: Request) -> dict:
    return {
        "boot_id": BOOT_ID,
        "method": request.method,
        "origin": request.headers.get("origin"),
        "host": request.headers.get("host"),
        "server_time": time.time(),
    }


async def health(request: Request):
    return JSONResponse({"status": "ok", "boot_id": BOOT_ID})


async def public_echo(request: Request):
    return JSONResponse(describe(request))


async def echo(request: Request):
    if (denied := require_token(request)) is not None:
        return denied
    return JSONResponse(describe(request))


async def echo_json(request: Request):
    if (denied := require_token(request)) is not None:
        return denied
    body = await request.json()
    return JSONResponse({**describe(request), "received": body})


async def stats(request: Request):
    if (denied := require_token(request)) is not None:
        return denied
    return JSONResponse({**STATS, "boot_id": BOOT_ID})


def _stream(request: Request, n: int, interval: float, event_source: bool):
    async def gen():
        sent = 0
        try:
            for i in range(n):
                if await request.is_disconnected():
                    STATS["aborted_streams"] += 1
                    log(f"stream aborted by client after {sent} events")
                    return
                yield f"data: {json.dumps({'i': i, 't': time.time(), 'boot_id': BOOT_ID})}\n\n"
                sent += 1
                await asyncio.sleep(interval)
            if event_source:
                yield "event: done\ndata: {}\n\n"
            else:
                yield "data: [DONE]\n\n"
            STATS["completed_streams"] += 1
        except asyncio.CancelledError:
            STATS["aborted_streams"] += 1
            log(f"stream cancelled after {sent} events")
            raise

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def stream(request: Request):
    if (denied := require_token(request)) is not None:
        return denied
    n = int(request.query_params.get("n", "20"))
    interval = int(request.query_params.get("interval_ms", "100")) / 1000
    return _stream(request, n, interval, event_source=False)


async def stream_es(request: Request):
    # EventSource cannot send custom headers; no token here (production will move off EventSource).
    n = int(request.query_params.get("n", "5"))
    interval = int(request.query_params.get("interval_ms", "100")) / 1000
    return _stream(request, n, interval, event_source=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--allowed-origins", default="http://tauri.localhost")
    ap.add_argument("--startup-delay-ms", type=int, default=0)
    args = ap.parse_args()

    origins = [o.strip() for o in args.allowed_origins.split(",") if o.strip()]
    log(f"starting; allowed origins={origins}; token configured={'yes' if TOKEN else 'no'}")
    if args.startup_delay_ms:
        log(f"simulating slow startup: {args.startup_delay_ms} ms before binding")
        time.sleep(args.startup_delay_ms / 1000)

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/api/public-echo", public_echo),
            Route("/api/echo", echo),
            Route("/api/echo-json", echo_json, methods=["POST"]),
            Route("/api/stats", stats),
            Route("/api/stream", stream),
            Route("/api/stream-es", stream_es),
        ],
        middleware=[
            Middleware(RequestLog),
            Middleware(HostValidation),
            Middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type", "X-Mustang-Token"],
            ),
        ],
    )

    config = uvicorn.Config(app, host=BIND_HOST, port=args.port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    def watch_stdin():
        for _ in sys.stdin:
            pass
        log("stdin closed: graceful shutdown requested")
        server.should_exit = True

    threading.Thread(target=watch_stdin, daemon=True).start()
    log(f"listening on http://{BIND_HOST}:{args.port}")
    server.run()
    log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
