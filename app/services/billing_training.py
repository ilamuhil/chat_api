"""Reserve extraction/embedding capacity before replacing a source's vectors."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import text

from app.ingestion.errors import TrainingFailure
from app.services.billing import (
    TOKENIZER_VERSION,
    access_allowed,
    billing_tokens,
    priced_usage,
)


def reserve_training(db, source, config, documents):
    tokens = sum(billing_tokens(document.content or "") for document in documents)
    content_hash = hashlib.sha256(
        (
            str(config.id)
            + "\n"
            + "\n".join(document.content or "" for document in documents)
        ).encode()
    ).hexdigest()
    key = f"training:{source.id}:{config.id}:{content_hash}"
    account = (
        db.execute(
            text(
                "SELECT * FROM billing_accounts WHERE organization_id=:org FOR UPDATE"
            ),
            {"org": source.organization_id},
        )
        .mappings()
        .first()
    )
    if not account or not access_allowed(dict(account)):
        db.rollback()
        raise TrainingFailure(
            "plan_expired",
            "Choose an active plan before training new content.",
            retryable=False,
        )
    existing = (
        db.execute(text("SELECT * FROM billing_usage WHERE id=:id"), {"id": key})
        .mappings()
        .first()
    )
    current = (
        db.execute(
            text("SELECT * FROM billing_source_usage WHERE source_id=:source"),
            {"source": source.id},
        )
        .mappings()
        .first()
    )
    if (
        current
        and current["content_hash"] == content_hash
        and current["pending_tokens"] == 0
    ):
        db.commit()
        return key, True
    if existing and existing["status"] == "completed":
        key += f":{uuid4()}"
    pending = db.execute(
        text(
            "SELECT count(*),coalesce(bool_or(cost->>'source_id'=:source),false) FROM billing_usage WHERE organization_id=:org AND kind='training' AND status='reserved'"
        ),
        {"org": source.organization_id, "source": str(source.id)},
    ).one()
    if pending[1] or pending[0] >= 2:
        db.rollback()
        raise TrainingFailure(
            "training_in_progress",
            "Training is already in progress. Please wait for it to finish.",
            retryable=True,
        )
    period = (
        db.execute(
            text("SELECT * FROM billing_periods WHERE id=:id FOR UPDATE"),
            {"id": account["current_period_id"]},
        )
        .mappings()
        .one()
    )
    limits = dict(account["limits"])
    pending_limits = {
        "starter": {"corpusTokens": 500000},
        "growth": {"corpusTokens": 2000000},
    }
    if account.get("pending_plan_id") in pending_limits:
        limits["corpusTokens"] = min(
            limits["corpusTokens"],
            pending_limits[account["pending_plan_id"]]["corpusTokens"],
        )
    corpus = db.execute(
        text(
            "SELECT coalesce(sum(b.tokens+b.pending_tokens),0) FROM billing_source_usage b JOIN training_sources s ON s.id=b.source_id WHERE b.organization_id=:org AND s.deleted_at IS NULL"
        ),
        {"org": source.organization_id},
    ).scalar_one()
    if corpus - (current["tokens"] if current else 0) + tokens > limits["corpusTokens"]:
        db.rollback()
        raise TrainingFailure(
            "training_corpus_limit",
            "The training content limit has been reached. Remove a source or change your plan.",
            retryable=False,
        )
    if (
        period["training_used"] + period["training_reserved"] + tokens
        > period["training_limit"]
    ):
        db.rollback()
        raise TrainingFailure(
            "training_cycle_limit",
            "This billing cycle’s training allowance is used. Existing trained content is still available.",
            retryable=False,
        )
    db.execute(
        text(
            "INSERT INTO billing_source_usage (source_id,organization_id,tokens,pending_tokens) VALUES (:source,:org,0,:tokens) ON CONFLICT (source_id) DO UPDATE SET pending_tokens=:tokens"
        ),
        {"source": source.id, "org": source.organization_id, "tokens": tokens},
    )
    db.execute(
        text(
            "UPDATE billing_periods SET training_reserved=training_reserved+:tokens WHERE id=:id"
        ),
        {"id": period["id"], "tokens": tokens},
    )
    db.execute(
        text("""INSERT INTO billing_usage (id,organization_id,period_id,kind,status,amount,cost) VALUES (:id,:org,:period,'training','reserved',:tokens,CAST(:cost AS jsonb))
      ON CONFLICT (id) DO UPDATE SET status='reserved',period_id=EXCLUDED.period_id,created_at=now(),completed_at=NULL"""),
        {
            "id": key,
            "org": source.organization_id,
            "period": period["id"],
            "tokens": tokens,
            "cost": json.dumps(
                {
                    "embedding_provider": config.provider,
                    "embedding_model": config.model,
                    "embedding_input_tokens": tokens,
                    "tokenizer_version": TOKENIZER_VERSION,
                    **priced_usage(
                        [
                            {
                                "model": config.model,
                                "input_tokens": tokens,
                                "output_tokens": 0,
                            }
                        ]
                    ),
                    "source_id": str(source.id),
                    "content_hash": content_hash,
                }
            ),
        },
    )
    db.commit()
    return key, False


def finish_training(db, key, success):
    row = (
        db.execute(
            text("SELECT organization_id FROM billing_usage WHERE id=:id"), {"id": key}
        )
        .mappings()
        .first()
    )
    if not row:
        return
    db.execute(
        text(
            "SELECT organization_id FROM billing_accounts WHERE organization_id=:org FOR UPDATE"
        ),
        {"org": row["organization_id"]},
    )
    usage = (
        db.execute(
            text("SELECT * FROM billing_usage WHERE id=:id FOR UPDATE"), {"id": key}
        )
        .mappings()
        .one()
    )
    if usage["status"] != "reserved":
        db.commit()
        return
    source = usage["cost"]["source_id"]
    db.execute(
        text(
            "UPDATE billing_periods SET training_reserved=training_reserved-:tokens,training_used=training_used+:charged WHERE id=:id"
        ),
        {
            "tokens": usage["amount"],
            "charged": usage["amount"] if success else 0,
            "id": usage["period_id"],
        },
    )
    db.execute(
        text(
            "UPDATE billing_source_usage SET pending_tokens=0"
            + (",tokens=:tokens,content_hash=:hash" if success else "")
            + " WHERE source_id=CAST(:source AS uuid)"
        ),
        {
            "source": source,
            "tokens": usage["amount"],
            "hash": usage["cost"]["content_hash"],
        },
    )
    db.execute(
        text("UPDATE billing_usage SET status=:status,completed_at=now() WHERE id=:id"),
        {"id": key, "status": "completed" if success else "released"},
    )
    db.commit()
