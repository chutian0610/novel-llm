"""The story structure (ADR-0004/0009): Volumes and Chapters inside a
Project — stable identities (UUIDv7) with mutable positions, reordered
without breaking references. References are typed UUID columns with indexes;
there are no physical foreign keys — domain services validate references
inside their transactions. The (volume_id, position) uniqueness is deferred
so one reorder transaction can renumber siblings without transient
conflicts.

Revision ID: 0006_story_structure
Revises: 0005_job_pause
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_story_structure"
down_revision: str | None = "0005_job_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "volumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("position >= 1", name="ck_volumes_position_positive"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_volumes_title_not_blank"),
    )
    # One Project's volumes in display order.
    op.create_index("ix_volumes_project_id_position", "volumes", ["project_id", "position"])

    op.create_table(
        "chapters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("volume_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("position >= 1", name="ck_chapters_position_positive"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_chapters_title_not_blank"),
        # Dense sibling ordering; deferred so a single reorder transaction can
        # renumber without transient conflicts at commit.
        sa.UniqueConstraint(
            "volume_id",
            "position",
            name="uq_chapters_volume_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index("ix_chapters_volume_id_position", "chapters", ["volume_id", "position"])
    op.create_index("ix_chapters_project_id", "chapters", ["project_id"])

    op.create_table(
        "volume_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("volume_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_volume_idempotency_keys_key"),
    )

    op.create_table(
        "chapter_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_chapter_idempotency_keys_key"),
    )


def downgrade() -> None:
    op.drop_table("chapter_idempotency_keys")
    op.drop_table("volume_idempotency_keys")
    op.drop_table("chapters")
    op.drop_table("volumes")
