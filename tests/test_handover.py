from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from copy import copy
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.api.routes.ws_chat import process_ai_queue
from app.api.routes.ws_chat import router as socket_router
from app.domain import ChatSession
from app.services.billing import metered_reply
from app.services.chat import _send_json_safe, _stream_agent
from app.services.latency import TurnLatency
from app.ws.auth import authenticate_socket
from app.ws.handover import (
    AITurnScope,
    AITurnSuperseded,
    HandoverRejected,
    current_ai_scope,
    guard_ai_output,
    receive_relay,
    refresh_session,
    register_connection,
    relay_to,
    state_payload,
    unregister_connection,
)


class Database:
    """Transaction-isolated row fixture with blocking SELECT FOR UPDATE semantics."""

    def __init__(self, row):
        self.row = row
        self.lock = asyncio.Lock()
        self.queries = []
        self.fail_commit = False

    def session(self):
        database = self

        class Transaction:
            def __init__(self):
                self.locked = False
                self.row = None

            async def __aenter__(self):
                return self

            async def scalar(self, query):
                database.queries.append(query)
                if query._for_update_arg is not None:
                    await database.lock.acquire()
                    self.locked = True
                params = query.compile().params
                if params.get("organization_id_1") != database.row.organization_id:
                    return None
                self.row = copy(database.row)
                return self.row

            async def commit(self):
                if database.fail_commit:
                    raise RuntimeError("Synthetic DB failure")
                database.row = copy(self.row)
                if self.locked:
                    database.lock.release()
                    self.locked = False

            async def __aexit__(self, *args):
                if self.locked:
                    database.lock.release()

        return Transaction()


def socket():
    ws = AsyncMock(spec=WebSocket)
    ws.state = SimpleNamespace()
    ws.__len__.return_value = 1
    return ws


class HandoverTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.conversation_id, self.bot_id, self.agent_id = uuid4(), uuid4(), uuid4()
        self.user_connection_id = str(uuid4())
        self.agent_connection_id = str(uuid4())
        self.database = Database(
            SimpleNamespace(
                id=self.conversation_id,
                bot_id=self.bot_id,
                organization_id="org-a",
                mode="ai",
                status="open",
                handover_status="none",
                handover_version=0,
                visitor_connection_id=uuid4(),
                agent_connection_id=None,
                active_agent_id=None,
            )
        )
        self.database.row.visitor_connection_id = __import__("uuid").UUID(
            self.user_connection_id
        )
        self.session = ChatSession(
            conversation_id=str(self.conversation_id),
            organization_id="org-a",
            visitor_id=str(uuid4()),
            user_socket=socket(),
            user_connection_id=self.user_connection_id,
            active_user_connection_id=self.user_connection_id,
        )
        self.enterContext(
            patch(
                "app.ws.handover.create_async_dashboard_db_session",
                self.database.session,
            )
        )
        self.redis = self.enterContext(patch("app.ws.handover.async_redis_client"))
        self.redis.set = AsyncMock()
        self.redis.exists = AsyncMock(return_value=1)
        self.redis.delete = AsyncMock()
        self.publish = self.enterContext(
            patch(
                "app.ws.handover.publish_to_channel", AsyncMock(return_value=(True, 2))
            )
        )

    async def takeover(self, session=None, connection=None):
        await register_connection(
            session or self.session,
            role="support_agent",
            connection_id=connection or self.agent_connection_id,
            bot_id=self.bot_id,
            user_id=str(self.agent_id),
            takeover=True,
            expected_version=self.database.row.handover_version,
        )

    def ai_scope(self, version=0):
        token = current_ai_scope.set(
            AITurnScope(
                str(self.conversation_id),
                "org-a",
                version,
                self.user_connection_id,
            )
        )
        self.addCleanup(current_ai_scope.reset, token)

    async def test_takeover_is_committed_and_restored_by_new_process(self):
        await self.takeover()
        self.assertEqual(self.database.row.mode, "human")
        self.assertEqual(
            str(self.database.row.agent_connection_id), self.agent_connection_id
        )
        self.assertEqual(self.database.row.handover_version, 1)
        fresh = ChatSession(
            conversation_id=str(self.conversation_id), organization_id="org-a"
        )
        await register_connection(
            fresh, role="user", connection_id=str(uuid4()), bot_id=self.bot_id
        )
        self.assertEqual(fresh.mode, "human")
        self.assertEqual(fresh.agent_user_id, str(self.agent_id))

    async def test_old_takeover_grant_cannot_replace_a_new_owner(self):
        await self.takeover()
        with self.assertRaises(HandoverRejected):
            await register_connection(
                self.session,
                role="support_agent",
                connection_id=str(uuid4()),
                bot_id=self.bot_id,
                user_id=str(uuid4()),
                takeover=True,
                expected_version=0,
            )
        self.assertEqual(str(self.database.row.active_agent_id), str(self.agent_id))

    async def test_two_simultaneous_takeovers_with_same_version_have_one_winner(self):
        async def claim(user_id):
            await register_connection(
                self.session,
                role="support_agent",
                connection_id=str(uuid4()),
                bot_id=self.bot_id,
                user_id=user_id,
                takeover=True,
                expected_version=0,
            )

        results = await asyncio.gather(
            claim(str(uuid4())), claim(str(uuid4())), return_exceptions=True
        )
        self.assertEqual(sum(result is None for result in results), 1)
        self.assertEqual(
            sum(isinstance(result, HandoverRejected) for result in results), 1
        )

    async def test_failed_commit_never_reports_successful_takeover(self):
        self.database.fail_commit = True
        with self.assertRaises(RuntimeError):
            await self.takeover()
        self.assertEqual(self.database.row.mode, "ai")
        self.assertEqual(self.session.mode, "ai")
        self.publish.assert_not_awaited()

    async def test_disconnect_preserves_human_mode_and_stale_disconnect_cannot_detach_replacement(
        self,
    ):
        await self.takeover()
        replacement = str(uuid4())
        await self.takeover(connection=replacement)
        await unregister_connection(
            self.session, "support_agent", self.agent_connection_id
        )
        self.assertEqual(str(self.database.row.agent_connection_id), replacement)
        await unregister_connection(self.session, "support_agent", replacement)
        self.assertIsNone(self.database.row.agent_connection_id)
        self.assertEqual(self.database.row.mode, "human")

    async def test_read_only_reconnect_cannot_take_over_ai_or_another_counsellor(self):
        with self.assertRaises(HandoverRejected):
            await register_connection(
                self.session,
                role="support_agent",
                connection_id=self.agent_connection_id,
                bot_id=self.bot_id,
                user_id=str(self.agent_id),
                takeover=False,
            )
        await self.takeover()
        with self.assertRaises(HandoverRejected):
            await register_connection(
                self.session,
                role="support_agent",
                connection_id=str(uuid4()),
                bot_id=self.bot_id,
                user_id=str(uuid4()),
                takeover=False,
            )

    async def test_queued_and_cached_ai_output_is_rejected_after_takeover(self):
        self.ai_scope()
        await self.takeover()
        with self.assertRaises(AITurnSuperseded):
            await _send_json_safe(
                self.session.user_socket, {"role": "ai", "message": "stale"}
            )
        cached = {"role": "ai", "message": "cached"}
        model = AsyncMock()
        wrapped = metered_reply(model)
        with patch(
            "app.services.billing.reserve_reply", AsyncMock(return_value=cached)
        ):
            with self.assertRaises(AITurnSuperseded):
                await wrapped({"message": "question"}, {}, self.session, None)
        model.assert_not_awaited()
        self.session.user_socket.send_json.assert_not_awaited()

    async def test_version_fences_old_turn_even_if_mode_returns_to_ai(self):
        self.ai_scope()
        self.database.row.handover_version = 2
        with self.assertRaises(AITurnSuperseded):
            async with guard_ai_output():
                self.fail("Old turn was admitted")

    async def test_ai_send_and_takeover_are_serialized(self):
        self.ai_scope()
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def send(_):
            entered.set()
            await release.wait()
            order.append("ai_sent")

        self.session.user_socket.send_json.side_effect = send
        delivery = asyncio.create_task(
            _send_json_safe(self.session.user_socket, {"role": "ai"})
        )
        await entered.wait()

        async def claim():
            await self.takeover()
            order.append("human_committed")

        claim_task = asyncio.create_task(claim())
        await asyncio.sleep(0)
        self.assertFalse(claim_task.done())
        release.set()
        await asyncio.gather(delivery, claim_task)
        self.assertEqual(order, ["ai_sent", "human_committed"])

    async def test_streaming_stops_before_next_chunk_after_takeover(self):
        self.ai_scope()

        async def stream(*args, **kwargs):
            yield (
                "messages",
                (SimpleNamespace(content="first"), {"tags": ["final_answer"]}),
            )
            await self.takeover()
            yield (
                "messages",
                (SimpleNamespace(content="stale"), {"tags": ["final_answer"]}),
            )

        with self.assertRaises(AITurnSuperseded):
            await _stream_agent(
                SimpleNamespace(astream=stream), {}, {}, None, self.session, "stream-a"
            )
        self.assertEqual(self.session.user_socket.send_json.await_count, 1)

    async def test_visitor_and_counsellor_on_different_processes_receive_only_their_targeted_relays(
        self,
    ):
        await self.takeover()
        self.session.active_agent_connection_id = self.agent_connection_id
        self.assertTrue(
            await relay_to(self.session, "agent", {"role": "user", "message": "hello"})
        )
        event = self.publish.call_args.args[1]
        agent_process = ChatSession(
            conversation_id=str(self.conversation_id),
            organization_id="org-a",
            agent_socket=socket(),
            agent_connection_id=self.agent_connection_id,
        )
        await receive_relay(agent_process, event)
        agent_process.agent_socket.send_json.assert_awaited_once_with(event["payload"])
        self.assertTrue(
            await relay_to(
                agent_process,
                "user",
                {"role": "support_agent", "message": "reply"},
                sender_agent_connection_id=self.agent_connection_id,
            )
        )
        reverse = self.publish.call_args.args[1]
        await receive_relay(self.session, reverse)
        self.session.user_socket.send_json.assert_awaited_once_with(reverse["payload"])

    async def test_stale_or_wrong_tenant_relay_is_not_delivered(self):
        event = dict(
            organization_id="org-b",
            recipient="user",
            connection_id=self.user_connection_id,
            payload={"message": "bad"},
        )
        await receive_relay(self.session, event)
        event["organization_id"] = "org-a"
        event["connection_id"] = str(uuid4())
        await receive_relay(self.session, event)
        self.session.user_socket.send_json.assert_not_awaited()

    async def test_state_refresh_closes_replaced_socket_and_cancels_old_ai(self):
        self.session.ai_turn_task = MagicMock()
        self.database.row.handover_version = 1
        self.database.row.visitor_connection_id = uuid4()
        await refresh_session(self.session)
        self.session.user_socket.close.assert_awaited_once_with(
            code=4001, reason="Connection replaced"
        )
        self.session.ai_turn_task.cancel.assert_called_once()

    async def test_expired_agent_lease_is_shown_as_reconnecting(self):
        await self.takeover()
        self.redis.exists.return_value = 0
        payload = await state_payload(self.session)
        self.assertEqual(payload["mode"], "human")
        self.assertFalse(payload["agent_connected"])

    async def test_agent_authentication_does_not_require_local_visitor_session(self):
        ws = socket()
        ws.receive_json.return_value = {
            "token": "signed",
            "conversation_id": str(self.conversation_id),
        }
        claims = {
            "type": "support_agent",
            "sub": str(self.agent_id),
            "organization_id": "org-a",
            "bot_id": str(self.bot_id),
            "conversation_id": str(self.conversation_id),
            "agent_takeover": True,
            "handover_version": 0,
        }
        registry = {}
        with patch("app.ws.auth.verify_token", return_value=claims):
            metadata = await authenticate_socket(ws, registry)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata[0].mode, "human")
        self.assertEqual(ws.state.chat_role, "support_agent")
        ws.close.assert_not_awaited()

    async def test_queue_discards_waiting_turns_when_running_turn_is_cancelled_by_handover(
        self,
    ):
        queue = asyncio.Queue()
        scope = AITurnScope(
            str(self.conversation_id), "org-a", 0, self.user_connection_id
        )
        for _ in range(3):
            queue.put_nowait(({"message": "question"}, TurnLatency(), scope))
        started = asyncio.Event()

        async def respond(*args):
            started.set()
            await asyncio.Event().wait()

        @asynccontextmanager
        async def track(*args):
            yield

        with (
            patch(
                "app.api.routes.ws_chat.respond_with_ai", AsyncMock(side_effect=respond)
            ) as ai,
            patch("app.api.routes.ws_chat.track_turn", track),
        ):
            worker = asyncio.create_task(
                process_ai_queue(queue, self.session, {}, None)
            )
            await started.wait()
            await self.takeover()
            await asyncio.wait_for(queue.join(), 1)
            self.assertEqual(ai.await_count, 1)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

    async def test_websocket_takeover_stops_ai_and_relays_human_messages(self):
        from app.ws.registry import ACTIVE_SESSIONS

        app = FastAPI()
        app.include_router(socket_router, prefix="/api")
        app.state.agent = object()
        started, cancelled = threading.Event(), threading.Event()

        async def delayed_ai(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        def claims(token, options):
            result = {
                "conversation_id": str(self.conversation_id),
                "organization_id": "org-a",
                "bot_id": str(self.bot_id),
            }
            if token == "visitor":
                result["type"] = "user"
            else:
                result.update(
                    type="support_agent",
                    sub=str(self.agent_id),
                    agent_takeover=True,
                    handover_version=self.database.row.handover_version,
                )
            return result

        history = MagicMock()
        history.scalars = AsyncMock(return_value=MagicMock())
        history.scalars.return_value.all.return_value = []
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=history)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.dict(ACTIVE_SESSIONS, {}, clear=True),
            patch("app.ws.auth.verify_token", side_effect=claims),
            patch(
                "app.ws.auth.get_conversation",
                AsyncMock(side_effect=lambda *a, **k: copy(self.database.row)),
            ),
            patch(
                "app.api.routes.ws_chat.get_conversation",
                AsyncMock(side_effect=lambda *a, **k: copy(self.database.row)),
            ),
            patch("app.api.routes.ws_chat.load_bot_prefs", AsyncMock(return_value={})),
            patch(
                "app.api.routes.ws_chat.has_lead", AsyncMock(return_value=(True, None))
            ),
            patch("app.api.routes.ws_chat.send_first_message_once", AsyncMock()),
            patch(
                "app.api.routes.ws_chat.log_message",
                AsyncMock(side_effect=lambda *a: uuid4()),
            ),
            patch(
                "app.api.routes.ws_chat.respond_with_ai",
                AsyncMock(side_effect=delayed_ai),
            ),
            patch("app.api.routes.ws_chat.create_async_chat_db_session", factory),
            patch("app.api.routes.ws_chat.persist_latency", AsyncMock()),
            patch("app.services.latency.persist_latency", AsyncMock()),
            TestClient(app) as client,
        ):
            auth = {
                "token": "visitor",
                "conversation_id": str(self.conversation_id),
                "visitor_id": self.session.visitor_id,
            }
            with client.websocket_connect("/api/chat/ws") as visitor:
                visitor.send_json(auth)
                self.assertEqual(visitor.receive_json()["type"], "conversation_ready")
                self.assertEqual(visitor.receive_json()["mode"], "ai")
                visitor.send_json({"type": "message", "message": "first question"})
                self.assertTrue(started.wait(2))
                with client.websocket_connect("/api/chat/ws") as agent:
                    agent.send_json(
                        {"token": "agent", "conversation_id": str(self.conversation_id)}
                    )
                    self.assertEqual(agent.receive_json()["mode"], "human")
                    self.assertEqual(
                        agent.receive_json()["type"], "conversation_history"
                    )
                    self.assertEqual(agent.receive_json()["type"], "agent_ready")
                    self.assertTrue(cancelled.wait(2))
                    visitor.send_json(
                        {"type": "message", "message": "hello counsellor"}
                    )
                    incoming = agent.receive_json()
                    while incoming.get("type") != "message":
                        incoming = agent.receive_json()
                    self.assertEqual(incoming["message"], "hello counsellor")
                    agent.send_json({"type": "message", "message": "human reply"})
                    incoming = visitor.receive_json()
                    while incoming.get("type") != "message":
                        incoming = visitor.receive_json()
                    self.assertEqual(incoming["message"], "human reply")
            with client.websocket_connect("/api/chat/ws") as reconnected:
                reconnected.send_json(auth)
                self.assertEqual(
                    reconnected.receive_json()["type"], "conversation_ready"
                )
                self.assertEqual(reconnected.receive_json()["mode"], "human")

    async def test_broadcast_is_delivered_only_by_local_visitor_owner(self):
        from app.ws.registry import ACTIVE_SESSIONS
        from app.ws.runtime_events import dispatch_runtime_event

        self.session.user_socket = None
        with (
            patch.dict(
                ACTIVE_SESSIONS,
                {self.session.conversation_id: self.session},
                clear=True,
            ),
            patch("app.ws.runtime_events.send_to_end_user", AsyncMock()) as send,
        ):
            await dispatch_runtime_event(
                {
                    "type": "assistance",
                    "status": "searching",
                    "conversation_id": self.session.conversation_id,
                }
            )
            send.assert_not_awaited()

    async def test_delayed_timeout_cannot_override_completed_handover(self):
        from app.ws.registry import ACTIVE_SESSIONS
        from app.ws.runtime_events import dispatch_runtime_event

        await self.takeover()
        with (
            patch.dict(
                ACTIVE_SESSIONS,
                {self.session.conversation_id: self.session},
                clear=True,
            ),
            patch("app.ws.runtime_events.send_to_end_user", AsyncMock()) as send,
        ):
            await dispatch_runtime_event(
                {
                    "type": "handover_timeout",
                    "conversation_id": self.session.conversation_id,
                }
            )
            send.assert_not_awaited()
