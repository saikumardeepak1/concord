"""Test fixtures. These tests do NOT call the live model API; they exercise
the deterministic parts of the system (intake, PII, chunking, action service
with mocked verifier, etc.).
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault("CONCORD_DB_URL", f"sqlite+aiosqlite:///{tempfile.gettempdir()}/concord-test.db")
os.environ.setdefault("CONCORD_VERIFICATION_ENABLED", "false")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def _init_db():
    from concord.state import init_db
    await init_db()
    yield
