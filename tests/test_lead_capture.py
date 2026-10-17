from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.lead_capture_models import (
    Action,
    ActionSignal,
    ActionSignalType,
    ActionStatus,
    Attribution,
    FieldEvidence,
    LeadCaptureState,
    LeadField,
    LeadPatch,
    LeadProfile,
    LeadTurnUpdate,
    PendingAction,
    QuestionRecord,
    SourceKind,
)
from app.db.session import async_chat_engine, async_dashboard_db_engine
from app.domain.chat import ChatSession
from app.models.chat_db_models import Messages
from app.models.dashboard_db_models import Bots, ConversationsMeta, LeadFollowUps, Leads
from app.services.lead_capture import (
    LeadStateConflict,
    Observation,
    ProposedAction,
    TurnExtraction,
    action_requirements,
    capture_lead_form,
    capture_lead_turn,
    handle_actions,
    load_profile,
    manual_change_allows,
    merge_policy,
    parse_lead_form,
    record_lead_question,
    to_turn_update,
)
from app.services.lead_merge_service import LeadMergeService, MergePolicy, SourceRecord
from app.services.lead_summary import SummaryText, summarize_conversation


def event(conversation_id=None, **values):
    return Messages(
        id=uuid4(),
        conversation_id=conversation_id or uuid4(),
        role="user",
        content_type="text",
        content="Please call me",
        created_at=datetime.now(UTC),
        **values,
    )


def evidence(message):
    return FieldEvidence(
        source_id=str(message.id),
        source_kind=SourceKind.VISITOR_MESSAGE,
        observed_at=message.created_at,
        attribution=Attribution.ENQUIRER_SELF,
        uncertain=False,
    )


class LeadCaptureTests(unittest.IsolatedAsyncioTestCase):
    def test_human_field_labels_and_bad_field_do_not_discard_valid_facts(self):
        message = event()
        update = to_turn_update(
            TurnExtraction(
                observations=[
                    Observation(
                        field="preferred_mode",
                        value="offline classroom study",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="education_level",
                        value="BCom",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="student_age", value="25", attribution="enquirer_self"
                    ),
                    Observation(
                        field="joining_timeline",
                        value="not-a-valid-timeline",
                        attribution="enquirer_self",
                    ),
                    Observation(field="email", value=None, attribution="enquirer_self"),
                    Observation(field="phone", value="", attribution="enquirer_self"),
                ]
            ),
            message,
            LeadCaptureState(conversation_id=message.conversation_id),
        )
        self.assertEqual(len(update.patches), 3)
        self.assertEqual(update.patches[0].set_values.preferred_mode, "offline")
        self.assertEqual(update.patches[1].set_values.education_level, "BCom")
        self.assertEqual(update.patches[2].set_values.student_age, 25)

    def test_third_party_and_duplicate_observations_do_not_break_turn(self):
        message = event()
        update = to_turn_update(
            TurnExtraction(
                observations=[
                    Observation(
                        field="education_level", value="BTech", attribution="other"
                    ),
                    Observation(
                        field="education_level",
                        value="BCom",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="education_level",
                        value="BCom",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="student_age", value=24, attribution="enquirer_self"
                    ),
                    Observation(
                        field="student_age",
                        value=25,
                        correction=True,
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="preferred_mode",
                        value="online",
                        uncertain=True,
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="preferred_mode",
                        value="offline",
                        attribution="enquirer_self",
                    ),
                ]
            ),
            message,
            LeadCaptureState(conversation_id=message.conversation_id),
        )
        self.assertEqual(len(update.patches), 3)
        self.assertEqual(update.patches[0].set_values.education_level, "BCom")
        self.assertEqual(update.patches[1].set_values.student_age, 25)
        self.assertEqual(update.patches[2].set_values.preferred_mode, "offline")

    def test_superseded_values_are_not_removed_without_explicit_quote(self):
        message = event()
        message.content = "The old email and qualification are superseded. Remove my saved phone number."
        state = LeadCaptureState(
            conversation_id=message.conversation_id,
            profile=LeadProfile(
                email="current@example.invalid", education_level="MCom"
            ),
        )
        update = to_turn_update(
            TurnExtraction(
                observations=[
                    Observation(
                        field="email",
                        clear=True,
                        clear_evidence=" ",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="education_level",
                        clear=True,
                        clear_evidence="delete my education",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="phone",
                        clear=True,
                        clear_evidence="Remove my saved phone number",
                        attribution="enquirer_self",
                    ),
                ]
            ),
            message,
            state,
        )
        self.assertEqual(len(update.patches), 1)
        self.assertEqual(update.patches[0].clear_fields, [LeadField.PHONE])

    def test_form_requires_email_but_not_phone_or_consent(self):
        profile = parse_lead_form({"name": "Visitor", "email": "visitor@example.com"})
        self.assertIsNone(profile.phone)
        self.assertIsNone(profile.consent_to_contact)
        self.assertFalse(
            parse_lead_form(
                {
                    "name": "Visitor",
                    "email": "visitor@example.com",
                    "phone": "",
                    "consentToContact": False,
                }
            ).consent_to_contact
        )
        self.assertIsNone(
            parse_lead_form("Visitor:visitor@example.com:").consent_to_contact
        )
        for data in [
            {"name": "Visitor", "email": ""},
            {"name": "Visitor", "email": "bad"},
            {"name": "Visitor", "email": "visitor@example.com", "phone": "123"},
            {
                "name": "Visitor",
                "email": "visitor@example.com",
                "consentToContact": "true",
            },
        ]:
            with (
                self.subTest(data=data),
                self.assertRaises((ValidationError, ValueError)),
            ):
                parse_lead_form(data)

    def test_manual_edits_reject_older_visitor_evidence(self):
        message = event()
        metadata = {
            "manual_updated_at": {
                "phone": (message.created_at + timedelta(seconds=1)).isoformat()
            }
        }
        self.assertFalse(
            manual_change_allows(LeadField.PHONE, None, [evidence(message)], metadata)
        )
        self.assertTrue(
            manual_change_allows(LeadField.EMAIL, None, [evidence(message)], metadata)
        )
        metadata["manual_updated_at"]["phone"] = (
            message.created_at - timedelta(seconds=1)
        ).isoformat()
        self.assertTrue(
            manual_change_allows(LeadField.PHONE, None, [evidence(message)], metadata)
        )

    def test_bot_flags_control_collection(self):
        policy = merge_policy(
            {
                "capture_leads": True,
                "capture_name": True,
                "capture_email": True,
                "capture_phone": False,
            }
        )
        self.assertNotIn(LeadField.PHONE, policy.enabled_fields)
        self.assertIn(LeadField.EMAIL, policy.enabled_fields)
        self.assertNotIn(LeadField.STUDENT_GENDER, policy.enabled_fields)
        self.assertFalse(merge_policy({}).capture_leads)

    def test_legacy_values_load_without_mutation(self):
        lead = Leads(
            preferred_mode="both",
            joining_timeline="within_1_month",
            student_age="19",
            phone="not a number",
            enquirer_type="friend",
        )
        profile = load_profile(lead)
        self.assertEqual(profile.student_age, 19)
        self.assertEqual(profile.preferred_mode, "hybrid")
        self.assertEqual(profile.joining_timeline, "specific_period")
        self.assertEqual(profile.joining_preference_details, "within_1_month")
        self.assertIsNone(profile.phone)
        self.assertIsNone(profile.enquirer_type)
        self.assertEqual(lead.preferred_mode, "both")

    def test_extraction_uses_trusted_event_ids_and_timestamps(self):
        message = event()
        state = LeadCaptureState(conversation_id=message.conversation_id)
        update = to_turn_update(
            TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.PHONE,
                        value="+91 98765 43210",
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ],
                action=ProposedAction(
                    action_type=Action.SCHEDULE_CALLBACK,
                    signal_type=ActionSignalType.REQUESTED,
                ),
            ),
            message,
            state,
        )
        proposed = update.patches[0].evidence[LeadField.PHONE][0]
        self.assertEqual(proposed.source_id, str(message.id))
        self.assertEqual(proposed.observed_at, message.created_at)
        self.assertIsNone(update.action_signals[0].request_id)
        with self.assertRaises(ValidationError):
            TurnExtraction.model_validate({"request_id": str(uuid4())})

    def test_corrections_conflicts_clears_and_cross_conversation_evidence(self):
        message = event()
        state = LeadCaptureState(
            conversation_id=message.conversation_id, profile=LeadProfile(student_age=18)
        )
        extraction = TurnExtraction(
            observations=[
                Observation(
                    field=LeadField.STUDENT_AGE,
                    value=19,
                    attribution=Attribution.ENQUIRER_SELF,
                )
            ]
        )
        update = to_turn_update(extraction, message, state)
        source = SourceRecord(
            source_id=str(message.id),
            conversation_id=message.conversation_id,
            source_kind=SourceKind.VISITOR_MESSAGE,
            observed_at=message.created_at,
        )
        service = LeadMergeService()
        result = service.merge(state, update, sources={source.source_id: source})
        self.assertEqual(result.state.profile.student_age, 18)
        self.assertEqual(len(result.new_conflicts), 1)
        extraction.observations[0].correction = True
        update = to_turn_update(extraction, message, state)
        result = service.merge(state, update, sources={source.source_id: source})
        self.assertEqual(result.state.profile.student_age, 19)
        source.conversation_id = uuid4()
        result = service.merge(state, update, sources={source.source_id: source})
        self.assertEqual(result.state.profile.student_age, 18)
        self.assertTrue(result.rejected_changes)
        source.conversation_id = message.conversation_id
        update = LeadTurnUpdate(
            patches=[
                LeadPatch(
                    clear_fields=[LeadField.STUDENT_AGE],
                    evidence={LeadField.STUDENT_AGE: [evidence(message)]},
                )
            ]
        )
        result = LeadMergeService(MergePolicy(capture_leads=False)).merge(
            state, update, sources={source.source_id: source}
        )
        self.assertIsNone(result.state.profile.student_age)

    def test_question_resolution_and_declines(self):
        message = event()
        state = LeadCaptureState(conversation_id=message.conversation_id)
        service = LeadMergeService()
        state = service.record_question(
            state,
            QuestionRecord(
                text="Your phone?",
                target_fields=[LeadField.PHONE],
                message_id=uuid4(),
                asked_at=message.created_at - timedelta(seconds=1),
            ),
        )
        source = SourceRecord(
            source_id=str(message.id),
            conversation_id=message.conversation_id,
            source_kind=SourceKind.VISITOR_MESSAGE,
            observed_at=message.created_at,
        )
        update = to_turn_update(
            TurnExtraction(
                declined_fields=[LeadField.PHONE], question_resolution="declined"
            ),
            message,
            state,
        )
        result = service.merge(state, update, sources={source.source_id: source})
        self.assertIsNone(result.state.pending_question)
        self.assertIn(LeadField.PHONE, result.state.declined_fields)
        with self.assertRaises(ValueError):
            service.record_question(
                result.state,
                QuestionRecord(
                    text="Your phone?",
                    target_fields=[LeadField.PHONE],
                    message_id=uuid4(),
                    asked_at=message.created_at,
                ),
            )

    async def test_contact_actions_wait_for_consent_and_phone(self):
        message = event()
        state = LeadCaptureState(conversation_id=message.conversation_id)
        signal = ActionSignal(
            action_type=Action.SCHEDULE_CALLBACK,
            signal_type=ActionSignalType.REQUESTED,
            evidence=[evidence(message)],
        )
        db = AsyncMock()
        lead = Leads(id=uuid4())
        outcomes = await handle_actions(
            db, state, [signal], lead, message, MergePolicy()
        )
        self.assertEqual(state.pending_action.status, ActionStatus.AWAITING_DETAILS)
        self.assertIn("explicit contact consent", outcomes[-1])
        db.scalar.assert_not_awaited()
        self.assertEqual(
            action_requirements(
                Action.SEND_COURSE_DETAILS,
                LeadProfile(email="v@example.com", consent_to_contact=True),
            ),
            [],
        )

    async def test_removing_required_phone_cancels_saved_callback(self):
        message = event()
        state = LeadCaptureState(
            conversation_id=message.conversation_id,
            lead_id=uuid4(),
            profile=LeadProfile(consent_to_contact=True),
            pending_action=PendingAction(
                request_id=uuid4(),
                action_type=Action.SCHEDULE_CALLBACK,
                status=ActionStatus.REQUEST_SAVED,
                follow_up_id=uuid4(),
                evidence=[evidence(message)],
            ),
        )
        db = AsyncMock()
        db.scalar.return_value = SimpleNamespace(deleted_at=None, status="pending")
        with patch("app.services.lead_capture.save_follow_up", AsyncMock()) as saver:
            outcomes = await handle_actions(
                db, state, [], Leads(id=state.lead_id), message, MergePolicy()
            )
        self.assertEqual(state.pending_action.status, ActionStatus.CANCELLED)
        self.assertEqual(saver.call_args.kwargs["status"], "cancelled")
        self.assertIn("removed", outcomes[-1])

    async def test_followup_retry_reuses_request_id_and_refusal_cancels(self):
        message = event()
        state = LeadCaptureState(
            conversation_id=message.conversation_id,
            lead_id=uuid4(),
            profile=LeadProfile(phone="9876543210", consent_to_contact=True),
        )
        signal = ActionSignal(
            action_type=Action.SCHEDULE_CALLBACK,
            signal_type=ActionSignalType.REQUESTED,
            evidence=[evidence(message)],
        )

        async def save(db, lead, pending, **kwargs):
            pending.follow_up_id = uuid4()

        db = AsyncMock()
        db.scalar.return_value = SimpleNamespace(deleted_at=None, status="pending")
        with patch(
            "app.services.lead_capture.save_follow_up", AsyncMock(side_effect=save)
        ) as saver:
            await handle_actions(
                db, state, [signal], Leads(id=state.lead_id), message, MergePolicy()
            )
            request_id = state.pending_action.request_id
            await handle_actions(
                db, state, [signal], Leads(id=state.lead_id), message, MergePolicy()
            )
            self.assertEqual(state.pending_action.request_id, request_id)
            state.profile.consent_to_contact = False
            await handle_actions(
                db, state, [], Leads(id=state.lead_id), message, MergePolicy()
            )
            self.assertEqual(state.pending_action.status, ActionStatus.CANCELLED)
            self.assertEqual(saver.call_args.kwargs["status"], "cancelled")


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_LEAD_TESTS") == "1",
    "Opt-in PostgreSQL lead integration test",
)
class PostgresLeadCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dash_conn = await async_dashboard_db_engine.connect()
        self.dash_tx = await self.dash_conn.begin()
        self.chat_conn = await async_chat_engine.connect()
        self.chat_tx = await self.chat_conn.begin()

        def dash():
            return AsyncSession(
                bind=self.dash_conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

        def chat():
            return AsyncSession(
                bind=self.chat_conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

        self.dash, self.chat = dash, chat
        self.patches = []
        for module in ["app.services.lead_capture", "app.services.lead_summary"]:
            for suffix, factory in [("dashboard", dash), ("chat", chat)]:
                p = patch(f"{module}.create_async_{suffix}_db_session", factory)
                p.start()
                self.patches.append(p)
        async with dash() as db:
            bot = await db.scalar(
                select(Bots).where(Bots.organization_id.is_not(None)).limit(1)
            )
            if bot is None:
                self.skipTest("No configured bot")
            self.bot_id = bot.id
            self.org = bot.organization_id
            self.visitor_id = uuid4()
            self.conversation_id = uuid4()
            db.add(
                ConversationsMeta(
                    id=self.conversation_id,
                    bot_id=self.bot_id,
                    organization_id=self.org,
                )
            )
            await db.commit()
        self.session = ChatSession(
            conversation_id=str(self.conversation_id),
            organization_id=self.org,
            visitor_id=str(self.visitor_id),
        )
        self.prefs = {
            "bot_id": str(self.bot_id),
            "capture_leads": True,
            "capture_name": True,
            "capture_email": True,
            "capture_phone": True,
            "llm_model": "unused-mocked-model",
        }

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.chat_tx.rollback()
        await self.dash_tx.rollback()
        await self.chat_conn.close()
        await self.dash_conn.close()
        await async_chat_engine.dispose()
        await async_dashboard_db_engine.dispose()

    async def store(self, text):
        message = event(self.conversation_id)
        message.content = text
        async with self.chat() as db:
            db.add(message)
            await db.commit()
        return message

    async def run_turn(self, text, extraction):
        message = await self.store(text)
        with patch(
            "app.services.lead_capture.extract_turn", AsyncMock(return_value=extraction)
        ):
            result = await capture_lead_turn(self.session, self.prefs, message.id)
        return message, result

    async def test_form_merge_followup_retries_corrections_and_revocation(self):
        lead_id = await capture_lead_form(
            self.session,
            self.bot_id,
            self.visitor_id,
            parse_lead_form(
                {
                    "name": "Integration visitor",
                    "email": "integration@example.com",
                    "phone": "",
                    "consentToContact": False,
                }
            ),
        )
        async with self.dash() as db:
            lead = await db.get(Leads, lead_id)
            self.assertIsNone(lead.phone)
            self.assertFalse(lead.consent_to_contact)
            self.assertGreater(lead.profile_version, 0)
        extraction = TurnExtraction(
            observations=[
                Observation(
                    field=LeadField.PHONE,
                    value="9876543210",
                    attribution=Attribution.ENQUIRER_SELF,
                ),
                Observation(
                    field=LeadField.CONSENT_TO_CONTACT,
                    value=True,
                    attribution=Attribution.ENQUIRER_SELF,
                ),
            ],
            action=ProposedAction(
                action_type=Action.SCHEDULE_CALLBACK,
                signal_type=ActionSignalType.REQUESTED,
                requested_time_text="tomorrow afternoon",
            ),
        )
        message, result = await self.run_turn(
            "Please call me tomorrow afternoon at 9876543210.", extraction
        )
        self.assertEqual(result["state"]["pending_action"]["status"], "request_saved")
        with patch(
            "app.services.lead_capture.extract_turn", AsyncMock(return_value=extraction)
        ):
            retried = await capture_lead_turn(self.session, self.prefs, message.id)
        self.assertEqual(
            retried["state"]["pending_action"]["request_id"],
            result["state"]["pending_action"]["request_id"],
        )
        async with self.dash() as db:
            self.assertEqual(
                await db.scalar(
                    select(func.count())
                    .select_from(LeadFollowUps)
                    .where(LeadFollowUps.lead_id == lead_id)
                ),
                1,
            )
            follow = await db.scalar(
                select(LeadFollowUps).where(LeadFollowUps.lead_id == lead_id)
            )
            self.assertIsNone(follow.scheduled_for)
        await self.run_turn(
            "I am 18",
            TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.STUDENT_AGE,
                        value=18,
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ]
            ),
        )
        _, conflict = await self.run_turn(
            "I am 19",
            TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.STUDENT_AGE,
                        value=19,
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ]
            ),
        )
        self.assertEqual(conflict["state"]["profile"]["student_age"], 18)
        _, corrected = await self.run_turn(
            "Sorry I meant 19",
            TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.STUDENT_AGE,
                        value=19,
                        correction=True,
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ]
            ),
        )
        self.assertEqual(corrected["state"]["profile"]["student_age"], 19)
        original_conversation_id = self.conversation_id
        second_id = uuid4()
        async with self.dash() as db:
            db.add(
                ConversationsMeta(
                    id=second_id,
                    bot_id=self.bot_id,
                    organization_id=self.org,
                    lead_id=lead_id,
                )
            )
            await db.commit()
        self.conversation_id = second_id
        self.session.conversation_id = str(second_id)
        await self.run_turn(
            "Do not contact me",
            TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.CONSENT_TO_CONTACT,
                        value=False,
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ]
            ),
        )
        async with self.dash() as db:
            follow = await db.scalar(
                select(LeadFollowUps).where(LeadFollowUps.lead_id == lead_id)
            )
            self.assertEqual(follow.status, "cancelled")
        self.conversation_id = original_conversation_id
        self.session.conversation_id = str(original_conversation_id)
        _, revoked = await self.run_turn(
            "Do not contact me",
            TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.CONSENT_TO_CONTACT,
                        value=False,
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ]
            ),
        )
        self.assertEqual(revoked["state"]["pending_action"]["status"], "cancelled")
        async with self.dash() as db:
            follow = await db.scalar(
                select(LeadFollowUps).where(LeadFollowUps.lead_id == lead_id)
            )
            self.assertEqual(follow.status, "cancelled")
            lead = await db.get(Leads, lead_id)
            self.assertEqual(lead.student_age, "19")

    async def test_database_unique_index_scope_and_stale_writer(self):
        lead_id = await capture_lead_form(
            self.session,
            self.bot_id,
            self.visitor_id,
            parse_lead_form("Visitor:integration@example.com:"),
        )
        async with self.dash() as db:
            db.add(
                Leads(
                    id=uuid4(),
                    organization_id=self.org,
                    bot_id=self.bot_id,
                    visitor_id=self.visitor_id,
                )
            )
            with self.assertRaises(IntegrityError):
                await db.flush()
            await db.rollback()
        message = await self.store("I completed BCom")

        async def competing_update(*args):
            async with self.dash() as db:
                lead = await db.get(Leads, lead_id)
                lead.profile_version += 1
                await db.commit()
            return TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.EDUCATION_LEVEL,
                        value="BCom",
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ]
            )

        with patch(
            "app.services.lead_capture.extract_turn",
            AsyncMock(side_effect=competing_update),
        ):
            with self.assertRaises(LeadStateConflict):
                await capture_lead_turn(self.session, self.prefs, message.id)
        async with self.dash() as db:
            self.assertIsNone((await db.get(Leads, lead_id)).education_level)

    async def test_question_and_summary_persist_without_creating_followups(self):
        await capture_lead_form(
            self.session,
            self.bot_id,
            self.visitor_id,
            parse_lead_form("Visitor:integration@example.com:"),
        )
        message = await self.store("What is your education level?")
        message.role = "ai"
        async with self.chat() as db:
            row = await db.get(Messages, message.id)
            row.role = "ai"
            await db.commit()
        await record_lead_question(
            self.session,
            self.prefs,
            message.id,
            message.content,
            [LeadField.EDUCATION_LEVEL],
        )
        _, result = await self.run_turn(
            "BCom",
            TurnExtraction(
                observations=[
                    Observation(
                        field=LeadField.EDUCATION_LEVEL,
                        value="BCom",
                        attribution=Attribution.ENQUIRER_SELF,
                    )
                ],
                question_resolution="answered",
            ),
        )
        self.assertIsNone(result["state"]["pending_question"])
        async with self.dash() as db:
            row = await db.get(ConversationsMeta, self.conversation_id)
            row.status = "closed"
            await db.commit()
        model = SimpleNamespace(
            with_structured_output=lambda *args, **kwargs: SimpleNamespace(
                ainvoke=AsyncMock(
                    return_value=SummaryText(
                        conversation_summary="## Details shared\n\n- **Education:** BCom.",
                        lead_overview="## Current profile\n\n- **Education:** BCom.\n\n## Contact and next steps\n\n- No callback requested.",
                    )
                )
            )
        )
        with patch("app.services.lead_summary.ChatOpenAI", return_value=model), patch("app.services.lead_summary.reserve_auxiliary", new=AsyncMock(return_value=True)), patch("app.services.lead_summary.finish_auxiliary", new=AsyncMock()):
            await summarize_conversation(self.conversation_id)
        async with self.dash() as db:
            row = await db.get(ConversationsMeta, self.conversation_id)
            self.assertEqual(row.summary_status, "completed")
            self.assertEqual(
                row.ai_summary, "## Details shared\n\n- **Education:** BCom."
            )
            saved_lead = await db.get(Leads, row.lead_id)
            self.assertIn("## Current profile", saved_lead.ai_summary)
            self.assertIn("- **Education:** BCom.", saved_lead.ai_summary)
            self.assertIsNotNone(row.summary_through_message_id)
            self.assertEqual(
                await db.scalar(
                    select(func.count())
                    .select_from(LeadFollowUps)
                    .where(LeadFollowUps.lead_id == row.lead_id)
                ),
                0,
            )

    async def test_later_chat_corrections_and_delayed_summary_use_current_profile(self):
        lead_id = await capture_lead_form(
            self.session,
            self.bot_id,
            self.visitor_id,
            parse_lead_form(
                {"name": "Multi chat QA", "email": "multi@example.invalid"}
            ),
        )
        await self.run_turn(
            "I completed BCom and prefer offline Data Analytics.",
            TurnExtraction(
                observations=[
                    Observation(
                        field="education_level",
                        value="BCom",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="preferred_mode",
                        value="offline",
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="course_interest",
                        value="Data Analytics",
                        attribution="enquirer_self",
                    ),
                ]
            ),
        )
        older_id = self.conversation_id
        async with self.dash() as db:
            old = await db.get(ConversationsMeta, older_id)
            old.status = "closed"
            self.conversation_id = uuid4()
            db.add(
                ConversationsMeta(
                    id=self.conversation_id,
                    organization_id=self.org,
                    bot_id=self.bot_id,
                    lead_id=lead_id,
                )
            )
            await db.commit()
        self.session = ChatSession(
            conversation_id=str(self.conversation_id),
            organization_id=self.org,
            visitor_id=str(self.visitor_id),
        )
        _, result = await self.run_turn(
            "Correction: MCom, online Full Stack instead.",
            TurnExtraction(
                observations=[
                    Observation(
                        field="education_level",
                        value="MCom",
                        correction=True,
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="preferred_mode",
                        value="online",
                        correction=True,
                        attribution="enquirer_self",
                    ),
                    Observation(
                        field="course_interest",
                        value="Full Stack",
                        correction=True,
                        attribution="enquirer_self",
                    ),
                ]
            ),
        )
        self.assertEqual(result["state"]["lead_id"], str(lead_id))
        async with self.dash() as db:
            lead = await db.get(Leads, lead_id)
            self.assertEqual(lead.education_level, "MCom")
            self.assertEqual(lead.preferred_mode, "online")
            self.assertEqual(lead.course_interest, "Full Stack")
            lead.ai_summary = "Current: MCom, online Full Stack. Previously BCom and offline Data Analytics."
            await db.commit()
        invoke = AsyncMock(
            return_value=SummaryText(
                conversation_summary="This older chat discussed BCom and offline Data Analytics.",
                lead_overview="Current: MCom and online Full Stack; earlier chat discussed Data Analytics.",
            )
        )
        model = SimpleNamespace(
            with_structured_output=lambda *args, **kwargs: SimpleNamespace(
                ainvoke=invoke
            )
        )
        with patch("app.services.lead_summary.ChatOpenAI", return_value=model):
            await summarize_conversation(older_id)
        payload = json.loads(invoke.call_args.args[0][1].content)
        self.assertEqual(
            payload["accepted_state"]["profile"]["education_level"], "MCom"
        )
        self.assertEqual(
            payload["accepted_state"]["profile"]["preferred_mode"], "online"
        )
        self.assertIn("Full Stack", payload["previous_lead_overview"])
        self.assertTrue(
            any("BCom" in message["content"] for message in payload["messages"])
        )
        self.assertIn("created_at", payload["messages"][0])
        async with self.dash() as db:
            old = await db.get(ConversationsMeta, older_id)
            lead = await db.get(Leads, lead_id)
            self.assertEqual(old.summary_status, "completed")
            self.assertIn("BCom", old.ai_summary)
            self.assertEqual(lead.education_level, "MCom")
            self.assertIn("Current: MCom", lead.ai_summary)
