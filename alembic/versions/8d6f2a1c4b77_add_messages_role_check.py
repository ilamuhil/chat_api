"""add messages role check

Revision ID: 8d6f2a1c4b77
Revises: 0201b6e6ed19
Create Date: 2026-08-05 20:26:00

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8d6f2a1c4b77"
down_revision: Union[str, None] = "0201b6e6ed19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "messages_role_valid",
        "messages",
        "role IN ('user', 'ai', 'support_agent', 'system')",
    )


def downgrade() -> None:
    op.drop_constraint("messages_role_valid", "messages", type_="check")
