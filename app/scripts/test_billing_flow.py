"""Opt-in development end-to-end check with one real, bounded OpenAI turn."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import websockets
from sqlalchemy import delete, text

from app.db.session import create_chat_db_session, create_dashboard_db_session
from app.models.chat_db_models import (
    BotConfigurations,
    EmbeddingConfigurations,
    Messages,
    RetrievalLogs,
)
from app.services.billing import STANDARD_MODEL


async def main():
    dashboard_url = os.getenv("BILLING_TEST_BASE_URL", "http://localhost:4000")
    websocket_url = os.getenv("BILLING_TEST_WS_URL", "ws://127.0.0.1:18000/api/chat/ws")
    fixture_path = Path(
        os.getenv(
            "BILLING_TEST_FIXTURES",
            "../chat-dashboard/.billing-test/work/sandbox-fixtures.json",
        )
    )
    fixture = next(
        f for f in json.loads(fixture_path.read_text()) if f["outcome"] == "success"
    )
    org = f"billing-flow-test-{uuid4()}"
    bot, api_id, period, embedding_id, config_id = [uuid4() for _ in range(5)]
    api_key = "bot_test_" + uuid4().hex
    user_id = UUID(fixture["userId"])
    conversations = []
    limits = {
        "replies": 1,
        "members": 3,
        "bots": 1,
        "storageBytes": 100000000,
        "fileBytes": 10000000,
        "sources": 20,
        "corpusTokens": 500000,
        "trainingTokens": 1000000,
        "transcriptDays": 90,
        "leadDays": 365,
        "leadAlert": 1000,
    }
    with create_dashboard_db_session() as db:
        db.execute(
            text("INSERT INTO organizations(id,name) VALUES(:org,'Billing flow test')"),
            {"org": org},
        )
        db.execute(
            text(
                "INSERT INTO organization_members(id,organization_id,user_id,role) VALUES(:id,:org,:user,'admin')"
            ),
            {"id": uuid4(), "org": org, "user": user_id},
        )
        db.execute(
            text(
                "INSERT INTO bots(id,organization_id,name,institute_name,business_description,tone,first_message,capture_leads,capture_name,capture_email,capture_phone) VALUES(:id,:org,'Billing flow bot','Sample Institute','A sandbox test institute. Respond briefly and never invent institute details.','friendly','Hello from the sandbox.',true,true,true,true)"
            ),
            {"id": bot, "org": org},
        )
        db.execute(
            text(
                "INSERT INTO api_keys(id,name,key_hash,organization_id,bot_id,is_active) VALUES(:id,'Billing flow test',:hash,:org,:bot,true)"
            ),
            {
                "id": api_id,
                "hash": hashlib.sha256(api_key.encode()).hexdigest(),
                "org": org,
                "bot": bot,
            },
        )
        db.execute(
            text(
                "INSERT INTO billing_accounts(organization_id,plan_id,plan_version,limits,status,trial_ends_at,paid_through,current_period_id,updated_at) VALUES(:org,'starter','2026-10-16',CAST(:limits AS jsonb),'active',now(),now()+interval '30 days',:period,now())"
            ),
            {"org": org, "limits": json.dumps(limits), "period": period},
        )
        db.execute(
            text(
                "INSERT INTO billing_periods(id,organization_id,plan_id,plan_version,starts_at,ends_at,reply_limit,training_limit) VALUES(:id,:org,'starter','2026-10-16',now(),now()+interval '30 days',1,1000000)"
            ),
            {"id": period, "org": org},
        )
        db.commit()
    try:
        with create_chat_db_session() as db:
            db.add(
                EmbeddingConfigurations(
                    id=embedding_id,
                    bot_id=bot,
                    provider="openai",
                    model="text-embedding-3-small",
                    version="1",
                    dimension=1536,
                    min_chunk_tokens=100,
                    target_chunk_tokens=400,
                    max_chunk_tokens=700,
                    state="active",
                )
            )
            db.add(
                BotConfigurations(
                    id=config_id,
                    bot_id=bot,
                    embedding_configuration_id=embedding_id,
                    provider="openai",
                    model=STANDARD_MODEL,
                    version="1",
                    retrieval_k=3,
                    similarity_threshold=0.7,
                    state="active",
                    created_by_user_id=user_id,
                )
            )
            db.commit()
        async with httpx.AsyncClient(timeout=60) as client:
            minted = await client.post(
                dashboard_url + "/api/auth/user/token", json={"api_key": api_key}
            )
            minted.raise_for_status()
            auth = minted.json()
            conversation = auth["conversation_id"]
            conversations.append(conversation)
            async with websockets.connect(websocket_url) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "token": auth["token"],
                            "conversation_id": conversation,
                            "visitor_id": str(uuid4()),
                        }
                    )
                )

                async def wait_for(predicate, seconds=100):
                    async with asyncio.timeout(seconds):
                        while True:
                            event = json.loads(await ws.recv())
                            if event.get("type") == "error":
                                raise AssertionError(event.get("message"))
                            if predicate(event):
                                return event

                await wait_for(lambda e: e.get("type") == "conversation_ready")
                profile = {
                    "name": "Billing Test Visitor",
                    "email": "visitor@example.invalid",
                    "phone": "9999999999",
                    "consentToContact": True,
                }
                await ws.send(json.dumps({"type": "form_capture", "content": profile}))
                await wait_for(lambda e: e.get("type") == "form_capture")
                await ws.send(
                    json.dumps(
                        {
                            "type": "message",
                            "message": "Hello. Please greet me briefly.",
                            "request_id": "bounded-real-turn",
                        }
                    )
                )
                answer = await wait_for(
                    lambda e: (
                        e.get("type") in {"ai_unavailable", "message"}
                        and (e.get("role") == "ai" or e.get("type") == "ai_unavailable")
                    )
                )
                assert answer.get("role") == "ai", answer
                with create_dashboard_db_session() as db:
                    usage = (
                        db.execute(
                            text(
                                "SELECT status,cost,response FROM billing_usage WHERE organization_id=:org AND kind='reply' ORDER BY created_at DESC LIMIT 1"
                            ),
                            {"org": org},
                        )
                        .mappings()
                        .one()
                    )
                    assert usage["status"] == "completed", usage["status"]
                    assert usage["cost"]["model_calls"] > 0
                    assert usage["cost"]["usage"]
                    assert usage["cost"]["estimated_cost_usd"] is not None
                    assert (
                        db.scalar(
                            text(
                                "SELECT replies_used FROM billing_periods WHERE id=:id"
                            ),
                            {"id": period},
                        )
                        == 1
                    )
                await ws.send(
                    json.dumps(
                        {
                            "type": "message",
                            "message": "Another greeting.",
                            "request_id": "quota-denied-turn",
                        }
                    )
                )
                denied = await wait_for(lambda e: e.get("type") == "ai_unavailable")
                assert denied["code"] == "ai_limit_reached", denied
                await ws.send(
                    json.dumps(
                        {
                            "type": "form_capture",
                            "fallback": "contact",
                            "content": profile,
                        }
                    )
                )
                saved = await wait_for(lambda e: e.get("type") == "form_capture")
                assert saved["fallback"] == "contact"
                await ws.send(json.dumps({"type": "request_counsellor"}))
                handoff = await wait_for(
                    lambda e: (
                        e.get("type") == "assistance"
                        or (e.get("type") == "message" and e.get("role") == "system")
                    )
                )
                assert handoff
                with create_dashboard_db_session() as db:
                    assert (
                        db.scalar(
                            text(
                                "SELECT replies_used FROM billing_periods WHERE id=:id"
                            ),
                            {"id": period},
                        )
                        == 1
                    )
                    assert (
                        db.scalar(
                            text(
                                "SELECT count(*) FROM leads WHERE organization_id=:org"
                            ),
                            {"org": org},
                        )
                        == 1
                    )
            exported = await client.get(
                dashboard_url + "/api/billing/export",
                cookies={
                    "auth_token": fixture["authToken"],
                    "current_organization_id": org,
                },
            )
            exported.raise_for_status()
            body = exported.json()
            assert body["organization"]["id"] == org
            assert body["chat"]["messages"]
            result = {
                "passed": [
                    "Real OpenAI answer consumes exactly one reply and records token/cost usage",
                    "Exhausted AI quota returns contact fallback without another model call",
                    "Contact form still saves a lead at zero AI balance",
                    "Counsellor handoff works at zero AI balance",
                    "Administrator export returns dashboard records and chat messages together",
                ]
            }
            output = Path(
                os.getenv(
                    "BILLING_TEST_OUTPUT", ".billing-test/billing-flow-results.json"
                )
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
    finally:
        with create_chat_db_session() as db:
            ids = [UUID(value) for value in conversations]
            if ids:
                db.execute(
                    delete(RetrievalLogs).where(RetrievalLogs.conversation_id.in_(ids))
                )
                db.execute(delete(Messages).where(Messages.conversation_id.in_(ids)))
                for table in ["checkpoint_writes", "checkpoint_blobs", "checkpoints"]:
                    db.execute(
                        text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"),
                        {"ids": conversations},
                    )
            db.execute(
                delete(BotConfigurations).where(BotConfigurations.id == config_id)
            )
            db.execute(
                delete(EmbeddingConfigurations).where(
                    EmbeddingConfigurations.id == embedding_id
                )
            )
            db.commit()
        with create_dashboard_db_session() as db:
            db.execute(text("DELETE FROM organizations WHERE id=:org"), {"org": org})
            db.commit()


if __name__ == "__main__":
    asyncio.run(main())
