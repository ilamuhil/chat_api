"""Durable organization quotas in the Prisma-owned dashboard database.

All quota mutations take the account row lock, shared with dashboard mutations.
The browser cannot supply balances or choose an organization for a reservation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from typing import Any
from uuid import uuid4

import tiktoken
from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import text

from app.db.session import create_async_dashboard_db_session
from app.services.latency import mark_response_sent, set_latency_outcome, timed_stage

logger = logging.getLogger(__name__)
TOKENIZER_VERSION = "cl100k_base-v1"
STANDARD_MODEL = os.getenv("BILLING_STANDARD_MODEL", "gpt-5.6-luna")
DEFAULT_MODEL_RATES = {
    "gpt-5.6-luna": {
        "input": 0.20,
        "cached": 0.02,
        "cache_write": 0.25,
        "output": 1.20,
    },
    "text-embedding-3-small": {"input": 0.02, "output": 0},
}


def billing_tokens(content: str) -> int:
    return len(
        tiktoken.get_encoding("cl100k_base").encode(content, disallowed_special=())
    )


def priced_usage(usage: list[dict]) -> dict:
    # Optional versioned rates are USD per million tokens. Unknown models are
    # explicitly unpriced, never recorded as zero-cost model work.
    rates = {
        **DEFAULT_MODEL_RATES,
        **json.loads(os.getenv("BILLING_MODEL_RATES_JSON", "{}")),
    }
    total = 0.0
    missing = []
    for item in usage:
        model = item.get("model")
        rate = rates.get(model) or rates.get(
            re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model or "")
        )
        if not rate:
            missing.append(model or "unknown")
            continue
        cached = int((item.get("input_token_details") or {}).get("cache_read", 0))
        cache_write = int(
            (item.get("input_token_details") or {}).get("cache_creation", 0)
        )
        total += (
            max(0, int(item.get("input_tokens", 0)) - cached - cache_write)
            * float(rate["input"])
            + cached * float(rate.get("cached", rate["input"]))
            + cache_write * float(rate.get("cache_write", rate["input"]))
            + int(item.get("output_tokens", 0)) * float(rate["output"])
        ) / 1_000_000
    return {
        "estimated_cost_usd": None if missing else total,
        "unpriced_models": sorted(set(missing)),
        "cost_status": "missing_model_rates"
        if missing
        else "estimated_from_recorded_tokens",
        "pricing_version": os.getenv(
            "BILLING_MODEL_RATES_VERSION", "openai-standard-2026-10-16"
        ),
    }


class BillingUnavailable(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def access_allowed(account: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)

    def future(value):
        if not value:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value > now

    if account["status"] == "trialing":
        return future(account["trial_ends_at"])
    return future(account.get("paid_through")) or (
        account["status"] == "past_due" and future(account.get("grace_until"))
    )


class TurnMeter(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self):
        self.calls = 0
        self.reply_completed = False
        self.organization_id: str | None = None
        self.estimated_tokens = 0
        self.usage: list[dict[str, Any]] = []
        self.blocked = False
        self.embedding_usage = []
        self.tools: dict[str, int] = {}

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = (serialized or {}).get("name", "tool")
        self.tools[name] = self.tools.get(name, 0) + 1

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.calls += 1
        # A conservative pre-call bound includes tool schemas/prompt overhead.
        # Actual provider usage, including non-visible output, is recorded below.
        self.estimated_tokens += (
            sum(
                billing_tokens(str(getattr(m, "content", "")))
                for batch in messages
                for m in batch
            )
            + 2000
        )
        if self.calls > int(
            os.getenv("BILLING_MAX_MODEL_CALLS", "6")
        ) or self.estimated_tokens > int(os.getenv("BILLING_MAX_TURN_TOKENS", "40000")):
            self.blocked = True
            raise BillingUnavailable(
                "turn_budget",
                "This request is too large. Please ask a shorter question or speak with a counsellor.",
            )

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                metadata = getattr(message, "response_metadata", {}) or {}
                if usage:
                    self.usage.append(
                        {
                            "provider": "openai",
                            "model": metadata.get("model_name")
                            or metadata.get("model"),
                            **usage,
                        }
                    )

    def snapshot(self):
        return {
            "model_calls": self.calls,
            "usage": self.usage,
            "budget_blocked": self.blocked,
            **priced_usage(self.usage + self.embedding_usage),
            "embeddings": self.embedding_usage,
            "tools": self.tools,
            "tokenizer_version": TOKENIZER_VERSION,
            "estimated_input_tokens": self.estimated_tokens,
        }


current_meter: ContextVar[TurnMeter | None] = ContextVar("billing_meter", default=None)
current_reservation: ContextVar[str | None] = ContextVar(
    "billing_reservation", default=None
)


def model_callbacks() -> list[BaseCallbackHandler]:
    meter = current_meter.get()
    return [meter] if meter else []


@timed_stage("billing_reservation")
async def reserve_reply(org_id: str, request_id: str, request_hash: str):
    async with create_async_dashboard_db_session() as db:
        account = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM billing_accounts WHERE organization_id=:org FOR UPDATE"
                    ),
                    {"org": org_id},
                )
            )
            .mappings()
            .first()
        )
        if not account or not access_allowed(dict(account)):
            raise BillingUnavailable(
                "plan_expired",
                "The assistant is unavailable right now. Leave your details and the institute can help you directly.",
            )
        existing = (
            (
                await db.execute(
                    text("SELECT * FROM billing_usage WHERE id=:id"), {"id": request_id}
                )
            )
            .mappings()
            .first()
        )
        if existing:
            if (
                existing["organization_id"] != org_id
                or (existing["cost"] or {}).get("request_hash") != request_hash
            ):
                raise BillingUnavailable(
                    "invalid_request_id", "Please send your message again."
                )
            if existing["status"] == "completed":
                if existing["response"] is None:
                    raise BillingUnavailable(
                        "request_expired",
                        "This previous request is no longer available. Send a new message to continue.",
                    )
                return existing["response"]
            if existing["status"] == "reserved":
                raise BillingUnavailable(
                    "request_pending",
                    "Your previous message is still being processed. Please wait a moment.",
                )
        traffic = (
            (
                await db.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE status='reserved') AS active, count(*) FILTER (WHERE created_at>now()-interval '1 minute') AS recent FROM billing_usage WHERE organization_id=:org AND kind='reply' AND created_at>now()-interval '10 minutes'"
                    ),
                    {"org": org_id},
                )
            )
            .mappings()
            .one()
        )
        if traffic["active"] >= int(os.getenv("BILLING_ORG_CONCURRENCY", "5")):
            raise BillingUnavailable(
                "temporarily_busy",
                "The assistant is busy. Please try again in a moment.",
            )
        if traffic["recent"] >= int(os.getenv("BILLING_ORG_REQUESTS_PER_MINUTE", "60")):
            raise BillingUnavailable(
                "rate_limited",
                "There are many requests right now. Please wait a minute and try again.",
            )
        period = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM billing_periods WHERE id=:id AND organization_id=:org FOR UPDATE"
                    ),
                    {"id": account["current_period_id"], "org": org_id},
                )
            )
            .mappings()
            .first()
        )
        if not period:
            raise BillingUnavailable(
                "billing_unavailable",
                "The assistant is temporarily unavailable. Please leave your contact details.",
            )
        pack_id = None
        if period["replies_used"] + period["replies_reserved"] >= period["reply_limit"]:
            pack = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM billing_credit_packs WHERE organization_id=:org AND NOT revoked AND expires_at>now() AND used+reserved<replies ORDER BY expires_at FOR UPDATE LIMIT 1"
                        ),
                        {"org": org_id},
                    )
                )
                .mappings()
                .first()
            )
            if account["plan_id"] == "trial" or not pack:
                raise BillingUnavailable(
                    "ai_limit_reached",
                    "Leave your details or request a counsellor and the institute can help you directly.",
                )
            pack_id = pack["id"]
            await db.execute(
                text(
                    "UPDATE billing_credit_packs SET reserved=reserved+1 WHERE id=:id"
                ),
                {"id": pack_id},
            )
        else:
            await db.execute(
                text(
                    "UPDATE billing_periods SET replies_reserved=replies_reserved+1 WHERE id=:id"
                ),
                {"id": period["id"]},
            )
        await db.execute(
            text("""INSERT INTO billing_usage (id,organization_id,period_id,kind,status,amount,pack_id,cost)
          VALUES (:id,:org,:period,'reply','reserved',1,:pack,CAST(:cost AS jsonb))
          ON CONFLICT (id) DO UPDATE SET status='reserved',period_id=EXCLUDED.period_id,pack_id=EXCLUDED.pack_id,cost=EXCLUDED.cost,created_at=now(),completed_at=NULL"""),
            {
                "id": request_id,
                "org": org_id,
                "period": period["id"],
                "pack": pack_id,
                "cost": json.dumps(
                    {
                        "request_hash": request_hash,
                        "attempts": [
                            *((existing["cost"] or {}).get("attempts", [])),
                            *(
                                [
                                    {
                                        k: v
                                        for k, v in (existing["cost"] or {}).items()
                                        if k != "attempts"
                                    }
                                ]
                                if existing
                                else []
                            ),
                        ]
                        if existing
                        else [],
                    }
                ),
            },
        )
        await db.commit()
        return None


@timed_stage("billing_finalize")
async def finish_reply(request_id: str, response: dict | None, meter: TurnMeter):
    async with create_async_dashboard_db_session() as db:
        organization_id = meter.organization_id
        if organization_id is None:
            organization_id = await db.scalar(
                text("SELECT organization_id FROM billing_usage WHERE id=:id"),
                {"id": request_id},
            )
        if organization_id is None:
            return
        # Materialize the account lock before locking usage; all quota paths
        # keep this order, including reservation and recovery.
        usage = (
            (
                await db.execute(
                    text("""
            WITH locked_account AS MATERIALIZED (
                SELECT organization_id FROM billing_accounts
                WHERE organization_id=:org FOR UPDATE
            )
            SELECT u.* FROM locked_account a
            JOIN billing_usage u ON u.organization_id=a.organization_id
            WHERE u.id=:id FOR UPDATE OF u
        """),
                    {"org": organization_id, "id": request_id},
                )
            )
            .mappings()
            .first()
        )
        if usage is None:
            return
        if usage["status"] != "reserved":
            return
        completed = response is not None
        if usage["pack_id"]:
            await db.execute(
                text(
                    "UPDATE billing_credit_packs SET reserved=reserved-1,used=used+:used WHERE id=:id"
                ),
                {"id": usage["pack_id"], "used": int(completed)},
            )
        else:
            await db.execute(
                text(
                    "UPDATE billing_periods SET replies_reserved=replies_reserved-1,replies_used=replies_used+:used WHERE id=:id"
                ),
                {"id": usage["period_id"], "used": int(completed)},
            )
        cost = {**(usage["cost"] or {}), **meter.snapshot()}
        await db.execute(
            text(
                "UPDATE billing_usage SET status=:status,response=CAST(:response AS jsonb),cost=CAST(:cost AS jsonb),completed_at=now() WHERE id=:id"
            ),
            {
                "id": request_id,
                "status": "completed" if completed else "released",
                "response": json.dumps(response),
                "cost": json.dumps(cost),
            },
        )
        await db.commit()


@timed_stage("billing_complete")
async def complete_current_reply(payload: dict):
    request_id = current_reservation.get()
    meter = current_meter.get()
    if request_id and meter:
        await finish_reply(request_id, payload, meter)
        meter.reply_completed = True


async def reserve_auxiliary(org_id: str, key: str) -> bool:
    async with create_async_dashboard_db_session() as db:
        account = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM billing_accounts WHERE organization_id=:org FOR UPDATE"
                    ),
                    {"org": org_id},
                )
            )
            .mappings()
            .first()
        )
        if not account or not access_allowed(dict(account)):
            raise BillingUnavailable(
                "plan_expired",
                "Background AI is paused until this organization has an active plan.",
            )
        existing = (
            await db.execute(
                text("SELECT status FROM billing_usage WHERE id=:id"), {"id": key}
            )
        ).scalar()
        if existing in {"reserved", "completed"}:
            return False
        period = (
            (
                await db.execute(
                    text("SELECT * FROM billing_periods WHERE id=:id"),
                    {"id": account["current_period_id"]},
                )
            )
            .mappings()
            .one()
        )
        count = await db.scalar(
            text(
                "SELECT count(*) FROM billing_usage WHERE organization_id=:org AND period_id=:period AND kind='summary' AND status IN ('reserved','completed')"
            ),
            {"org": org_id, "period": period["id"]},
        )
        if count >= period["reply_limit"] * 2 + 20:
            raise BillingUnavailable(
                "background_budget",
                "Background AI allowance reached for this billing cycle.",
            )
        await db.execute(
            text(
                "INSERT INTO billing_usage(id,organization_id,period_id,kind,status,amount,cost) VALUES(:id,:org,:period,'summary','reserved',0,'{}') ON CONFLICT(id) DO UPDATE SET status='reserved',created_at=now(),completed_at=NULL"
            ),
            {"id": key, "org": org_id, "period": period["id"]},
        )
        await db.commit()
        return True


async def finish_auxiliary(key: str, meter: TurnMeter, success: bool):
    async with create_async_dashboard_db_session() as db:
        await db.execute(
            text(
                "UPDATE billing_usage SET status=:status,cost=CAST(:cost AS jsonb),completed_at=now() WHERE id=:id AND status='reserved'"
            ),
            {
                "id": key,
                "status": "completed" if success else "released",
                "cost": json.dumps(meter.snapshot()),
            },
        )
        await db.commit()


def metered_reply(function):
    @wraps(function)
    async def wrapped(message_data, bot_pref, session, agent):
        raw_id = str(
            message_data.get("request_id") or message_data.get("message_id") or uuid4()
        )
        if len(raw_id) > 100:
            raw_id = str(uuid4())
        request_id = f"reply:{session.conversation_id}:{raw_id}"
        content = str(message_data.get("message") or message_data.get("content") or "")
        meter = TurnMeter()
        meter.organization_id = session.organization_id
        token = None
        reservation_token = None
        try:
            if len(content) > 8000:
                raise BillingUnavailable(
                    "message_too_large",
                    "Please keep your message under 8,000 characters.",
                )
            cached = await reserve_reply(
                session.organization_id,
                request_id,
                hashlib.sha256(content.encode()).hexdigest(),
            )
            if cached:
                set_latency_outcome("cached")
                if session.user_socket:
                    await session.user_socket.send_json(cached)
                    mark_response_sent(cached)
                return
            token = current_meter.set(meter)
            reservation_token = current_reservation.set(request_id)
            await asyncio.wait_for(
                function(message_data, bot_pref, session, agent), timeout=90
            )
        except BillingUnavailable as error:
            set_latency_outcome("rejected")
            if session.user_socket:
                await session.user_socket.send_json(
                    {
                        "type": "ai_unavailable",
                        "code": error.code,
                        "message": str(error),
                        "fallback": "contact",
                        "conversation_id": session.conversation_id,
                    }
                )
                mark_response_sent({"type": "ai_unavailable"})
        except Exception as error:
            set_latency_outcome(
                "timeout" if isinstance(error, TimeoutError) else "error"
            )
            logger.exception(
                "Metered AI request failed", extra={"request_id": request_id}
            )
            if session.user_socket:
                await session.user_socket.send_json(
                    {
                        "type": "ai_unavailable",
                        "code": "service_unavailable",
                        "message": "The assistant is temporarily unavailable. You can leave your details or request a counsellor.",
                        "fallback": "contact",
                        "conversation_id": session.conversation_id,
                    }
                )
                mark_response_sent({"type": "ai_unavailable"})
        finally:
            if reservation_token is not None:
                try:
                    if not meter.reply_completed:
                        await finish_reply(request_id, None, meter)
                finally:
                    current_reservation.reset(reservation_token)
            if token is not None:
                current_meter.reset(token)

    return wrapped


def standard_model(model: str) -> str:
    if model != STANDARD_MODEL:
        raise BillingUnavailable(
            "model_not_available",
            "This assistant’s model needs to be updated by an administrator.",
        )
    return model
