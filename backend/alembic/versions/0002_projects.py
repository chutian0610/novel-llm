"""Projects and their idempotency keys (ADR-0002/0009): snake-case plural
tables, UUIDv7 keys, status enum check, no physical foreign keys. Archive is
the only removal path, so there is no delete.

Revision ID: 0002_projects
Revises: 0001_baseline
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_projects"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False, server_default="zh-Hans"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_projects_title_not_blank"),
    )
    op.create_index("ix_projects_status", "projects", ["status"])

    # PostgreSQL enforces idempotency-key uniqueness (spec, Implementation
    # Decisions); the fingerprint detects reuse with a different payload.
    op.create_table(
        "project_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_project_idempotency_keys_key"),
    )
    op.create_index(
        "ix_project_idempotency_keys_project_id",
        "project_idempotency_keys",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_table("project_idempotency_keys")
    op.drop_table("projects")
