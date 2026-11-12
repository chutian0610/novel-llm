"""Story Bible drafts and Published Bible Versions (ADR-0004/0009): identity
is split from content. Bible identities carry the stable names and aliases
that persist across versions; draft entries hold the typed content and are
frozen into an append-only published version at each publication —
generation always references a published version, never a draft.

References are typed UUID columns with indexes and no physical foreign keys;
domain services validate them inside their transactions.

Revision ID: 0008_bible_drafts_versions
Revises: 0007_outline_versions
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_bible_drafts_versions"
down_revision: str | None = "0007_outline_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The nine first-version entry types (architecture doc), enforced here.
_ENTRY_TYPES = (
    "character",
    "relationship",
    "location",
    "organization",
    "item",
    "rule",
    "event",
    "style",
    "open_question",
)


def upgrade() -> None:
    op.create_table(
        "bible_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint("btrim(name) <> ''", name="ck_bible_identities_name_not_blank"),
        sa.UniqueConstraint("project_id", "name", name="uq_bible_identities_project_id_name"),
    )

    op.create_table(
        "bible_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint(
            "entry_type IN (" + ", ".join(f"'{entry_type}'" for entry_type in _ENTRY_TYPES) + ")",
            name="ck_bible_entries_entry_type",
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_bible_entries_status"),
    )
    op.create_index("ix_bible_entries_project_id", "bible_entries", ["project_id"])
    op.create_index("ix_bible_entries_identity_id", "bible_entries", ["identity_id"])

    op.create_table(
        "published_bible_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False),
        sa.Column("entries", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "version_number",
            name="uq_published_bible_versions_project_id_version_number",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_published_bible_versions_version_number_positive"
        ),
    )
    op.create_index(
        "ix_published_bible_versions_project_id", "published_bible_versions", ["project_id"]
    )

    for resource in ("identity", "entry"):
        op.create_table(
            f"bible_{resource}_idempotency_keys",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("key", sa.Text(), nullable=False),
            sa.Column("request_fingerprint", sa.Text(), nullable=False),
            sa.Column(f"{resource}_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("key", name=f"uq_bible_{resource}_idempotency_keys_key"),
        )

    op.create_table(
        "bible_publish_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_bible_publish_idempotency_keys_key"),
    )


def downgrade() -> None:
    op.drop_table("bible_publish_idempotency_keys")
    op.drop_table("bible_entry_idempotency_keys")
    op.drop_table("bible_identity_idempotency_keys")
    op.drop_table("published_bible_versions")
    op.drop_table("bible_entries")
    op.drop_table("bible_identities")
