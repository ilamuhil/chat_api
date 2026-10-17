from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import WebSocket
from langchain_core.messages import AIMessage

from app.domain import ChatSession
from app.rag import RetrievalPipeline
from app.rag.shared_dataclasses import (
    PreparedQuery,
    RetrievalResult,
    RetrievalResultStatus,
)
from app.services.billing import current_meter, current_reservation
from app.services.chat import respond_with_ai
from app.services.latency import TurnLatency, current_latency


class ChatPreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(
            patch("app.services.billing.reserve_reply", AsyncMock(return_value=None))
        )
        self.enterContext(patch("app.services.billing.finish_reply", AsyncMock()))
        self.enterContext(
            patch("app.services.chat.log_message", AsyncMock(return_value=uuid4()))
        )
        self.log_retrieval = self.enterContext(
            patch("app.services.chat.log_retrieval", AsyncMock())
        )
        self.factory = self.enterContext(
            patch("app.services.chat.create_async_chat_db_session")
        )
        self.factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        self.factory.return_value.__aexit__ = AsyncMock(return_value=False)
        self.retrieval = RetrievalResult(
            status=RetrievalResultStatus.NO_EVIDENCE,
            prepared_query=PreparedQuery(
                original_message="And fees?",
                standalone_query="CS101 fees?",
                did_rewrite=True,
            ),
            context_bundle=None,
            diagnostics={},
        )
        self.lead = {
            "status": "ready",
            "state": {},
            "action_outcomes": ["Details saved."],
        }
        self.retrieve = self.enterContext(
            patch.object(
                RetrievalPipeline, "retrieve", AsyncMock(return_value=self.retrieval)
            )
        )
        self.capture = self.enterContext(
            patch(
                "app.services.chat.capture_lead_turn", AsyncMock(return_value=self.lead)
            )
        )
        self.agent = SimpleNamespace(
            aget_state=AsyncMock(return_value=SimpleNamespace(values={"messages": []})),
            ainvoke=AsyncMock(
                return_value={"messages": [AIMessage(content="I can help with that.")]}
            ),
        )
        self.session = ChatSession(
            organization_id="org-test",
            conversation_id=str(uuid4()),
            user_socket=AsyncMock(spec=WebSocket),
        )
        self.prefs = {
            "bot_id": str(uuid4()),
            "embedding_configuration_id": str(uuid4()),
            "bot_configuration_id": str(uuid4()),
            "similarity_threshold": 0.7,
            "retrieval_k": 5,
        }
        self.message = {"message": "And fees?", "message_id": str(uuid4())}

    async def respond(self):
        await respond_with_ai(self.message, self.prefs, self.session, self.agent)

    async def test_operations_overlap_and_agent_waits_for_both(self):
        started = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        contexts = []
        timing = TurnLatency()

        async def retrieval(request):
            contexts.append(
                (current_meter.get(), current_reservation.get(), current_latency.get())
            )
            started[0].set()
            await release[0].wait()
            return self.retrieval

        async def capture(*args):
            contexts.append(
                (current_meter.get(), current_reservation.get(), current_latency.get())
            )
            started[1].set()
            await release[1].wait()
            return self.lead

        self.retrieve.side_effect = retrieval
        self.capture.side_effect = capture
        token = current_latency.set(timing)
        try:
            async with asyncio.timeout(1):
                async with asyncio.TaskGroup() as tasks:
                    task = tasks.create_task(self.respond())
                    await started[0].wait()
                    await started[1].wait()
                    self.agent.ainvoke.assert_not_awaited()
                    # Retrieval finishes while lead capture is still pending.
                    release[0].set()
                    await asyncio.sleep(0)
                    self.agent.ainvoke.assert_not_awaited()
                    release[1].set()
                self.assertIsNone(task.result())
        finally:
            current_latency.reset(token)
        context = self.agent.ainvoke.call_args.kwargs["context"]
        self.assertIs(context.retrieval_result, self.retrieval)
        self.assertEqual(context.lead_capture, self.lead)
        self.assertEqual(len(contexts), 2)
        self.assertIsNotNone(contexts[0][0])
        self.assertIs(contexts[0][0], contexts[1][0])
        self.assertEqual(contexts[0][1], contexts[1][1])
        self.assertIsNotNone(contexts[0][1])
        self.assertIs(contexts[0][2], timing)
        self.assertIs(contexts[1][2], timing)
        self.assertIn("turn_preparation", timing.stages)
        self.factory.return_value.__aexit__.assert_awaited_once()

    async def test_lead_failure_keeps_retrieval_and_safe_action_context(self):
        self.capture.side_effect = RuntimeError("extraction offline")
        with self.assertLogs("app.services.chat", level="ERROR"):
            await self.respond()
        context = self.agent.ainvoke.call_args.kwargs["context"]
        self.assertIs(context.retrieval_result, self.retrieval)
        self.assertEqual(context.lead_capture["status"], "unavailable")
        self.assertIn(
            "Do not claim a new save", context.lead_capture["action_outcomes"][0]
        )

    async def test_retrieval_failure_keeps_confirmed_lead_outcomes(self):
        self.retrieve.side_effect = RuntimeError("search offline")
        with self.assertLogs("app.services.chat", level="ERROR"):
            await self.respond()
        context = self.agent.ainvoke.call_args.kwargs["context"]
        self.assertEqual(
            context.retrieval_result.status, RetrievalResultStatus.UNAVAILABLE
        )
        self.assertEqual(context.lead_capture, self.lead)
        self.log_retrieval.assert_awaited_once()

    async def test_both_failures_still_allow_safe_agent_response(self):
        self.retrieve.side_effect = RuntimeError("search offline")
        self.capture.side_effect = RuntimeError("extraction offline")
        with self.assertLogs("app.services.chat", level="ERROR"):
            await self.respond()
        context = self.agent.ainvoke.call_args.kwargs["context"]
        self.assertEqual(
            context.retrieval_result.status, RetrievalResultStatus.UNAVAILABLE
        )
        self.assertEqual(context.lead_capture["status"], "unavailable")
        self.agent.ainvoke.assert_awaited_once()

    async def test_missing_or_invalid_message_id_skips_extraction(self):
        for message_id in (None, "invalid-id"):
            self.message["message_id"] = message_id
            await self.respond()
        self.capture.assert_not_awaited()
        self.assertEqual(self.retrieve.await_count, 2)
        self.assertEqual(self.agent.ainvoke.await_count, 2)

    async def test_cancellation_awaits_cleanup_of_both_operations(self):
        started = [asyncio.Event(), asyncio.Event()]
        finished = [asyncio.Event(), asyncio.Event()]

        async def block(index):
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                finished[index].set()

        async def retrieve(*args):
            await block(0)

        async def capture(*args):
            await block(1)

        self.retrieve.side_effect = retrieve
        self.capture.side_effect = capture
        async with asyncio.timeout(1):
            task = asyncio.create_task(self.respond())
            try:
                await started[0].wait()
                await started[1].wait()
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(finished[0].is_set())
        self.assertTrue(finished[1].is_set())
        self.agent.ainvoke.assert_not_awaited()
        self.factory.return_value.__aexit__.assert_awaited_once()
        payloads = [
            call.args[0] for call in self.session.user_socket.send_json.call_args_list
        ]
        self.assertFalse(payloads[-1]["is_typing"])
