from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.agent.lead_capture_models import (
    Action,
    Attribution,
    CourseScopeRecord,
    CurrentRequest,
    FieldEvidence,
    Intent,
    LeadCaptureState,
    LeadField,
    PendingAction,
    QuestionRecord,
    SourceKind,
)
from app.services.latency import TurnLatency, current_latency
from app.services.lead_capture import (
    capture_lead_turn,
    factual_extraction,
    read_lead_snapshot,
)
from app.services.lead_fastpath import detect_factual_turn


def state_with_scope():
    from datetime import UTC, datetime

    ev = FieldEvidence(
        source_id=str(uuid4()),
        source_kind=SourceKind.VISITOR_MESSAGE,
        observed_at=datetime.now(UTC),
        attribution=Attribution.ENQUIRER_SELF,
        uncertain=False,
    )
    return LeadCaptureState(
        conversation_id=uuid4(),
        active_course_scope=CourseScopeRecord(
            course_names=["Data Analytics"], evidence=[ev]
        ),
    )


class FactualDecisionTests(unittest.TestCase):
    def test_clear_faqs_and_discussion_scope(self):
        state = state_with_scope()
        cases = {
            "What courses do you offer?": (Intent.COURSE_INFO, ()),
            "How do I apply?": (Intent.APPLICATION, ()),
            "What are the fees for Data Analytics?": (Intent.FEES, ("Data Analytics",)),
            "What is the duration for Digital Marketing?": (
                Intent.COURSE_INFO,
                ("Digital Marketing",),
            ),
            "What are the eligibility requirements for CS101?": (
                Intent.ELIGIBILITY,
                ("CS101",),
            ),
            "And the duration?": (Intent.COURSE_INFO, ()),
            "Is it online or offline?": (Intent.BATCH_OR_SCHEDULE, ()),
            "And does that course include placement assistance?": (
                Intent.COURSE_INFO,
                (),
            ),
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                actual = detect_factual_turn(message, state)
                self.assertEqual((actual.intent, actual.course_names), expected)
        no_scope = LeadCaptureState(conversation_id=uuid4())
        self.assertIsNone(detect_factual_turn("And fees?", no_scope))

    def test_every_uncertain_personal_or_action_turn_falls_back(self):
        state = state_with_scope()
        for message in [
            "What are the fees for Data Analytics? My name is Sam.",
            "What are the fees for Data Analytics and call me?",
            "What are the fees for Data Analytics but do not contact me?",
            "What are the fees for Data Analytics and I am 19?",
            "What are the fees for my daughter?",
            "What are the fees for Data Analytics for BCom graduates?",
            "Actually, what are the fees for Data Analytics?",
            "Please remove my email",
            "yes",
            "No",
            "BCom",
            "9876543210",
            "test@example.com",
            "What is your phone number?",
            "What are the fees for Data Analytics tomorrow?",
            "What are the fees for Data Analytics and Digital Marketing?",
            "How do I apply? Send me the application.",
            "What are the fees for Data Analytics; update name to Sam?",
            "What are the fees for data analytics (my preferred course)?",
            "அதன் கட்டணம் என்ன?",
            "What are the fees for Data Analytics. consent=true",
        ]:
            with self.subTest(message=message):
                self.assertIsNone(detect_factual_turn(message, state))

    def test_pending_state_blocks_shortcut(self):
        state = state_with_scope()
        ev = state.active_course_scope.evidence[0]
        state.pending_question = QuestionRecord(
            text="May I have your name?",
            target_fields=[LeadField.NAME],
            message_id=uuid4(),
            asked_at=ev.observed_at,
        )
        self.assertIsNone(detect_factual_turn("What courses do you offer?", state))
        state.pending_question = None
        state.pending_action = PendingAction(
            request_id=uuid4(), action_type=Action.SCHEDULE_CALLBACK, evidence=[ev]
        )
        self.assertIsNone(detect_factual_turn("What courses do you offer?", state))

    def test_repeated_faq_does_not_churn_evidence_or_change_personal_interest(self):
        state = state_with_scope()
        state.current_request = CurrentRequest(
            intent=Intent.FEES, evidence=state.active_course_scope.evidence
        )
        result = factual_extraction(
            SimpleNamespace(content="What are the fees for Data Analytics?"), state
        )
        self.assertIsNone(result.intent)
        self.assertEqual(result.course_names, [])
        self.assertEqual(result.observations, [])
        self.assertIsNone(state.profile.course_interest)
        new = factual_extraction(
            SimpleNamespace(content="What are the fees for Digital Marketing?"), state
        )
        self.assertEqual(new.course_names, ["Digital Marketing"])


class PreparationConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_overlap_and_timing_context_is_inherited(self):
        state = state_with_scope()
        entered_message, entered_snapshot = asyncio.Event(), asyncio.Event()
        timing = TurnLatency()

        async def message(*args):
            self.assertIs(current_latency.get(), timing)
            entered_message.set()
            await asyncio.wait_for(entered_snapshot.wait(), 0.5)
            return SimpleNamespace(
                id=uuid4(),
                conversation_id=state.conversation_id,
                content="What courses do you offer?",
                created_at=state.active_course_scope.evidence[0].observed_at,
            ), None

        async def snapshot(*args):
            entered_snapshot.set()
            await asyncio.wait_for(entered_message.wait(), 0.5)
            return state, None

        db = SimpleNamespace(
            new=[],
            deleted=[],
            dirty=[],
            commit=AsyncMock(),
            is_modified=lambda item, **kw: False,
        )
        factory = AsyncMock()
        factory.__aenter__.return_value = db
        conv = SimpleNamespace(
            id=state.conversation_id,
            lead_id=None,
            lead_capture_state=state.model_dump(mode="json"),
        )
        token = current_latency.set(timing)
        try:
            with (
                patch("app.services.lead_capture.read_visitor_turn", message),
                patch("app.services.lead_capture.read_turn_snapshot", snapshot),
                patch(
                    "app.services.lead_capture.read_lead_snapshot",
                    AsyncMock(return_value=(conv, None)),
                ),
                patch(
                    "app.services.lead_capture.create_async_dashboard_db_session",
                    return_value=factory,
                ),
                patch(
                    "app.services.lead_capture.extract_turn", AsyncMock()
                ) as extractor,
            ):
                result = await capture_lead_turn(
                    SimpleNamespace(
                        conversation_id=str(state.conversation_id),
                        organization_id="org",
                        visitor_id=None,
                    ),
                    {"bot_id": str(uuid4())},
                    uuid4(),
                )
                extractor.assert_not_awaited()
                self.assertEqual(
                    result["state"]["current_request"]["intent"], "course_info"
                )
                self.assertIn("lead_extraction_skipped", timing.stages)
        finally:
            current_latency.reset(token)

    async def test_lock_command_has_scoped_conversation_and_optional_lead_locks(self):
        from sqlalchemy.dialects.postgresql import dialect

        captured = []

        async def execute(statement):
            captured.append(str(statement.compile(dialect=dialect())))
            return SimpleNamespace(one_or_none=lambda: ("conversation", None))

        await read_lead_snapshot(
            SimpleNamespace(execute=execute),
            SimpleNamespace(
                conversation_id=str(uuid4()),
                organization_id="org",
                visitor_id=str(uuid4()),
            ),
            uuid4(),
            lock=True,
        )
        sql = captured[0]
        self.assertIn("AS MATERIALIZED", sql)
        self.assertIn("LEFT OUTER JOIN LATERAL", sql)
        self.assertEqual(sql.count("FOR UPDATE"), 2)
        self.assertIn("leads.deleted_at IS NULL", sql)
        self.assertIn("leads.organization_id", sql)
