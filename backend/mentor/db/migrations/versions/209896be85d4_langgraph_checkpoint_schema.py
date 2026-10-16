"""langgraph checkpoint schema

Revision ID: 209896be85d4
Revises: a8d102f8525c
Create Date: 2026-10-16 12:23:27.690862

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "209896be85d4"
down_revision: str | Sequence[str] | None = "a8d102f8525c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # LangGraph creates its checkpoint tables in here on first run (PostgresSaver.setup).
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
