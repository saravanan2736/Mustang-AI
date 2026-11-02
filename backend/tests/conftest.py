"""
Mustang AI Pytest Fixtures
Configures test client, mock engine, and test environment.
"""

import os
import pytest
from httpx import ASGITransport, AsyncClient

# Set test environment flags
os.environ["MUSTANG_USE_MOCK_ENGINE"] = "1"

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
async def async_client(mock_engine):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:18000") as client:
        yield client
