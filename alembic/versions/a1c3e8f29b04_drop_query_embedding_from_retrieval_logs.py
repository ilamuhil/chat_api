"""drop query_embedding from retrieval_logs

Revision ID: a1c3e8f29b04
Revises: 8d6f2a1c4b77
Create Date: 2026-08-11

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR


revision: str = "a1c3e8f29b04"
down_revision: Union[str, None] = "8d6f2a1c4b77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("retrieval_logs", "query_embedding")


def downgrade() -> None:
    op.add_column(
        "retrieval_logs",
        sa.Column("query_embedding", VECTOR(1536), nullable=True),
    )
