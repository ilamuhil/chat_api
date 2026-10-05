"""Record hybrid retrieval evidence and diagnostics.

Revision ID: 8bf16c07a2de
Revises: 6e40a12bc893
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8bf16c07a2de"
down_revision: str | None = "6e40a12bc893"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retrieval_logs",
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("retrieval_logs", "details")
