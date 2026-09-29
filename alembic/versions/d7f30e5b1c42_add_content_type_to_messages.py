"""add content type to messages

Revision ID: d7f30e5b1c42
Revises: fc1514028346
Create Date: 2026-08-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f30e5b1c42"
down_revision: str | None = "fc1514028346"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "content_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'text'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "content_type")
