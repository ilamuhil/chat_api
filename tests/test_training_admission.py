"""Exercise overlapping route calls with transaction-scoped lock semantics."""

import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.training import queue_training
from app.models.chat_db_models import TrainingJobs


def result(value=None):
    rows = MagicMock()
    rows.first.return_value = value
    return rows


class TrainingAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def run_overlapping(self, *, different_bots=False, fail_first_commit=False):
        locks = {}
        jobs = []
        keys = []
        both_arrived = asyncio.Event()
        config = SimpleNamespace(id=uuid.uuid4())
        sessions = []

        def make_chat(index):
            chat = MagicMock(spec=AsyncSession)
            held = None
            pending = None

            async def execute(statement, params):
                nonlocal held
                self.assertEqual(
                    str(statement), "SELECT pg_advisory_xact_lock(:lock_key)"
                )
                key = params["lock_key"]
                self.assertGreaterEqual(key, -(2**63))
                self.assertLess(key, 2**63)
                keys.append(key)
                if len(keys) == 2:
                    both_arrived.set()
                held = locks.setdefault(key, asyncio.Lock())
                await held.acquire()

            async def scalars(statement):
                # Both requests reach admission before either can create a job.
                await asyncio.wait_for(both_arrived.wait(), 1)
                if statement.column_descriptions[0]["entity"] is TrainingJobs:
                    self.assertTrue(held.locked())
                    params = statement.compile().params
                    return result(
                        next(
                            (job for job in jobs if job.bot_id == params["bot_id_1"]),
                            None,
                        )
                    )
                return result(config)

            def add(job):
                nonlocal pending
                pending = job

            async def release():
                nonlocal held
                if held is not None:
                    held.release()
                    held = None

            async def commit():
                if fail_first_commit and index == 0:
                    raise RuntimeError("commit failed")
                jobs.append(pending)
                await release()

            chat.execute.side_effect = execute
            chat.scalars.side_effect = scalars
            chat.add.side_effect = add
            chat.commit.side_effect = commit
            chat.rollback.side_effect = release
            sessions.append(chat)
            return chat

        bot = uuid.uuid4()

        async def request(index):
            bot_id = uuid.uuid4() if different_bots else bot
            source = SimpleNamespace(
                id=uuid.uuid4(), status="created", error_message=[]
            )
            dashboard = MagicMock(spec=AsyncSession)
            rows = MagicMock()
            rows.all.return_value = [source]
            dashboard.scalars.return_value = rows
            req = MagicMock()
            req.state.claims = {"organization_id": "test-org"}
            req.json = AsyncMock(
                return_value={"bot_id": str(bot_id), "source_ids": [str(source.id)]}
            )
            chat = make_chat(index)
            try:
                return await queue_training(req, dashboard, chat)
            finally:
                # The request dependency closes/rolls back unfinished transactions.
                await chat.rollback()

        with (
            patch("app.api.routes.training.Queue") as queue,
            patch(
                "app.api.routes.training._record_queue_failure",
                AsyncMock(return_value=[{"message": "commit failed"}]),
            ),
        ):
            responses = await asyncio.wait_for(
                asyncio.gather(request(0), request(1)), 2
            )
            return responses, jobs, keys, queue.return_value.enqueue.call_count

    async def test_same_bot_different_sources_admits_only_one_job(self):
        responses, jobs, keys, dispatched = await self.run_overlapping()
        self.assertEqual(
            sorted(response.status_code for response in responses), [200, 409]
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(dispatched, 1)
        self.assertEqual(keys[0], keys[1])

    async def test_different_bots_can_both_be_admitted(self):
        responses, jobs, keys, dispatched = await self.run_overlapping(
            different_bots=True
        )
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(dispatched, 2)
        self.assertNotEqual(keys[0], keys[1])

    async def test_rollback_releases_admission_for_waiting_request(self):
        with self.assertLogs("app.api.routes.training", level="ERROR"):
            responses, jobs, _, dispatched = await self.run_overlapping(
                fail_first_commit=True
            )
        self.assertEqual(
            sorted(response.status_code for response in responses), [200, 500]
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(dispatched, 1)
