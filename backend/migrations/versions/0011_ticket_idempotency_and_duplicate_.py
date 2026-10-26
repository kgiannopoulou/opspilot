"""ticket idempotency keys and duplicate detection

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-26 10:02:11.482113
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.add_column("tickets", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.create_index("ix_tickets_reporter_fingerprint", "tickets", ["reporter_id", "request_fingerprint"], unique=False)
    # NULL keys do not collide, so tickets created without a key are unaffected.
    op.create_unique_constraint("uq_tickets_reporter_idempotency_key", "tickets", ["reporter_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_tickets_reporter_idempotency_key", "tickets", type_="unique")
    op.drop_index("ix_tickets_reporter_fingerprint", table_name="tickets")
    op.drop_column("tickets", "request_fingerprint")
    op.drop_column("tickets", "idempotency_key")
