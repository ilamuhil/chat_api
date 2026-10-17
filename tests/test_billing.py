from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import text

from app.db.session import (
    async_dashboard_db_engine,
    create_async_dashboard_db_session,
    create_dashboard_db_session,
)
from app.ingestion.errors import TrainingFailure
from app.services.billing import (
    BillingUnavailable,
    TurnMeter,
    access_allowed,
    billing_tokens,
    finish_reply,
    reserve_reply,
)
from app.services.billing_training import finish_training, reserve_training


class BillingRulesTests(unittest.TestCase):
    def test_expired_and_grace_access(self):
        now = datetime.now(UTC)
        account = {
            "status": "trialing",
            "trial_ends_at": now - timedelta(seconds=1),
            "paid_through": None,
            "grace_until": None,
        }
        self.assertFalse(access_allowed(account, now))
        account.update(status="past_due", grace_until=now + timedelta(days=1))
        self.assertTrue(access_allowed(account, now))
        account.update(status="refunded", grace_until=now + timedelta(days=1))
        self.assertFalse(access_allowed(account, now))

    def test_turn_budget_stops_unbounded_model_calls(self):
        meter = TurnMeter()
        with self.assertRaises(BillingUnavailable):
            for _ in range(7):
                meter.on_chat_model_start(
                    {}, [[SimpleNamespace(content="short prompt")]]
                )
        self.assertTrue(meter.snapshot()["budget_blocked"])


@unittest.skipUnless(
    os.getenv("RUN_BILLING_DB_TESTS") == "1", "Development DB verification is opt-in"
)
class BillingDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_credit_retries_training_expiry_and_tenant_isolation(self):
        org = f"billing-python-test-{uuid4()}"
        period = uuid4()
        source = uuid4()
        bot = uuid4()
        config = uuid4()
        limits = {
            "corpusTokens": 100,
            "trainingTokens": 100,
            "transcriptDays": 90,
            "leadDays": 365,
        }
        async with create_async_dashboard_db_session() as db:
            await db.execute(
                text("INSERT INTO organizations(id) VALUES (:org)"), {"org": org}
            )
            await db.execute(
                text(
                    "INSERT INTO billing_accounts(organization_id,plan_id,plan_version,limits,status,trial_ends_at,current_period_id,updated_at) VALUES (:org,'trial','2026-10-16',CAST(:limits AS jsonb),'trialing',now()+interval '14 days',:period,now())"
                ),
                {"org": org, "period": period, "limits": json.dumps(limits)},
            )
            await db.execute(
                text(
                    "INSERT INTO billing_periods(id,organization_id,plan_id,plan_version,starts_at,ends_at,reply_limit,training_limit) VALUES (:period,:org,'trial','2026-10-16',now(),now()+interval '14 days',1,100)"
                ),
                {"org": org, "period": period},
            )
            await db.execute(
                text(
                    "INSERT INTO bots(id,organization_id,name) VALUES (:id,:org,'Billing test bot')"
                ),
                {"id": bot, "org": org},
            )
            await db.execute(
                text(
                    "INSERT INTO training_sources(id,organization_id,bot_id,updated_at) VALUES (:id,:org,:bot,now())"
                ),
                {"id": source, "org": org, "bot": bot},
            )
            await db.commit()
        try:
            digest = hashlib.sha256(b"question").hexdigest()
            first = f"test:{uuid4()}"
            second = f"test:{uuid4()}"
            results = await asyncio.gather(
                reserve_reply(org, first, digest),
                reserve_reply(org, second, digest),
                return_exceptions=True,
            )
            self.assertEqual(
                sum(isinstance(result, BillingUnavailable) for result in results), 1
            )
            winner = first if results[0] is None else second
            await finish_reply(
                winner, {"type": "message", "message": "saved answer"}, TurnMeter()
            )
            replay = await reserve_reply(org, winner, digest)
            if replay is None:
                self.fail("Expected a saved response when replaying the request")
            self.assertEqual(replay["message"], "saved answer")
            with self.assertRaises(BillingUnavailable):
                await reserve_reply(org, winner, "different-content")
            with self.assertRaises(BillingUnavailable):
                await reserve_reply("different-organization", winner, digest)
            async with create_async_dashboard_db_session() as db:
                count = (
                    await db.execute(
                        text(
                            "SELECT replies_used,replies_reserved FROM billing_periods WHERE id=:id"
                        ),
                        {"id": period},
                    )
                ).one()
                self.assertEqual(tuple(count), (1, 0))
                await db.execute(
                    text("UPDATE billing_periods SET reply_limit=2 WHERE id=:id"),
                    {"id": period},
                )
                await db.commit()
            retry = f"test:{uuid4()}"
            await reserve_reply(org, retry, digest)
            await finish_reply(retry, None, TurnMeter())
            await reserve_reply(org, retry, digest)
            await finish_reply(retry, {"message": "retry worked"}, TurnMeter())
            with create_dashboard_db_session() as db:
                src = SimpleNamespace(id=source, organization_id=org)
                cfg = SimpleNamespace(
                    id=config, provider="openai", model="text-embedding-3-small"
                )
                documents = [
                    SimpleNamespace(token_count=60, content="training fixture")
                ]
                key, duplicate = reserve_training(db, src, cfg, documents)
                self.assertFalse(duplicate)
                finish_training(db, key, False)
                key, duplicate = reserve_training(db, src, cfg, documents)
                finish_training(db, key, True)
                _, duplicate = reserve_training(db, src, cfg, documents)
                self.assertTrue(duplicate)
                balance = db.execute(
                    text(
                        "SELECT training_used,training_reserved FROM billing_periods WHERE id=:id"
                    ),
                    {"id": period},
                ).one()
                self.assertEqual(
                    tuple(balance), (billing_tokens("training fixture"), 0)
                )
                with self.assertRaises(TrainingFailure):
                    reserve_training(
                        db,
                        src,
                        cfg,
                        [
                            SimpleNamespace(
                                token_count=60,
                                content="a different training fixture " * 100,
                            )
                        ],
                    )
            async with create_async_dashboard_db_session() as db:
                await db.execute(
                    text(
                        "UPDATE billing_accounts SET trial_ends_at=now()-interval '1 day' WHERE organization_id=:org"
                    ),
                    {"org": org},
                )
                await db.commit()
            with self.assertRaises(BillingUnavailable):
                await reserve_reply(org, f"test:{uuid4()}", digest)
        finally:
            async with create_async_dashboard_db_session() as db:
                await db.execute(
                    text("DELETE FROM organizations WHERE id=:org"), {"org": org}
                )
                await db.commit()
            await async_dashboard_db_engine.dispose()


@unittest.skipUnless(
    os.getenv("RUN_BILLING_DB_TESTS") == "1", "Development DB verification is opt-in"
)
class BillingRetentionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_reservation_and_seven_day_retention_notice(self):
        from app.db.session import async_chat_engine
        from app.services.billing_maintenance import (
            recover_stale_reservations,
            retain_organization,
        )

        org = f"billing-retention-test-{uuid4()}"
        bot, period, conversation, lead = [uuid4() for _ in range(4)]
        usage = f"test-stale:{uuid4()}"
        limits = {"transcriptDays": 90, "leadDays": 365}
        async with create_async_dashboard_db_session() as db:
            await db.execute(
                text("INSERT INTO organizations(id) VALUES (:org)"), {"org": org}
            )
            await db.execute(
                text(
                    "INSERT INTO bots(id,organization_id,name) VALUES (:id,:org,'Retention fixture')"
                ),
                {"id": bot, "org": org},
            )
            await db.execute(
                text(
                    "INSERT INTO billing_accounts(organization_id,plan_id,plan_version,limits,status,trial_ends_at,paid_through,current_period_id,updated_at) VALUES(:org,'starter','2026-10-16',CAST(:limits AS jsonb),'active',now(),now()+interval '30 days',:period,now())"
                ),
                {"org": org, "limits": json.dumps(limits), "period": period},
            )
            await db.execute(
                text(
                    "INSERT INTO billing_periods(id,organization_id,plan_id,plan_version,starts_at,ends_at,reply_limit,replies_reserved,training_limit) VALUES(:id,:org,'starter','2026-10-16',now(),now()+interval '30 days',10,1,100)"
                ),
                {"id": period, "org": org},
            )
            await db.execute(
                text(
                    "INSERT INTO billing_usage(id,organization_id,period_id,kind,status,amount,created_at) VALUES(:id,:org,:period,'reply','reserved',1,now()-interval '15 minutes')"
                ),
                {"id": usage, "org": org, "period": period},
            )
            await db.execute(
                text(
                    "INSERT INTO conversations_meta(id,organization_id,bot_id,status,created_at) VALUES(:id,:org,:bot,'closed',now()-interval '500 days')"
                ),
                {"id": conversation, "org": org, "bot": bot},
            )
            await db.execute(
                text(
                    "INSERT INTO leads(id,organization_id,bot_id,visitor_id,captured_at,updated_at) VALUES(:id,:org,:bot,:visitor,now()-interval '500 days',now())"
                ),
                {"id": lead, "org": org, "bot": bot, "visitor": uuid4()},
            )
            await db.commit()
        try:
            await recover_stale_reservations(org)
            async with create_async_dashboard_db_session() as db:
                self.assertEqual(
                    await db.scalar(
                        text(
                            "SELECT replies_reserved FROM billing_periods WHERE id=:id"
                        ),
                        {"id": period},
                    ),
                    0,
                )
                self.assertEqual(
                    await db.scalar(
                        text("SELECT status FROM billing_usage WHERE id=:id"),
                        {"id": usage},
                    ),
                    "released",
                )
                account = dict(
                    (
                        await db.execute(
                            text(
                                "SELECT * FROM billing_accounts WHERE organization_id=:org"
                            ),
                            {"org": org},
                        )
                    )
                    .mappings()
                    .one()
                )
            await retain_organization(org, account)
            async with create_async_dashboard_db_session() as db:
                self.assertEqual(
                    await db.scalar(
                        text(
                            "SELECT count(*) FROM conversations_meta WHERE organization_id=:org"
                        ),
                        {"org": org},
                    ),
                    1,
                )
                self.assertEqual(
                    await db.scalar(
                        text("SELECT count(*) FROM leads WHERE organization_id=:org"),
                        {"org": org},
                    ),
                    1,
                )
                await db.execute(
                    text(
                        "UPDATE billing_notices SET sent_at=now()-interval '8 days' WHERE organization_id=:org AND kind='retention'"
                    ),
                    {"org": org},
                )
                await db.commit()
            await retain_organization(org, account)
            async with create_async_dashboard_db_session() as db:
                self.assertEqual(
                    await db.scalar(
                        text(
                            "SELECT count(*) FROM conversations_meta WHERE organization_id=:org"
                        ),
                        {"org": org},
                    ),
                    0,
                )
                self.assertEqual(
                    await db.scalar(
                        text("SELECT count(*) FROM leads WHERE organization_id=:org"),
                        {"org": org},
                    ),
                    0,
                )
        finally:
            async with create_async_dashboard_db_session() as db:
                await db.execute(
                    text("DELETE FROM organizations WHERE id=:org"), {"org": org}
                )
                await db.commit()
            await async_dashboard_db_engine.dispose()
            await async_chat_engine.dispose()
