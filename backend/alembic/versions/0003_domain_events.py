"""The append-only event outbox (ADR-0002/0003): domain events commit in the
same transaction as their domain change, ordered by time-ordered UUIDv7 ids,
scoped per Project. There is no update or delete path — append-only.

Revision ID: 0003_domain_events
Revises: 0002_projects
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_domain_events"
down_revision: str | None = "0002_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("btrim(event_type) <> ''", name="ck_domain_events_event_type"),
    )
    # The delivery order is id order (UUIDv7 is time-ordered); the composite
    # index serves the per-project keyset replay scan.
    op.create_index(
        "ix_domain_events_project_id_id",
        "domain_events",
        ["project_id", sa.text("id ASC")],
    )


def downgrade() -> None:
    op.drop_table("domain_events")
