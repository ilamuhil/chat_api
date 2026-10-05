"""Store training error histories and remove failed configuration states.

Revision ID: 6e40a12bc893
Revises: 5dbde3ab18e2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6e40a12bc893"
down_revision: str | None = "5dbde3ab18e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This migration touches only the Chat DB. Dashboard DB changes use Prisma.
    op.alter_column(
        "training_jobs",
        "error_message",
        existing_type=sa.Text(),
        type_=postgresql.JSONB(),
        postgresql_using="""CASE
        WHEN error_message IS NULL OR btrim(error_message) = '' THEN '[]'::jsonb
        ELSE jsonb_build_array(jsonb_build_object(
            'id', gen_random_uuid()::text, 'code', 'legacy_training_error',
            'stage', 'job', 'message', error_message,
            'action', 'Retry the failed sources.', 'retryable', true,
            'occurred_at', coalesce(completed_at, started_at, now()),
            'job_id', id::text, 'source_id', NULL, 'resolved_at', NULL
        )) END""",
    )
    op.alter_column(
        "training_jobs",
        "error_message",
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    op.add_column(
        "training_jobs",
        sa.Column(
            "source_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    for table, constraint in (
        ("embedding_configurations", "embedding_config_state_valid"),
        ("bot_configurations", "bot_config_state_valid"),
    ):
        op.execute(
            sa.text(f"UPDATE {table} SET state = 'draft' WHERE state = 'failed'")
        )
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(
            constraint, table, "state IN ('draft', 'training', 'active', 'deprecated')"
        )


def downgrade() -> None:
    for table, constraint in (
        ("embedding_configurations", "embedding_config_state_valid"),
        ("bot_configurations", "bot_config_state_valid"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(
            constraint,
            table,
            "state IN ('draft', 'training', 'active', 'failed', 'deprecated')",
        )
    op.drop_column("training_jobs", "source_ids")
    op.alter_column(
        "training_jobs", "error_message", server_default=None, nullable=True
    )
    # Serialize the complete history so downgrade does not discard any errors.
    op.alter_column(
        "training_jobs",
        "error_message",
        existing_type=postgresql.JSONB(),
        type_=sa.Text(),
        postgresql_using="CASE WHEN error_message = '[]'::jsonb THEN NULL ELSE error_message::text END",
    )
