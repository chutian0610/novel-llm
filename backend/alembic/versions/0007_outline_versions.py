"""Outline publication (ADR-0004): one immutable version per publication,
containing work, volume, chapter, scene, foreshadowing, and constraint plans
as typed JSON (payload schema versioned in code). Rows are append-only:
changes require a new version, and the (project_id, version_number) pair is
unique — a publication is addressable forever at its version number.

Revision ID: 0007_outline_versions
Revises: 0006_story_structure
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_outline_versions"
down_revision: str | None = "0006_story_structure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outlines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False),
        sa.Column("plans", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "project_id", "version_number", name="uq_outlines_project_id_version_number"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_outlines_version_number_positive"),
    )
    op.create_index("ix_outlines_project_id", "outlines", ["project_id"])

    op.create_table(
        "outline_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("outline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_outline_idempotency_keys_key"),
    )


def downgrade() -> None:
    op.drop_table("outline_idempotency_keys")
    op.drop_table("outlines")
