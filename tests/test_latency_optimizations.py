import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, AIMessageChunk

from app.infra.local_cache import TTLCache
from app.rag.rewrite_decision import detect_rewrite_need
from app.rag.shared_dataclasses import ConversationTurn
from app.services.chat import _stream_agent, _stream_text
from app.services.latency import (
    TurnLatency,
    current_latency,
    mark_response_sent,
    track_turn,
)
from app.services.lead_context import compact_lead_state, plan_lead_question


class DecisionTests(unittest.TestCase):
    def test_ordered_signals(self):
        history = [
            ConversationTurn(role="user", content="Tell me about Data Analytics")
        ]
        cases = {
            "What courses do you offer?": False,
            "What are the fees for Data Analytics?": False,
            "Tell me about digital marketing": False,
            "What is the duration for cloud computing?": False,
            "What are CS101 fees?": False,
            "And fees?": True,
            "How long is it?": True,
            "What about that course?": True,
            "Compare these options": True,
            "Actually, the other one": True,
            "Can I attend online?": True,
            "What is the fee?": True,
            "thanks": False,
            "My email is test@example.com": False,
            "My email is test@example.com and what are the fees?": True,
            "அதன் கட்டணம் என்ன?": True,
            "What’s its duration?": True,
            "How do I apply?": False,
            "Where is your campus?": False,
            "Tell me more": True,
            "Is there an installment plan?": True,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    detect_rewrite_need(text, history).needs_rewrite, expected
                )
        self.assertFalse(detect_rewrite_need("And fees?", []).needs_rewrite)

    def test_cache_ttl_lru_isolation_and_copy(self):
        cache = TTLCache(2, 10)
        with patch("app.infra.local_cache.monotonic", return_value=0):
            cache.put(("org1", "bot", "v1"), [1])
            result = cache.get(("org1", "bot", "v1"))
            result.append(2)
            self.assertEqual(cache.get(("org1", "bot", "v1")), [1])
            self.assertIsNone(cache.get(("org2", "bot", "v1")))
            self.assertIsNone(cache.get(("org1", "bot", "v2")))
            cache.put(("a",), 2)
            cache.get(("org1", "bot", "v1"))
            cache.put(("b",), 3)
            self.assertIsNone(cache.get(("a",)))
        with patch("app.infra.local_cache.monotonic", return_value=11):
            self.assertIsNone(cache.get(("org1", "bot", "v1")))

    def test_compact_state_preserves_consent_and_conflicts(self):
        state = {
            "profile": {"consent_to_contact": False, "name": "Test", "email": None},
            "field_evidence": {"large": "history"},
            "unresolved_conflicts": [
                {
                    "field": "name",
                    "accepted_value": "Test",
                    "proposed_value": "New",
                    "reason": "unclear",
                    "proposed_evidence": ["long"],
                }
            ],
        }
        compact = compact_lead_state(state)
        self.assertIs(compact["profile"]["consent_to_contact"], False)
        self.assertNotIn("field_evidence", compact)
        self.assertEqual(compact["unresolved_conflicts"][0]["accepted_value"], "Test")
        self.assertNotIn("proposed_evidence", compact["unresolved_conflicts"][0])

    def test_minimal_collection_respects_consent(self):
        prefs = {"capture_leads": True, "capture_name": True, "capture_email": True}
        state = {
            "profile": {"name": "Test", "consent_to_contact": False},
            "pending_action": {
                "action_type": "send_course_details",
                "status": "awaiting_details",
            },
        }
        self.assertIsNone(plan_lead_question(prefs, {"state": state}))
        state["profile"]["consent_to_contact"] = True
        self.assertEqual(plan_lead_question(prefs, {"state": state})[1], ["email"])
        state["pending_action"] = None
        self.assertIsNone(plan_lead_question(prefs, {"state": state}))


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_final_text_streams_and_final_state_retained(self):
        async def events(*args, **kwargs):
            yield (
                "messages",
                (
                    AIMessageChunk(content="tool preamble"),
                    {"tags": ["tool_capable_answer"]},
                ),
            )
            yield (
                "messages",
                (
                    AIMessageChunk(content=[{"type": "reasoning", "text": "private"}]),
                    {"tags": ["final_answer"]},
                ),
            )
            yield (
                "messages",
                (AIMessageChunk(content="Hello "), {"tags": ["final_answer"]}),
            )
            yield (
                "messages",
                (AIMessageChunk(content="world"), {"tags": ["final_answer"]}),
            )
            yield "updates", {"model": {"messages": [AIMessage(content="Hello world")]}}

        socket = SimpleNamespace(send_json=AsyncMock())
        session = SimpleNamespace(user_socket=socket, conversation_id="conversation")
        timing = TurnLatency()
        token = current_latency.set(timing)
        try:
            result = await _stream_agent(
                SimpleNamespace(astream=events), {}, {}, None, session, "stream"
            )
            self.assertEqual(
                [call.args[0]["delta"] for call in socket.send_json.await_args_list],
                ["Hello ", "world"],
            )
            self.assertIsNone(timing.response_ms)
            self.assertIn("time_to_first_text", timing.stages)
            mark_response_sent({"role": "ai", "type": "message"})
            self.assertIsNotNone(timing.response_ms)
            self.assertEqual(result["messages"][-1].content, "Hello world")
        finally:
            current_latency.reset(token)

    def test_text_filter(self):
        self.assertEqual(
            _stream_text(
                SimpleNamespace(
                    content=[
                        {"type": "tool_call", "text": "hidden"},
                        {"type": "text", "text": " visible "},
                    ]
                )
            ),
            " visible ",
        )


class DisconnectTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_cancellation_finishes_bounded_save(self):
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def save(*args):
            entered.set()
            await release.wait()
            finished.set()

        async def turn():
            async with track_turn(TurnLatency(), SimpleNamespace(), {}):
                pass

        with patch("app.services.latency.persist_latency", save):
            task = asyncio.create_task(turn())
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(finished.is_set())
