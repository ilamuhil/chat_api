from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from rq.exceptions import DuplicateJobError, NoSuchJobError
from starlette.websockets import WebSocketState

from app.api.routes.conversation_lifecycle import router
from app.domain.chat import ChatSession
from app.services.conversation_lifecycle import (
    _enqueue_summary,
    close_conversation,
    enqueue_conversation_summary,
)
from app.services.cron_jobs.conversations import close_stale_conversations
from app.ws.handlers import request_end_chat


def socket():
    ws = AsyncMock(spec=WebSocket)
    ws.application_state = WebSocketState.CONNECTED
    ws.client_state = WebSocketState.CONNECTED
    return ws


def session():
    return ChatSession(
        conversation_id=str(uuid4()),
        organization_id="test-org",
        visitor_id=str(uuid4()),
        user_socket=socket(),
    )


class EndChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_visitor_cannot_close_human_chat_and_only_connected_agent_is_notified(
        self,
    ):
        state = session()
        state.agent_connect(socket(), str(uuid4()))
        with (
            patch(
                "app.ws.handlers.create_notifications",
                AsyncMock(return_value=[{"id": "synthetic"}]),
            ) as create,
            patch("app.ws.handlers.publish_notifications", AsyncMock()) as publish,
            patch("app.ws.handlers.end_chat_session", AsyncMock()) as end,
        ):
            self.assertFalse(await request_end_chat(state, state.user_socket))
            self.assertFalse(await request_end_chat(state, state.user_socket))
        end.assert_not_awaited()
        create.assert_awaited_once()
        self.assertEqual(
            create.call_args.kwargs["recipient_user_ids"],
            [__import__("uuid").UUID(state.agent_user_id)],
        )
        publish.assert_awaited_once()
        self.assertEqual(
            state.agent_socket.send_json.call_args.args[0]["type"], "end_chat_request"
        )
        self.assertEqual(
            state.user_socket.send_json.call_args.args[0]["message"],
            "agent has been notified to end chat",
        )
        state.user_socket.close.assert_not_awaited()

    async def test_agent_can_close_and_is_recorded_as_agent(self):
        state = session()
        state.agent_connect(socket(), str(uuid4()))
        with patch("app.ws.handlers.end_chat_session", AsyncMock()) as end:
            self.assertTrue(await request_end_chat(state, state.agent_socket))
        end.assert_awaited_once_with(state, closed_by="support_agent")

    async def test_visitor_can_close_ai_chat(self):
        state = session()
        with patch("app.ws.handlers.end_chat_session", AsyncMock()) as end:
            self.assertTrue(await request_end_chat(state, state.user_socket))
        end.assert_awaited_once_with(state, closed_by="visitor")

    async def test_failed_notification_does_not_claim_agent_notified(self):
        state = session()
        state.agent_connect(socket(), str(uuid4()))
        with (
            patch("app.ws.handlers.create_notifications", AsyncMock(return_value=[])),
            patch("app.ws.handlers.end_chat_session", AsyncMock()) as end,
        ):
            self.assertFalse(await request_end_chat(state, state.user_socket))
        end.assert_not_awaited()
        self.assertFalse(state.end_chat_requested)
        self.assertEqual(state.user_socket.send_json.call_args.args[0]["type"], "error")

    async def test_disconnected_agent_does_not_prevent_visitor_closure(self):
        state = session()
        state.agent_connect(socket(), str(uuid4()))
        state.agent_socket.client_state = WebSocketState.DISCONNECTED
        with patch("app.ws.handlers.end_chat_session", AsyncMock()) as end:
            self.assertTrue(await request_end_chat(state, state.user_socket))
        end.assert_awaited_once_with(state, closed_by="visitor")


class SummaryQueueTests(unittest.TestCase):
    def test_new_summary_job_is_unique_and_retries(self):
        with (
            patch(
                "app.services.conversation_lifecycle.Job.fetch",
                side_effect=NoSuchJobError,
            ),
            patch("app.services.conversation_lifecycle.Queue") as queue,
        ):
            conversation_id = uuid4()
            _enqueue_summary(conversation_id)
        kwargs = queue.return_value.enqueue.call_args.kwargs
        self.assertEqual(kwargs["job_id"], f"conversation-summary-{conversation_id}")
        self.assertTrue(kwargs["unique"])
        self.assertEqual(kwargs["result_ttl"], 0)
        self.assertEqual(kwargs["retry"].max, 3)

    def test_running_summary_is_not_duplicated(self):
        for status in ["queued", "started", "scheduled", "deferred"]:
            with (
                self.subTest(status=status),
                patch(
                    "app.services.conversation_lifecycle.Job.fetch",
                    return_value=SimpleNamespace(get_status=lambda current=status: current),
                ),
                patch("app.services.conversation_lifecycle.Queue") as queue,
            ):
                _enqueue_summary(uuid4())
                queue.assert_not_called()

    def test_failed_job_is_requeued(self):
        job = MagicMock()
        job.get_status.return_value = "failed"
        with patch("app.services.conversation_lifecycle.Job.fetch", return_value=job):
            _enqueue_summary(uuid4())
        job.requeue.assert_called_once()

    def test_concurrent_enqueue_race_is_safe(self):
        with (
            patch(
                "app.services.conversation_lifecycle.Job.fetch",
                side_effect=NoSuchJobError,
            ),
            patch("app.services.conversation_lifecycle.Queue") as queue,
        ):
            queue.return_value.enqueue.side_effect = DuplicateJobError
            _enqueue_summary(uuid4())


class ClosePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_commit_precedes_immediate_summary_enqueue(self):
        conversation = SimpleNamespace(
            status="open", summary_status="completed", closed_by=None
        )
        db = AsyncMock()
        db.scalar.return_value = conversation
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        async def enqueue(_):
            db.commit.assert_awaited_once()
            return True

        conversation_id = uuid4()
        with (
            patch(
                "app.services.conversation_lifecycle.create_async_dashboard_db_session",
                factory,
            ),
            patch(
                "app.services.conversation_lifecycle.enqueue_conversation_summary",
                AsyncMock(side_effect=enqueue),
            ) as queued,
        ):
            self.assertTrue(
                await close_conversation(
                    conversation_id,
                    closed_by="support_agent",
                    organization_id="test-org",
                )
            )
        self.assertEqual(conversation.status, "closed")
        self.assertEqual(conversation.summary_status, "pending")
        self.assertEqual(conversation.closed_by, "support_agent")
        queued.assert_awaited_once_with(conversation_id)
        self.assertIn("organization_id", str(db.scalar.call_args.args[0]))

    async def test_queue_failure_preserves_recovery_signal(self):
        with patch(
            "app.services.conversation_lifecycle._enqueue_summary",
            side_effect=RuntimeError("synthetic unavailable"),
        ):
            self.assertFalse(await enqueue_conversation_summary(uuid4()))

    async def test_timeout_closures_enqueue_summaries_and_notify_sessions(self):
        ids = [uuid4(), uuid4()]
        db = AsyncMock()
        db.scalars.return_value = SimpleNamespace(all=lambda: ids)
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "app.services.cron_jobs.conversations.create_async_dashboard_db_session",
                factory,
            ),
            patch(
                "app.services.cron_jobs.conversations.enqueue_conversation_summary",
                AsyncMock(),
            ) as queue,
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
        self.assertEqual(queue.await_count, 2)
        self.assertEqual(publish.await_count, 2)
        recover.assert_awaited_once()
        statement = db.scalars.call_args.args[0]
        self.assertEqual(statement.compile().params["summary_status"], "pending")


class CloseEndpointTests(unittest.TestCase):
    def test_endpoint_checks_role_conversation_and_organization(self):
        conversation_id = uuid4()
        app = FastAPI()
        app.include_router(router)
        claims = {"type": "user", "conversation_id": str(conversation_id)}

        @app.middleware("http")
        async def auth(request, call_next):
            request.state.claims = claims
            request.state.organization_id = "test-org"
            return await call_next(request)

        with (
            TestClient(app) as client,
            patch(
                "app.api.routes.conversation_lifecycle.close_conversation",
                AsyncMock(return_value=True),
            ) as close,
            patch(
                "app.api.routes.conversation_lifecycle.publish_conversation_closed",
                AsyncMock(),
            ),
        ):
            self.assertEqual(
                client.post(f"/conversations/{conversation_id}/close").status_code, 403
            )
            claims["type"] = "support_agent"
            claims["conversation_id"] = str(uuid4())
            self.assertEqual(
                client.post(f"/conversations/{conversation_id}/close").status_code, 403
            )
            claims["conversation_id"] = str(conversation_id)
            self.assertEqual(
                client.post(f"/conversations/{conversation_id}/close").status_code, 200
            )
            close.assert_awaited_once_with(
                conversation_id, closed_by="support_agent", organization_id="test-org"
            )
