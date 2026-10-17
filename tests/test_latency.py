from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.messages import router
from app.db.session import get_async_chat_db
from app.services.chat import _send_json_safe
from app.services.latency import (
    TurnLatency,
    current_latency,
    latency_stage,
    mark_response_sent,
    persist_latency,
    set_latency_outcome,
    timed_stage,
    track_turn,
)


class TimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_and_response_include_receive_time_but_exclude_cleanup(self):
        timing = TurnLatency(received_at=10, enqueued_at=12)
        session = SimpleNamespace(organization_id="org", conversation_id=str(uuid4()))
        prefs = {"bot_id": str(uuid4()), "llm_model": "test-model"}
        db = AsyncMock(spec=AsyncSession)
        with (
            patch("app.services.latency.perf_counter", side_effect=[15, 20, 25]),
            patch("app.services.latency.create_async_chat_db_session") as factory,
        ):
            factory.return_value.__aenter__ = AsyncMock(return_value=db)
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            async with track_turn(timing, session, prefs):
                set_latency_outcome("success")
                mark_response_sent({"type": "message", "role": "ai"})
            row = db.add.call_args.args[0]
        self.assertEqual(row.response_ms, 10000)
        self.assertEqual(row.elapsed_ms, 15000)
        self.assertEqual(row.stages_ms["queue_wait"], 3000)
        self.assertEqual(row.outcome, "success")
        self.assertIsNone(current_latency.get())

    async def test_stages_accumulate_and_survive_exception(self):
        timing = TurnLatency()
        token = current_latency.set(timing)
        try:
            with patch("app.services.latency.perf_counter", side_effect=[1, 2, 4, 6]):
                with latency_stage("retrieval"):
                    pass
                with self.assertRaises(ValueError), latency_stage("retrieval"):
                    raise ValueError("test")
            self.assertEqual(timing.stages["retrieval"], 3000)
        finally:
            current_latency.reset(token)

    async def test_delivery_only_counts_ai_or_terminal_error(self):
        timing = TurnLatency(received_at=1)
        token = current_latency.set(timing)
        try:
            socket = AsyncMock()
            with patch("app.services.latency.perf_counter", return_value=3):
                await _send_json_safe(socket, {"type": "typing"})
                await _send_json_safe(socket, {"role": "system", "type": "message"})
                self.assertIsNone(timing.response_ms)
                await _send_json_safe(None, {"role": "ai", "type": "message"})
                self.assertIsNone(timing.response_ms)
                socket.send_json.side_effect = RuntimeError("disconnected")
                await _send_json_safe(socket, {"role": "ai", "type": "message"})
                self.assertIsNone(timing.response_ms)
                socket.send_json.side_effect = None
                await _send_json_safe(socket, {"role": "ai", "type": "message"})
                self.assertEqual(timing.response_ms, 2000)
            with patch("app.services.latency.perf_counter", return_value=9):
                mark_response_sent({"type": "error"})
            self.assertEqual(timing.response_ms, 2000)
        finally:
            current_latency.reset(token)

    async def test_timeout_child_inherits_tracker_without_leaking_other_turns(self):
        @timed_stage("agent")
        async def child():
            await asyncio.sleep(0)
            set_latency_outcome("success")
            mark_response_sent({"role": "ai"})

        async def run_one():
            timing = TurnLatency()
            async with track_turn(timing, None, None):
                await asyncio.wait_for(child(), timeout=1)
            return timing

        with patch("app.services.latency.persist_latency", AsyncMock()):
            first, second = await asyncio.gather(run_one(), run_one())
        self.assertIsNot(first.stages, second.stages)
        self.assertIn("agent", first.stages)
        self.assertIn("agent", second.stages)
        self.assertIsNone(current_latency.get())

    async def test_cancellation_records_no_reply_and_propagates(self):
        timing = TurnLatency()
        with patch("app.services.latency.persist_latency", AsyncMock()) as persist:
            with self.assertRaises(asyncio.CancelledError):
                async with track_turn(timing, None, None):
                    raise asyncio.CancelledError()
        self.assertEqual(timing.outcome, "cancelled")
        self.assertIsNone(timing.response_ms)
        persist.assert_awaited_once()
        self.assertIsNone(current_latency.get())

    async def test_telemetry_failure_does_not_fail_reply(self):
        session = SimpleNamespace(organization_id="org", conversation_id=str(uuid4()))
        with patch(
            "app.services.latency.create_async_chat_db_session",
            side_effect=RuntimeError("offline"),
        ):
            with self.assertLogs("app.services.latency", level="ERROR"):
                await persist_latency(TurnLatency(), session, {"bot_id": str(uuid4())})

    async def test_undelivered_success_is_excluded_from_success_aggregates(self):
        db = AsyncMock(spec=AsyncSession)
        session = SimpleNamespace(organization_id="org", conversation_id=str(uuid4()))
        with patch("app.services.latency.create_async_chat_db_session") as factory:
            factory.return_value.__aenter__ = AsyncMock(return_value=db)
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await persist_latency(
                TurnLatency(outcome="success"), session, {"bot_id": str(uuid4())}
            )
        self.assertEqual(db.add.call_args.args[0].outcome, "disconnected")


class AggregateAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.conversation = str(uuid4())
        self.claims = {"type": "support_agent", "conversation_id": self.conversation}
        self.db = AsyncMock(spec=AsyncSession)
        self.app = FastAPI()
        self.app.include_router(router)

        async def db_override():
            yield self.db

        self.app.dependency_overrides[get_async_chat_db] = db_override

        @self.app.middleware("http")
        async def claims(request, call_next):
            request.state.claims = self.claims
            request.state.organization_id = "org-test"
            return await call_next(request)

    def test_user_and_wrong_conversation_cannot_read_timings(self):
        with TestClient(self.app) as client:
            self.claims["type"] = "user"
            self.assertEqual(
                client.get(f"/conversations/{self.conversation}/latency").status_code,
                403,
            )
            self.claims["type"] = "support_agent"
            self.claims["conversation_id"] = str(uuid4())
            self.assertEqual(
                client.get(f"/conversations/{self.conversation}/latency").status_code,
                403,
            )
        self.db.execute.assert_not_awaited()

    def test_staff_read_is_tenant_scoped_and_empty_is_not_zero_latency(self):
        dashboard = AsyncMock(spec=AsyncSession)
        dashboard.scalar.return_value = uuid4()
        from unittest.mock import MagicMock

        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        self.db.execute.return_value = result
        with patch("app.db.session.create_async_dashboard_db_session") as factory:
            factory.return_value.__aenter__ = AsyncMock(return_value=dashboard)
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with TestClient(self.app) as client:
                response = client.get(f"/conversations/{self.conversation}/latency")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["latency"]["average_response_ms"])
        self.assertEqual(self.db.execute.call_args.args[1]["org"], "org-test")
        query = str(dashboard.scalar.call_args.args[0])
        self.assertIn("organization_id", query)

    def test_different_organization_conversation_is_not_found(self):
        dashboard = AsyncMock(spec=AsyncSession)
        dashboard.scalar.return_value = None
        with patch("app.db.session.create_async_dashboard_db_session") as factory:
            factory.return_value.__aenter__ = AsyncMock(return_value=dashboard)
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with TestClient(self.app) as client:
                self.assertEqual(
                    client.get(
                        f"/conversations/{self.conversation}/latency"
                    ).status_code,
                    404,
                )
        self.db.execute.assert_not_awaited()
