"""
Mustang AI Pytest Fixtures
Configures test client, mock engine, and test environment.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

# Set test environment flags
os.environ["MUSTANG_USE_MOCK_ENGINE"] = "1"

# Isolate the database, session token file and temporary model files from the developer's
# real backend/run directory. Must happen before app.config is imported.
if "MUSTANG_RUN_DIR" not in os.environ:
    _test_run_dir = tempfile.mkdtemp(prefix="mustang-tests-")
    os.environ["MUSTANG_RUN_DIR"] = _test_run_dir
    atexit.register(shutil.rmtree, _test_run_dir, ignore_errors=True)

# Never let tests touch the real models folder: tests that delete or prune models must only
# ever reach a private directory. Real GGUF files are exposed to it as hard links (same
# volume required), so model-dependent tests still find them, while deleting a link — or
# removing the whole directory afterwards — leaves the user's file intact.
_REAL_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
if "MUSTANG_MODELS_DIR" not in os.environ:
    _test_models_dir = Path(tempfile.mkdtemp(prefix=".mustang-test-models-", dir=_REAL_MODELS_DIR.parent))
    # Only the small test model: the tests scan this folder, and parsing every multi-GB model the
    # user keeps in models/ made each scan take minutes.
    for _gguf in _REAL_MODELS_DIR.glob("qwen2.5-0.5b-instruct-q4_k_m.gguf"):
        try:
            os.link(_gguf, _test_models_dir / _gguf.name)
        except OSError:
            pass  # e.g. different volume: model-dependent tests fail, the real file is never exposed
    os.environ["MUSTANG_MODELS_DIR"] = str(_test_models_dir)
    os.environ["MUSTANG_DOWNLOADS_DIR"] = str(_test_models_dir / ".downloads")
    atexit.register(shutil.rmtree, _test_models_dir, ignore_errors=True)

from app.api.models import set_engine_instance
from app.config import settings
from app.core import security
from app.core.mock_engine import MockEngine
from app.db.database import init_db
from app.main import app


@pytest.fixture(autouse=True)
def setup_test_token():
    """Ensures a valid security token is initialized for each test run."""
    token = security.initialize_security_token()
    return token


@pytest.fixture(autouse=True)
async def setup_test_database():
    """Ensures the SQLite database schema and seed data are initialized."""
    await init_db()


@pytest.fixture
def auth_headers(setup_test_token):
    return {settings.TOKEN_HEADER_NAME: setup_test_token}


@pytest.fixture
def mock_engine():
    engine = MockEngine()
    set_engine_instance(engine)
    app.state.engine = engine
    return engine


@pytest.fixture
async def async_client(mock_engine, auth_headers):
    """Client authenticated like the real UI: every request carries X-Mustang-Token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:18000", headers=auth_headers) as client:
        yield client


@pytest.fixture
async def anon_client(mock_engine):
    """Client without a session token, for authentication-failure tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:18000") as client:
        yield client


class ScriptedEngine:
    """
    Fake engine for agent-mode tests (M5 Phase C): plays a fixed list of model turns and records
    every request. A turn is {"text": "..."} or {"calls": [(name, arguments_json), ...]}; when the
    script runs out it answers "done".
    """

    def __init__(self, script):
        from app.core.engine_base import EngineState

        self.script = list(script)
        self.requests = []
        self.cancelled = 0
        self.state = EngineState.READY

    async def start(self, config):
        return True

    async def stop(self):
        return True

    async def get_status(self):
        from app.core.engine_base import EngineState, EngineStatus

        return EngineStatus(state=EngineState.READY, model_name="scripted.gguf")

    async def cancel_generation(self):
        self.cancelled += 1
        return True

    async def stream_chat(self, request):
        from app.core.engine_base import ChatChunk, ToolCall, ToolCallFunction

        self.requests.append(request)
        turn = self.script.pop(0) if self.script else {"text": "done"}
        if turn.get("text"):
            yield ChatChunk(id="s", model="scripted.gguf", delta_content=turn["text"])
        calls = turn.get("calls")
        if calls and request.tool_choice != "none":
            yield ChatChunk(id="s", model="scripted.gguf", finish_reason="tool_calls", tool_calls=[
                ToolCall(id=f"call_{len(self.requests)}_{i}", function=ToolCallFunction(name=n, arguments=a))
                for i, (n, a) in enumerate(calls)])
        else:
            yield ChatChunk(id="s", model="scripted.gguf", finish_reason="stop")


@pytest.fixture
def scripted_engine():
    """Factory: scripted_engine([...turns]) installs a ScriptedEngine as the app's engine."""
    def make(script):
        engine = ScriptedEngine(script)
        set_engine_instance(engine)
        app.state.engine = engine
        return engine
    return make
