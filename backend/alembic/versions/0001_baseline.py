"""Baseline: anchors the schema history (ADR-0002) and proves the custom
PostgreSQL image carries its extensions (ADR-0007). Alembic is the only
schema-change mechanism; domain tables arrive with their Phase 1 tickets.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-15
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Loading the extensions on every fresh install (and every test migration
    # run) is what verifies the custom image actually carries them.
    op.execute("CREATE EXTENSION IF NOT EXISTS zhparser")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS zhparser")
