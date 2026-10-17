"""Opt-in checks against isolated PostgreSQL: nullable lead and real row locks."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete, select, update

from app.db.session import async_dashboard_db_engine, create_async_dashboard_db_session
from app.models.dashboard_db_models import Bots, ConversationsMeta, Leads
from app.services.lead_capture import read_lead_snapshot


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_LEAD_TESTS") == "1", "Opt-in PostgreSQL lead locking test"
)
class LeadLockDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_locks_both_rows_and_supports_unlinked_or_missing_lead(self):
        lead_id, conversation_id, visitor_id = uuid4(), uuid4(), uuid4()
        workers = []
        try:
            async with create_async_dashboard_db_session() as db:
                bot = await db.scalar(
                    select(Bots).where(Bots.organization_id.is_not(None)).limit(1)
                )
                if bot is None:
                    self.skipTest("No configured bot")
                bot_id, org = bot.id, bot.organization_id
                db.add(
                    Leads(
                        id=lead_id,
                        organization_id=org,
                        bot_id=bot_id,
                        visitor_id=visitor_id,
                    )
                )
                await db.flush()
                db.add(
                    ConversationsMeta(
                        id=conversation_id, organization_id=org, bot_id=bot_id
                    )
                )
                await db.commit()
            session = SimpleNamespace(
                conversation_id=str(conversation_id),
                organization_id=org,
                visitor_id=str(visitor_id),
            )
            async with create_async_dashboard_db_session() as owner:
                conv, lead = await read_lead_snapshot(owner, session, bot_id, lock=True)
                self.assertIsNone(conv.lead_id)
                self.assertEqual(
                    lead.id, lead_id
                )  # Visitor fallback, even without link.

                lead_started, conversation_started = asyncio.Event(), asyncio.Event()

                async def edit_lead():
                    async with create_async_dashboard_db_session() as db:
                        await db.connection()
                        lead_started.set()
                        await db.execute(
                            update(Leads)
                            .where(Leads.id == lead_id)
                            .values(name="Concurrent edit")
                        )
                        await db.commit()

                async def edit_conversation():
                    async with create_async_dashboard_db_session() as db:
                        await db.connection()
                        conversation_started.set()
                        await db.execute(
                            update(ConversationsMeta)
                            .where(ConversationsMeta.id == conversation_id)
                            .values(lead_id=lead_id)
                        )
                        await db.commit()

                workers = [
                    asyncio.create_task(edit_lead()),
                    asyncio.create_task(edit_conversation()),
                ]
                await asyncio.wait_for(
                    asyncio.gather(lead_started.wait(), conversation_started.wait()), 10
                )
                for worker in workers:
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(worker), 1)
                await owner.commit()
            await asyncio.wait_for(asyncio.gather(*workers), 10)
            async with create_async_dashboard_db_session() as db:
                conv, lead = await read_lead_snapshot(db, session, bot_id, lock=True)
                self.assertEqual(conv.lead_id, lead_id)
                self.assertEqual(lead.name, "Concurrent edit")
                await db.rollback()
                session.organization_id = "wrong-org"
                with self.assertRaises(ValueError):
                    await read_lead_snapshot(db, session, bot_id, lock=True)
                await db.rollback()
                session.organization_id = org
                await db.execute(
                    update(ConversationsMeta)
                    .where(ConversationsMeta.id == conversation_id)
                    .values(lead_id=None)
                )
                await db.execute(delete(Leads).where(Leads.id == lead_id))
                await db.commit()
                conv, lead = await read_lead_snapshot(db, session, bot_id, lock=True)
                self.assertIsNone(
                    lead
                )  # A missing nullable lead must not drop the conversation.
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            async with create_async_dashboard_db_session() as db:
                await db.execute(
                    delete(ConversationsMeta).where(
                        ConversationsMeta.id == conversation_id
                    )
                )
                await db.execute(delete(Leads).where(Leads.id == lead_id))
                await db.commit()
            await async_dashboard_db_engine.dispose()
