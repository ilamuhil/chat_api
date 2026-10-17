"""Server-side turn timings. No message text or contact details are recorded."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from app.db.session import create_async_chat_db_session
from app.domain import ChatSession
from app.models.chat_db_models import ResponseLatency

logger = logging.getLogger(__name__)


@dataclass
class TurnLatency:
    received_at: float = field(default_factory=perf_counter)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stages: dict[str, float] = field(default_factory=dict)
    enqueued_at: float | None = None
    response_ms: float | None = None
    outcome: str = "error"
    request_message_id: UUID | None = None

    def response_sent(self) -> None:
        # Freeze at the first terminal payload, before transcript/billing cleanup.
        if self.response_ms is None:
            self.response_ms = (perf_counter() - self.received_at) * 1000


current_latency: ContextVar[TurnLatency | None] = ContextVar(
    "turn_latency", default=None
)


@contextmanager
def latency_stage(name: str) -> Iterator[None]:
    timing = current_latency.get()
    started = perf_counter()
    try:
        yield
    finally:
        if timing is not None:
            timing.stages[name] = (
                timing.stages.get(name, 0.0) + (perf_counter() - started) * 1000
            )

def timed_stage(name: str):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with latency_stage(name):
                return await function(*args, **kwargs)
        return wrapped
    return decorate


def set_latency_outcome(outcome: str) -> None:
    if timing := current_latency.get():
        timing.outcome = outcome


def mark_response_sent(payload: dict[str, Any]) -> None:
    if payload.get("role") == "ai" or payload.get("type") in {
        "error",
        "ai_unavailable",
    }:
        if timing := current_latency.get():
            timing.response_sent()


async def persist_latency(timing: TurnLatency, session, bot_pref) -> None:
    """Best effort, after delivery; a telemetry failure never fails a reply."""
    elapsed_ms = (perf_counter() - timing.received_at) * 1000
    outcome = timing.outcome
    if timing.response_ms is None and outcome in {"success", "fallback", "cached"}:
        outcome = "disconnected"
    try:
        async with asyncio.timeout(2):
            async with create_async_chat_db_session() as db:
                db.add(
                    ResponseLatency(
                        id=uuid4(),
                        organization_id=session.organization_id,
                        bot_id=UUID(str(bot_pref["bot_id"])),
                        conversation_id=UUID(session.conversation_id),
                        request_message_id=timing.request_message_id,
                        model=bot_pref.get("llm_model"),
                        outcome=outcome,
                        response_ms=timing.response_ms,
                        elapsed_ms=elapsed_ms,
                        stages_ms={
                            key: round(value, 3) for key, value in timing.stages.items()
                        },
                        created_at=timing.created_at,
                    )
                )
                await db.commit()
    except Exception:
        logger.exception(
            "Could not persist response latency",
            extra={
                "conversation_id": session.conversation_id,
            },
        )


@asynccontextmanager
async def track_turn(
    timing: TurnLatency, session: ChatSession, bot_pref: dict[str, Any]
) -> AsyncIterator[None]:
    token = current_latency.set(timing)
    if timing.enqueued_at is not None:
        timing.stages["queue_wait"] = (perf_counter() - timing.enqueued_at) * 1000
    try:
        yield
    except asyncio.CancelledError:
        if timing.response_ms is None:
            timing.outcome = "cancelled"
        raise
    except Exception:
        if timing.response_ms is None:
            timing.outcome = "error"
        raise
    finally:
        current_latency.reset(token)
        await persist_latency(timing, session, bot_pref)
