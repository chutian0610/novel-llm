"""Cooperative pause as a domain action (ADR-0003): a paused Job's attempt
ends with the paused outcome — the attempt is closed, its lease gone, and a
resume requeues the Job for a fresh attempt under a new fencing token. The
attempt-outcome vocabulary is a database-enforced closed set, so extending it
with 'paused' is a migration.

Revision ID: 0005_job_pause
Revises: 0004_job_runtime
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_job_pause"
down_revision: str | None = "0004_job_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_job_attempts_outcome", "job_attempts", type_="check")
    op.create_check_constraint(
        "ck_job_attempts_outcome",
        "job_attempts",
        "outcome IS NULL OR outcome IN ("
        "'succeeded', 'failed', 'cancelled', 'abandoned', 'interrupted', 'paused')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_attempts_outcome", "job_attempts", type_="check")
    op.create_check_constraint(
        "ck_job_attempts_outcome",
        "job_attempts",
        "outcome IS NULL OR outcome IN ("
        "'succeeded', 'failed', 'cancelled', 'abandoned', 'interrupted')",
    )
