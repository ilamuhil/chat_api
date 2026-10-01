"""add generated full-text search vector to documents

Revision ID: c7f4e1a2b9d6
Revises: 5237389a3f48
Create Date: 2026-10-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "c7f4e1a2b9d6"
down_revision: str | None = "5237389a3f48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed(
                """
                setweight(
                    to_tsvector('english', coalesce(section_title, '')),
                    'A'
                )
                ||
                setweight(
                    to_tsvector(
                        'english',
                        coalesce(
                            metadata_json -> 'structure' ->> 'heading_paths',
                            ''
                        )
                    ),
                    'B'
                )
                ||
                setweight(
                    to_tsvector('english', coalesce(content, '')),
                    'D'
                )
                """,
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_documents_search_vector",
        "documents",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_documents_search_vector", table_name="documents")
    op.drop_column("documents", "search_vector")
