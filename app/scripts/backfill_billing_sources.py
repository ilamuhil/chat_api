"""Seed corpus accounting for already-trained sources, without charging past usage."""

from collections import defaultdict

from sqlalchemy import select, text

from app.db.session import create_chat_db_session, create_dashboard_db_session
from app.models.chat_db_models import Documents
from app.services.billing import billing_tokens


def main():
    totals = defaultdict(int)
    with create_chat_db_session() as chat:
        rows = chat.execute(
            select(Documents.source_id, Documents.organization_id, Documents.content)
            .where(Documents.is_active.is_(True), Documents.deleted_at.is_(None))
            .execution_options(yield_per=100)
        )
        for source_id, org_id, content in rows:
            totals[(org_id, source_id)] += billing_tokens(content or "")
    seeded = 0
    with create_dashboard_db_session() as db:
        for (org_id, source_id), tokens in totals.items():
            db.execute(
                text(
                    "SELECT organization_id FROM billing_accounts WHERE organization_id=:org FOR UPDATE"
                ),
                {"org": org_id},
            )
            result = db.execute(
                text("""INSERT INTO billing_source_usage(source_id,organization_id,tokens,pending_tokens)
             SELECT :source,:org,:tokens,0 WHERE EXISTS(SELECT 1 FROM training_sources WHERE id=:source AND organization_id=:org AND deleted_at IS NULL)
             AND EXISTS(SELECT 1 FROM billing_accounts WHERE organization_id=:org)
             ON CONFLICT(source_id) DO NOTHING"""),
                {"source": source_id, "org": org_id, "tokens": tokens},
            )
            seeded += result.rowcount
        db.commit()
    print(
        {
            "sources_seeded": seeded,
            "sources_inspected": len(totals),
            "past_usage_charged": False,
        }
    )


if __name__ == "__main__":
    main()
