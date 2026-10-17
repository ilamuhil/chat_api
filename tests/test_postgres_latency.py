"""Opt-in telemetry retry verification; all fixture writes roll back."""

import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from psycopg import OperationalError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import async_chat_engine
from app.models.chat_db_models import ResponseLatency
from app.services.latency import TurnLatency, persist_latency


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_LATENCY_TESTS") == "1", "Opt-in PostgreSQL test"
)
class PostgresLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_commit_acknowledgement_inserts_only_one_row(self):
        timing = TurnLatency(outcome="success", response_ms=123)
        session = SimpleNamespace(
            organization_id="latency-retry-test", conversation_id=str(uuid4())
        )
        attempts = 0
        try:
            async with async_chat_engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    # Establish a real outer transaction before SAVEPOINTs.
                    await connection.execute(text("SELECT 1"))
                    sessions = async_sessionmaker(
                        connection, join_transaction_mode="create_savepoint"
                    )

                    @asynccontextmanager
                    async def factory():
                        nonlocal attempts
                        attempts += 1
                        async with sessions() as db:
                            commit = db.commit

                            async def ambiguous_commit():
                                await commit()
                                if attempts == 1:
                                    raise OperationalError("SSL error: EOF detected")

                            db.commit = ambiguous_commit
                            yield db

                    with (
                        patch(
                            "app.services.latency.create_async_chat_db_session", factory
                        ),
                        patch("app.infra.db_retry.asyncio.sleep", AsyncMock()),
                    ):
                        await persist_latency(timing, session, {"bot_id": str(uuid4())})
                    count = await connection.scalar(
                        select(func.count())
                        .select_from(ResponseLatency)
                        .where(ResponseLatency.id == timing.event_id)
                    )
                    self.assertEqual(attempts, 2)
                    self.assertEqual(count, 1)
                finally:
                    await transaction.rollback()
                count = await connection.scalar(
                    select(func.count())
                    .select_from(ResponseLatency)
                    .where(ResponseLatency.id == timing.event_id)
                )
                self.assertEqual(count, 0)
        finally:
            await async_chat_engine.dispose()
