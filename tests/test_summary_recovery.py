import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from openai import LengthFinishReasonError

from app.services.lead_summary import (
    SummaryText,
    generate_summary,
    summarize_conversation,
)


class SummaryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncation_retries_once_with_larger_budget_and_low_reasoning(self):
        summary = SummaryText(conversation_summary="Summary", lead_overview="Overview")
        invoke = AsyncMock(
            side_effect=[
                LengthFinishReasonError(completion=SimpleNamespace(usage=None)),
                summary,
            ]
        )
        model = SimpleNamespace(
            with_structured_output=lambda *a, **kw: SimpleNamespace(ainvoke=invoke)
        )
        with patch(
            "app.services.lead_summary.ChatOpenAI", return_value=model
        ) as constructor:
            result = await generate_summary("configured-model", [], SimpleNamespace())
        self.assertEqual(result, summary)
        self.assertEqual(
            [
                call.kwargs["max_completion_tokens"]
                for call in constructor.call_args_list
            ],
            [2400, 4800],
        )
        self.assertTrue(
            all(
                call.kwargs["reasoning_effort"] == "low"
                for call in constructor.call_args_list
            )
        )
        self.assertTrue(
            all(
                call.kwargs["model"] == "configured-model"
                for call in constructor.call_args_list
            )
        )

    async def test_other_failures_do_not_repeat_provider_requests(self):
        invoke = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        model = SimpleNamespace(
            with_structured_output=lambda *a, **kw: SimpleNamespace(ainvoke=invoke)
        )
        with patch(
            "app.services.lead_summary.ChatOpenAI", return_value=model
        ) as constructor:
            with self.assertRaises(RuntimeError):
                await generate_summary("configured-model", [], SimpleNamespace())
        self.assertEqual(constructor.call_count, 1)

    async def test_changed_snapshot_releases_billing_so_pending_summary_can_retry(self):
        cid, bot_id = uuid4(), uuid4()
        conversation = SimpleNamespace(
            id=cid,
            status="closed",
            summary_status="pending",
            lead_id=None,
            lead_capture_state=None,
            ai_summary=None,
            summary_through_message_id=None,
            bot_id=bot_id,
            organization_id="org",
        )
        changed = SimpleNamespace(
            status="closed",
            summary_status="pending",
            summary_through_message_id=None,
            lead_capture_state={},
        )
        initial, write, chat = AsyncMock(), AsyncMock(), AsyncMock()
        initial.get.return_value = conversation
        write.scalar.return_value = changed
        chat.scalar.return_value = SimpleNamespace(model="configured-model")
        row = SimpleNamespace(
            id=uuid4(),
            role="user",
            content="Synthetic test",
            created_at=datetime.now(UTC),
        )
        chat.scalars.return_value = SimpleNamespace(all=lambda: [row])

        def ctx(db):
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=db)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        with (
            patch(
                "app.services.lead_summary.create_async_dashboard_db_session",
                side_effect=[ctx(initial), ctx(write)],
            ),
            patch(
                "app.services.lead_summary.create_async_chat_db_session",
                return_value=ctx(chat),
            ),
            patch(
                "app.services.lead_summary.reserve_auxiliary",
                AsyncMock(return_value=True),
            ),
            patch("app.services.lead_summary.finish_auxiliary", AsyncMock()) as finish,
            patch(
                "app.services.lead_summary.generate_summary",
                AsyncMock(
                    return_value=SummaryText(
                        conversation_summary="Summary", lead_overview="Overview"
                    )
                ),
            ),
        ):
            await summarize_conversation(cid)
        self.assertFalse(finish.await_args.args[2])
        self.assertEqual(changed.summary_status, "pending")
