"""Scheduled quota recovery and disclosed retention, using the existing RQ cron."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select, text

from app.db.session import (
    create_async_chat_db_session,
    create_async_dashboard_db_session,
)
from app.infra.r2_storage import r2_delete_object
from app.models.chat_db_models import Messages, ResponseLatency, RetrievalLogs
from app.models.dashboard_db_models import (
    BillingNotice,
    ConversationsMeta,
    Leads,
    Notifications,
    OrganizationMembers,
)

logger = logging.getLogger(__name__)


async def recover_stale_reservations(org_id: str | None = None):
    # Model turns time out at 90s; training jobs at 30 minutes. Recover only
    # abandoned work, under the same org lock as reservation/finalization.
    async with create_async_dashboard_db_session() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT id,organization_id,kind FROM billing_usage WHERE status='reserved' AND (CAST(:org AS text) IS NULL OR organization_id=:org) AND ((kind IN ('reply','summary') AND created_at<now()-interval '10 minutes') OR (kind='training' AND created_at<now()-interval '45 minutes')) LIMIT 100"
                    ),
                    {"org": org_id},
                )
            )
            .mappings()
            .all()
        )
    for row in rows:
        async with create_async_dashboard_db_session() as db:
            await db.execute(
                text(
                    "SELECT organization_id FROM billing_accounts WHERE organization_id=:org FOR UPDATE"
                ),
                {"org": row["organization_id"]},
            )
            usage = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM billing_usage WHERE id=:id AND status='reserved' FOR UPDATE"
                        ),
                        {"id": row["id"]},
                    )
                )
                .mappings()
                .first()
            )
            if not usage:
                continue
            if usage["kind"] == "reply":
                if usage["pack_id"]:
                    await db.execute(
                        text(
                            "UPDATE billing_credit_packs SET reserved=reserved-1 WHERE id=:id"
                        ),
                        {"id": usage["pack_id"]},
                    )
                else:
                    await db.execute(
                        text(
                            "UPDATE billing_periods SET replies_reserved=replies_reserved-1 WHERE id=:id"
                        ),
                        {"id": usage["period_id"]},
                    )
            elif usage["kind"] == "training":
                await db.execute(
                    text(
                        "UPDATE billing_periods SET training_reserved=training_reserved-:amount WHERE id=:id"
                    ),
                    {"id": usage["period_id"], "amount": usage["amount"]},
                )
                await db.execute(
                    text(
                        "UPDATE billing_source_usage SET pending_tokens=0 WHERE source_id=CAST(:source AS uuid)"
                    ),
                    {"source": usage["cost"].get("source_id")},
                )
            await db.execute(
                text(
                    "UPDATE billing_usage SET status='released',completed_at=now() WHERE id=:id"
                ),
                {"id": usage["id"]},
            )
            await db.commit()


async def retain_organization(org_id, account):
    now = datetime.now(UTC)
    limits = account["limits"]
    expiry = account.get("paid_through") or account["trial_ends_at"]
    recovery_ended = expiry + timedelta(days=30) < now
    transcript_cutoff = now - timedelta(days=int(limits["transcriptDays"]))
    lead_cutoff = now - timedelta(days=int(limits["leadDays"]))
    if account["plan_id"] == "trial":
        if not recovery_ended:
            return
        transcript_cutoff = lead_cutoff = now
    elif recovery_ended:
        transcript_cutoff = lead_cutoff = now
    async with create_async_dashboard_db_session() as db:
        # A notice is persisted at least seven days before automatic removal.
        notice_id = f"retention:{org_id}"
        notice = await db.get(BillingNotice, notice_id)
        candidates = list(
            (
                await db.scalars(
                    select(ConversationsMeta.id)
                    .where(
                        ConversationsMeta.organization_id == org_id,
                        ConversationsMeta.status == "closed",
                        ConversationsMeta.created_at < transcript_cutoff,
                    )
                    .limit(500)
                )
            ).all()
        )
        old_leads = list(
            (
                await db.scalars(
                    select(Leads.id)
                    .where(
                        Leads.organization_id == org_id,
                        Leads.deleted_at.is_(None),
                        Leads.captured_at < lead_cutoff,
                    )
                    .limit(500)
                )
            ).all()
        )
        if not candidates and not old_leads:
            return
        if not notice:
            admins = (
                await db.scalars(
                    select(OrganizationMembers.user_id).where(
                        OrganizationMembers.organization_id == org_id,
                        OrganizationMembers.role == "admin",
                    )
                )
            ).all()
            for user_id in admins:
                db.add(
                    Notifications(
                        id=__import__("uuid").uuid4(),
                        organization_id=org_id,
                        user_id=user_id,
                        type="billing_retention",
                        title="Older records are scheduled for removal",
                        body="Export your organization records in Billing within seven days. Older records will be removed according to your plan’s retention policy.",
                        channels=["dashboard"],
                        metadata_json={"url": "/dashboard/subscription"},
                    )
                )
            await db.execute(
                text(
                    "INSERT INTO billing_notices(id,organization_id,kind,sent_at) VALUES (:id,:org,'retention',now())"
                ),
                {"id": notice_id, "org": org_id},
            )
            await db.commit()
            return
        if (notice.sent_at or notice.created_at) + timedelta(days=7) > now:
            return
    if candidates:
        # Attachments share the conversation's retention window. Keep a
        # conversation eligible for retry if its object removal fails.
        async with create_async_dashboard_db_session() as db:
            files = (
                (
                    await db.execute(
                        text(
                            "SELECT id,bucket,path FROM files WHERE organization_id=:org AND purpose='conversation' AND split_part(path,'/',2)=ANY(:ids)"
                        ),
                        {"org": org_id, "ids": [str(id) for id in candidates]},
                    )
                )
                .mappings()
                .all()
            )
        failed = set()
        for file in files:
            try:
                if not file["bucket"] or not file["path"]:
                    raise ValueError("Attachment storage metadata is incomplete")
                await asyncio.to_thread(r2_delete_object, file["bucket"], file["path"])
                async with create_async_dashboard_db_session() as db:
                    await db.execute(
                        text("DELETE FROM files WHERE id=:id AND organization_id=:org"),
                        {"id": file["id"], "org": org_id},
                    )
                    await db.commit()
            except Exception:
                failed.add(file["path"].split("/")[1])
                logger.exception("Could not remove expired conversation attachment")
        candidates = [id for id in candidates if str(id) not in failed]
    if candidates:
        async with create_async_chat_db_session() as chat:
            await chat.execute(
                delete(ResponseLatency).where(
                    ResponseLatency.conversation_id.in_(candidates)
                )
            )
            await chat.execute(
                delete(RetrievalLogs).where(
                    RetrievalLogs.conversation_id.in_(candidates)
                )
            )
            await chat.execute(
                delete(Messages).where(Messages.conversation_id.in_(candidates))
            )
            # LangGraph checkpoints also contain conversation history.
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                exists = await chat.scalar(
                    text("SELECT to_regclass(:table)"), {"table": table}
                )
                if exists:
                    await chat.execute(
                        text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"),
                        {"ids": [str(id) for id in candidates]},
                    )
            await chat.commit()
        async with create_async_dashboard_db_session() as db:
            await db.execute(
                delete(ConversationsMeta).where(
                    ConversationsMeta.organization_id == org_id,
                    ConversationsMeta.id.in_(candidates),
                    ConversationsMeta.status == "closed",
                )
            )
            await db.commit()
    if old_leads:
        async with create_async_dashboard_db_session() as db:
            # Follow-ups cascade under the Prisma-owned lead relation.
            await db.execute(
                delete(Leads).where(
                    Leads.organization_id == org_id, Leads.id.in_(old_leads)
                )
            )
            await db.commit()


async def maintain_usage_and_retention():
    await recover_stale_reservations()
    async with create_async_dashboard_db_session() as db:
        accounts = (
            (await db.execute(text("SELECT * FROM billing_accounts"))).mappings().all()
        )
        await db.execute(
            text(
                "UPDATE billing_usage SET response=NULL WHERE completed_at < now()-interval '7 days' AND response IS NOT NULL"
            )
        )
        await db.commit()
    if os.getenv("BILLING_RETENTION_ENABLED", "true") == "true":
        for account in accounts:
            try:
                await retain_organization(account["organization_id"], dict(account))
            except Exception:
                logger.exception("Billing retention failed for an organization")
    base = os.getenv("DASHBOARD_APP_URL", "http://localhost:4000").rstrip("/")
    secret = os.getenv("BILLING_MAINTENANCE_SECRET")
    if secret:
        async with httpx.AsyncClient(timeout=240) as client:
            response = await client.post(
                f"{base}/api/billing/maintenance",
                headers={"Authorization": f"Bearer {secret}"},
            )
            response.raise_for_status()


def run_billing_maintenance():
    asyncio.run(maintain_usage_and_retention())
