"""
Mustang AI FastAPI Main Application
Lifespan supervisor, middleware registration, and API routing.
"""

import logging
import os
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.downloads import router as downloads_router
from app.api.hf import router as hf_router
from app.api.models import router as models_router, set_engine_instance
from app.api.openai import router as openai_router
from app.api.profiles import router as profiles_router
from app.api.settings import router as settings_router
from app.api.system import router as system_router
from app.config import settings
from app.core import security
from app.core.download_manager import get_download_manager
from app.core.llama_engine import LlamaServerEngine
from app.core.mock_engine import MockEngine
from app.core.security import HostValidationMiddleware
from app.db.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mustang.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup phase
    logger.info("Initializing Mustang AI backend security and persistence...")
    security.initialize_security_token()
    if settings.DESKTOP:
        logger.info("Desktop mode: session token supplied by the desktop shell (not written to disk)")
    else:
        logger.info(f"Session token generated (recorded to {settings.RUN_DIR / '.mustang_token'})")

    await init_db()

    # Determine engine variant (Mock for tests/CI or LlamaServerEngine for live inference)
    use_mock = os.getenv("MUSTANG_USE_MOCK_ENGINE", "0") == "1"
    if use_mock:
        logger.info("Running with MockEngine (MUSTANG_USE_MOCK_ENGINE=1)")
        engine = MockEngine()
    else:
        logger.info("Running with production LlamaServerEngine supervisor")
        engine = LlamaServerEngine()

    set_engine_instance(engine)
    app.state.engine = engine

    # Start asynchronous download manager worker
    dm = get_download_manager()
    await dm.start()

    logger.info(f"Mustang AI backend ready on http://{settings.HOST}:{settings.PORT}")
    yield

    # 2. Graceful Shutdown phase
    logger.info("Initiating graceful shutdown...")
    await dm.stop()
    if hasattr(app.state, "engine"):
        await app.state.engine.stop()
    logger.info("Mustang AI shutdown complete.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)

# Register security middlewares
app.add_middleware(HostValidationMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Register routers.
# system_router protects its own routes (only /health is public); auth_router is the
# origin-checked dev bootstrap. Every other API and /v1 route requires X-Mustang-Token.
app.include_router(system_router)
app.include_router(auth_router)

_require_token = [Depends(security.verify_session_token)]
for _router in (
    models_router,
    downloads_router,
    hf_router,
    conversations_router,
    profiles_router,
    settings_router,
    chat_router,
    openai_router,
):
    app.include_router(_router, dependencies=_require_token)
