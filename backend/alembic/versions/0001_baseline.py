"""Baseline: anchors the schema history (ADR-0002). Alembic is the only
schema-change mechanism; domain tables arrive with their Phase 1 tickets.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-15
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Intentionally empty: the baseline exists so every later migration has a
    # root and the migration state is observable from day one.
    pass


def downgrade() -> None:
    pass
