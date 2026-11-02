"""memories_embedding_768

Revision ID: 9efa2a6ba9e8
Revises: dc4782c9518a
Create Date: 2026-10-26 16:05:49.601741

"""

from collections.abc import Sequence
from typing import Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9efa2a6ba9e8"
down_revision: str | Sequence[str] | None = "dc4782c9518a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "memories",
        "embedding",
        existing_type=pgvector.sqlalchemy.Vector(dim=1024),
        type_=pgvector.sqlalchemy.Vector(dim=768),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "memories",
        "embedding",
        existing_type=pgvector.sqlalchemy.Vector(dim=768),
        type_=pgvector.sqlalchemy.Vector(dim=1024),
        existing_nullable=True,
    )
