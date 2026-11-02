"""
Mustang AI Desktop Launcher
Entry point used by the Tauri desktop shell. Configuration arrives through MUSTANG_*
environment variables (ports, data directories, session token); see app/config.py.

Differences from run_backend.py:
  * passes the app object to uvicorn (no import string)
  * file logging with credential redaction (no console in a packaged app)
  * registers the graceful shutdown hook used by POST /api/system/shutdown
  * exits when stdin closes, as a secondary signal that the shell went away
"""

import logging
import logging.handlers
import os
import sys
import threading

LOG_FILE_BYTES = 5 * 1024 * 1024
LOG_FILE_COUNT = 5
SECRET_ENV_VARS = ("MUSTANG_SESSION_TOKEN",)


class RedactingFilter(logging.Filter):
    """Replaces known secret values in every log record."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


def configure_logging(log_dir, secrets: list[str]) -> None:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    redact = RedactingFilter(secrets)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_dir / "backend.log", maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_COUNT, encoding="utf-8"
            )
        )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(redact)
        root.addHandler(handler)


def main() -> int:
    from app.config import settings

    secrets = [settings.SESSION_TOKEN.get_secret_value()]
    # Settings hold the secret now; keep it out of the environment that llama-server and
    # other children inherit.
    for name in SECRET_ENV_VARS:
        os.environ.pop(name, None)

    configure_logging(settings.LOG_DIR, secrets)
    log = logging.getLogger("mustang.launcher")

    import uvicorn
    from app.main import app

    config = uvicorn.Config(app, host=settings.HOST, port=settings.PORT, log_config=None, access_log=False)
    server = uvicorn.Server(config)

    def request_shutdown() -> None:
        log.info("Graceful shutdown requested")
        server.should_exit = True

    app.state.request_shutdown = request_shutdown

    def watch_stdin() -> None:
        try:
            for _ in sys.stdin:
                pass
        except (OSError, ValueError):
            return
        log.info("stdin closed by parent process")
        request_shutdown()

    if sys.stdin is not None and not sys.stdin.isatty():
        threading.Thread(target=watch_stdin, name="stdin-watch", daemon=True).start()

    log.info(f"Starting Mustang AI backend {settings.VERSION} on http://{settings.HOST}:{settings.PORT} (desktop={settings.DESKTOP})")
    server.run()
    log.info("Backend process exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
