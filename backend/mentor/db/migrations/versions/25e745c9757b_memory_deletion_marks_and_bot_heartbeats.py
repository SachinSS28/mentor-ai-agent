"""memory deletion marks and bot heartbeats

Revision ID: 25e745c9757b
Revises: 209896be85d4
Create Date: 2026-10-17 20:41:59.859549

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "25e745c9757b"
down_revision: str | Sequence[str] | None = "209896be85d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "heartbeats",
        sa.Column("host", sa.String(length=8), nullable=False),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("host IN ('mac', 'cloud')", name=op.f("ck_heartbeats_host")),
        sa.PrimaryKeyConstraint("host", name=op.f("pk_heartbeats")),
    )
    op.add_column("memories", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("memories", "deleted_at")
    op.drop_table("heartbeats")
