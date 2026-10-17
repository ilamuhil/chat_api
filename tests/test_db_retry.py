import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from psycopg import OperationalError
from sqlalchemy.exc import IntegrityError

from app.infra.db_retry import is_transient_db_error, retry_db_operation
from app.services.conversation_lifecycle import queue_pending_summaries
from app.services.cron_jobs.conversations import close_stale_conversations
from app.services.latency import TurnLatency, persist_latency


class DatabaseRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_temporary_failures_are_retried(self):
        operation = AsyncMock(
            side_effect=[OperationalError("SSL error: EOF detected"), 42]
        )
        with patch("app.infra.db_retry.asyncio.sleep", AsyncMock()):
            self.assertEqual(await retry_db_operation(operation, name="test"), 42)
        self.assertEqual(operation.await_count, 2)
        self.assertFalse(
            is_transient_db_error(OperationalError("password authentication failed"))
        )
        self.assertFalse(
            is_transient_db_error(IntegrityError("insert", {}, Exception()))
        )
        for error in (ValueError("invalid data"), asyncio.CancelledError()):
            operation = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                await retry_db_operation(operation, name="test")
            operation.assert_awaited_once()

    async def test_timeout_is_bounded_and_retried(self):
        async def slow():
            await asyncio.sleep(1)

        with patch("app.infra.db_retry.asyncio.sleep", AsyncMock()) as backoff:
            # Use a blocking event instead of sleep (patched above).
            operation = AsyncMock(side_effect=TimeoutError)
            with self.assertRaises(TimeoutError):
                await retry_db_operation(operation, name="test", attempts=2)
            self.assertEqual(operation.await_count, 2)
            backoff.assert_awaited_once()
        with self.assertRaises(TimeoutError):
            await retry_db_operation(slow, name="test", attempts=1, timeout=0.001)

    async def test_latency_retries_unknown_commit_without_duplicate_rows(self):
        rows = {}
        sessions = []
        statements = []

        @asynccontextmanager
        async def factory():
            db = AsyncMock()
            sessions.append(db)

            async def execute(statement):
                statements.append(statement)
                params = statement.compile().params
                rows.setdefault(params["id"], params)

            db.execute.side_effect = execute
            if len(sessions) == 1:
                # Simulate a successful commit whose acknowledgement was lost.
                db.commit.side_effect = OperationalError("SSL error: EOF detected")
            yield db

        timing = TurnLatency(received_at=1, response_ms=500, outcome="success")
        session = SimpleNamespace(organization_id="org", conversation_id=str(uuid4()))
        with (
            patch("app.services.latency.create_async_chat_db_session", factory),
            patch("app.services.latency.perf_counter", return_value=2),
            patch("app.infra.db_retry.asyncio.sleep", AsyncMock()),
        ):
            await persist_latency(timing, session, {"bot_id": str(uuid4())})
        self.assertEqual(len(sessions), 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[timing.event_id]["elapsed_ms"], 1000)
        self.assertEqual(rows[timing.event_id]["response_ms"], 500)
        self.assertTrue(
            all("ON CONFLICT (id) DO NOTHING" in str(s) for s in statements)
        )

    async def test_closure_retries_fresh_transaction_and_keeps_notifications(self):
        conversation = uuid4()
        sessions = []

        @asynccontextmanager
        async def factory():
            db = AsyncMock()
            sessions.append(db)
            result = MagicMock()
            result.all.return_value = [conversation]
            if len(sessions) == 1:
                db.scalars.return_value = result
            else:
                empty = MagicMock()
                empty.all.return_value = []
                db.scalars.side_effect = [result, empty]
            if len(sessions) == 1:
                db.commit.side_effect = OperationalError("SSL error: EOF detected")
            yield db

        with (
            patch(
                "app.services.cron_jobs.conversations.create_async_dashboard_db_session",
                factory,
            ),
            patch("app.infra.db_retry.asyncio.sleep", AsyncMock()),
            patch(
                "app.services.cron_jobs.conversations.enqueue_conversation_summary",
                AsyncMock(),
            ) as enqueue,
            patch(
                "app.services.cron_jobs.conversations.publish_conversation_closed",
                AsyncMock(),
            ) as publish,
            patch(
                "app.services.cron_jobs.conversations.queue_pending_summaries",
                AsyncMock(),
            ) as recover,
        ):
            await close_stale_conversations()
        self.assertEqual(len(sessions), 2)
        enqueue.assert_awaited_once_with(conversation)
        publish.assert_awaited_once_with(conversation, "system")
        recover.assert_awaited_once()

    async def test_exhausted_closure_does_not_issue_another_recovery_query(self):
        with (
            patch(
                "app.services.cron_jobs.conversations.create_async_dashboard_db_session",
                side_effect=OperationalError("failed to resolve host"),
            ) as factory,
            patch("app.infra.db_retry.asyncio.sleep", AsyncMock()),
            patch(
                "app.services.cron_jobs.conversations.queue_pending_summaries",
                AsyncMock(),
            ) as recover,
        ):
            with self.assertRaises(OperationalError):
                await close_stale_conversations()
        self.assertEqual(factory.call_count, 3)
        recover.assert_not_awaited()

    async def test_recovery_read_retries_without_duplicate_enqueues(self):
        conversation = uuid4()
        sessions = []

        @asynccontextmanager
        async def factory():
            db = AsyncMock()
            sessions.append(db)
            if len(sessions) == 1:
                db.scalars.side_effect = OperationalError("failed to resolve host")
            result = MagicMock()
            result.all.return_value = [conversation]
            db.scalars.return_value = result
            yield db

        with (
            patch(
                "app.services.conversation_lifecycle.create_async_dashboard_db_session",
                factory,
            ),
            patch("app.infra.db_retry.asyncio.sleep", AsyncMock()),
            patch(
                "app.services.conversation_lifecycle.enqueue_conversation_summary",
                AsyncMock(),
            ) as enqueue,
        ):
            await queue_pending_summaries()
        self.assertEqual(len(sessions), 2)
        enqueue.assert_awaited_once_with(conversation)

    async def test_rolled_back_closure_does_not_notify_a_reactivated_chat(self):
        conversation = uuid4()
        sessions = []

        @asynccontextmanager
        async def factory():
            db = AsyncMock()
            sessions.append(db)
            result = MagicMock()
            result.all.return_value = [conversation] if len(sessions) == 1 else []
            db.scalars.return_value = result
            if len(sessions) == 1:
                db.commit.side_effect = OperationalError("SSL error: EOF detected")
            yield db

        with (
            patch(
                "app.services.cron_jobs.conversations.create_async_dashboard_db_session",
                factory,
            ),
            patch("app.infra.db_retry.asyncio.sleep", AsyncMock()),
            patch(
                "app.services.cron_jobs.conversations.enqueue_conversation_summary",
                AsyncMock(),
            ) as enqueue,
            patch(
                "app.services.cron_jobs.conversations.publish_conversation_closed",
                AsyncMock(),
            ) as publish,
            patch(
                "app.services.cron_jobs.conversations.queue_pending_summaries",
                AsyncMock(),
            ),
        ):
            await close_stale_conversations()
        enqueue.assert_not_awaited()
        publish.assert_not_awaited()
