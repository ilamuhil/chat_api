"""Record per-turn response latency and conversation aggregates.

Revision ID: 93c2a4f6e801
Revises: 8bf16c07a2de
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "93c2a4f6e801"
down_revision = "8bf16c07a2de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_latency",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("bot_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "request_message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
        ),
        sa.Column("model", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("response_ms", sa.Double()),
        sa.Column("elapsed_ms", sa.Double(), nullable=False),
        sa.Column("stages_ms", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "response_ms IS NULL OR response_ms >= 0",
            name="response_latency_response_valid",
        ),
        sa.CheckConstraint("elapsed_ms >= 0", name="response_latency_elapsed_valid"),
        sa.CheckConstraint(
            "outcome IN ('success', 'fallback', 'error', 'timeout', 'rejected', 'cached', 'cancelled', 'disconnected')",
            name="response_latency_outcome_valid",
        ),
    )
    op.create_index(
        "response_latency_conversation_idx",
        "response_latency",
        ["organization_id", "conversation_id", "created_at"],
    )
    op.execute("""
        CREATE VIEW conversation_latency_stats AS
        SELECT organization_id, bot_id, conversation_id,
            count(*) AS attempt_count,
            count(*) FILTER (WHERE outcome = 'success' AND response_ms IS NOT NULL) AS reply_count,
            avg(response_ms) FILTER (WHERE outcome = 'success') AS average_response_ms,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY response_ms)
                FILTER (WHERE outcome = 'success') AS p50_response_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY response_ms)
                FILTER (WHERE outcome = 'success') AS p95_response_ms,
            max(response_ms) FILTER (WHERE outcome = 'success') AS max_response_ms,
            count(*) FILTER (WHERE outcome IN ('error', 'timeout', 'disconnected')) AS failure_count,
            count(*) FILTER (WHERE outcome = 'fallback') AS fallback_count,
            count(*) FILTER (WHERE outcome = 'cancelled') AS cancelled_count,
            count(*) FILTER (WHERE outcome = 'cached') AS cached_count,
            count(*) FILTER (WHERE outcome = 'rejected') AS rejected_count
        FROM response_latency
        GROUP BY organization_id, bot_id, conversation_id
    """)


def downgrade() -> None:
    op.execute("DROP VIEW conversation_latency_stats")
    op.drop_table("response_latency")
