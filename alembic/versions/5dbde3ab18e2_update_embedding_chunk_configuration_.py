"""update embedding chunk configuration fields

Revision ID: 5dbde3ab18e2
Revises: d8e5f2a1c7b4
Create Date: 2026-10-03 22:48:40.265436

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5dbde3ab18e2"
down_revision: str | None = "d8e5f2a1c7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "embedding_configurations",
        sa.Column("min_chunk_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "embedding_configurations",
        sa.Column("target_chunk_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "embedding_configurations",
        sa.Column("max_chunk_tokens", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE embedding_configurations
            SET min_chunk_tokens = GREATEST(1, chunk_size - chunk_overlap),
                target_chunk_tokens = chunk_size,
                max_chunk_tokens = chunk_size + chunk_overlap
            """
        )
    )
    op.alter_column("embedding_configurations", "min_chunk_tokens", nullable=False)
    op.alter_column("embedding_configurations", "target_chunk_tokens", nullable=False)
    op.alter_column("embedding_configurations", "max_chunk_tokens", nullable=False)
    op.drop_constraint(
        "embedding_config_chunk_overlap_range",
        "embedding_configurations",
        type_="check",
    )
    op.drop_constraint(
        "embedding_config_chunk_size_positive",
        "embedding_configurations",
        type_="check",
    )
    op.create_check_constraint(
        "embedding_config_min_chunk_tokens_positive",
        "embedding_configurations",
        "min_chunk_tokens > 0",
    )
    op.create_check_constraint(
        "embedding_config_target_chunk_tokens_valid",
        "embedding_configurations",
        "target_chunk_tokens >= min_chunk_tokens",
    )
    op.create_check_constraint(
        "embedding_config_max_chunk_tokens_valid",
        "embedding_configurations",
        "max_chunk_tokens >= target_chunk_tokens",
    )
    op.drop_column("embedding_configurations", "chunk_size")
    op.drop_column("embedding_configurations", "chunk_overlap")


def downgrade() -> None:
    op.add_column(
        "embedding_configurations",
        sa.Column("chunk_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "embedding_configurations",
        sa.Column("chunk_overlap", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE embedding_configurations
            SET chunk_size = target_chunk_tokens,
                chunk_overlap = target_chunk_tokens - min_chunk_tokens
            """
        )
    )
    op.alter_column("embedding_configurations", "chunk_size", nullable=False)
    op.alter_column("embedding_configurations", "chunk_overlap", nullable=False)
    op.drop_constraint(
        "embedding_config_max_chunk_tokens_valid",
        "embedding_configurations",
        type_="check",
    )
    op.drop_constraint(
        "embedding_config_target_chunk_tokens_valid",
        "embedding_configurations",
        type_="check",
    )
    op.drop_constraint(
        "embedding_config_min_chunk_tokens_positive",
        "embedding_configurations",
        type_="check",
    )
    op.create_check_constraint(
        "embedding_config_chunk_size_positive",
        "embedding_configurations",
        "chunk_size > 0",
    )
    op.create_check_constraint(
        "embedding_config_chunk_overlap_range",
        "embedding_configurations",
        "chunk_overlap >= 0 and chunk_overlap < chunk_size",
    )
    op.drop_column("embedding_configurations", "max_chunk_tokens")
    op.drop_column("embedding_configurations", "target_chunk_tokens")
    op.drop_column("embedding_configurations", "min_chunk_tokens")
