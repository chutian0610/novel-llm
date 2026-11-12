"""Model execution foundation (ADR-0006/0008): provider credentials and
Model Profiles as separate database objects — plaintext local credential
rows, never echoed back complete by the API — and the full model-call traces
(prompt, completion, metadata, latency, token use, cost, repair linkage).
Seeds carry the default mock credential, the default mock profile, and the
model-probe Job Type only: default configuration, never sample data
(ADR-0002).

Revision ID: 0009_model_execution
Revises: 0008_bible_drafts_versions
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_model_execution"
down_revision: str | None = "0008_bible_drafts_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed literals so every fresh install seeds the identical default
# configuration rows.
_MOCK_CREDENTIAL_ID = "01900000-0000-7000-8000-00000000c001"
_MOCK_PROFILE_ID = "01900000-0000-7000-8000-00000000c002"
_MODEL_PROBE_JOB_TYPE_ID = "01900000-0000-7000-8000-00000000c003"


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        # Plaintext local rows by decision (ADR-0006); the API masks it.
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("base_url", sa.Text()),
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
        sa.CheckConstraint("btrim(name) <> ''", name="ck_provider_credentials_name_not_blank"),
        sa.CheckConstraint(
            "btrim(provider) <> ''", name="ck_provider_credentials_provider_not_blank"
        ),
        sa.UniqueConstraint("name", name="uq_provider_credentials_name"),
    )

    op.create_table(
        "model_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        # Typed reference, validated in the domain transaction (ADR-0009).
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("temperature", sa.Numeric(4, 3), nullable=False, server_default="0.7"),
        sa.Column("top_p", sa.Numeric(4, 3), nullable=False, server_default="1"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_token_cost", sa.Numeric(12, 9), nullable=False, server_default="0"),
        sa.Column("output_token_cost", sa.Numeric(12, 9), nullable=False, server_default="0"),
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
        sa.CheckConstraint("btrim(name) <> ''", name="ck_model_profiles_name_not_blank"),
        sa.CheckConstraint("btrim(model) <> ''", name="ck_model_profiles_model_not_blank"),
        sa.CheckConstraint(
            "temperature >= 0 AND temperature <= 2", name="ck_model_profiles_temperature_range"
        ),
        sa.CheckConstraint("top_p > 0 AND top_p <= 1", name="ck_model_profiles_top_p_range"),
        sa.CheckConstraint("max_tokens > 0", name="ck_model_profiles_max_tokens_positive"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_model_profiles_timeout_positive"),
        sa.CheckConstraint("max_retries >= 0", name="ck_model_profiles_max_retries_non_negative"),
        sa.UniqueConstraint("name", name="uq_model_profiles_name"),
    )
    op.create_index("ix_model_profiles_credential_id", "model_profiles", ["credential_id"])

    op.create_table(
        "model_credential_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_model_credential_idempotency_keys_key"),
    )

    op.create_table(
        "model_profile_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_model_profile_idempotency_keys_key"),
    )

    op.create_table(
        "model_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("output_kind", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("completion", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Numeric(14, 9), nullable=False),
        # The trace of the repair call links back to the trace it repaired.
        sa.Column("repair_of_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "output_kind IN ('write', 'evaluate', 'diagnose', 'extract', 'project')",
            name="ck_model_traces_output_kind",
        ),
        sa.CheckConstraint("input_tokens >= 0", name="ck_model_traces_input_tokens_non_negative"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_model_traces_output_tokens_non_negative"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_model_traces_latency_non_negative"),
        sa.CheckConstraint("cost >= 0", name="ck_model_traces_cost_non_negative"),
        sa.CheckConstraint(
            "btrim(template_key) <> ''", name="ck_model_traces_template_key_not_blank"
        ),
    )
    op.create_index("ix_model_traces_project_id", "model_traces", ["project_id"])
    op.create_index("ix_model_traces_repair_of_id", "model_traces", ["repair_of_id"])

    op.execute(
        f"""
        INSERT INTO provider_credentials (id, name, provider, api_key)
        VALUES ('{_MOCK_CREDENTIAL_ID}', '本地 Mock 凭据', 'mock', '')
        """
    )
    op.execute(
        f"""
        INSERT INTO model_profiles (
            id, name, model, credential_id, temperature, top_p, max_tokens,
            timeout_seconds, max_retries, input_token_cost, output_token_cost
        ) VALUES (
            '{_MOCK_PROFILE_ID}', '本地 Mock 写手', 'fake/writer-v1',
            '{_MOCK_CREDENTIAL_ID}', 0.7, 1, 1024, 30, 0, 0, 0
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO job_types (
            id, job_type, timeout_seconds, heartbeat_seconds, lease_seconds,
            max_attempts, backoff_seconds, backoff_multiplier, backoff_max_seconds
        ) VALUES (
            '{_MODEL_PROBE_JOB_TYPE_ID}', 'model_probe_work', 60, 2, 6, 1, 1, 2.0, 30
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM job_types WHERE job_type = 'model_probe_work'")
    op.execute("DELETE FROM model_profiles WHERE id = '" + _MOCK_PROFILE_ID + "'")
    op.execute("DELETE FROM provider_credentials WHERE id = '" + _MOCK_CREDENTIAL_ID + "'")
    op.drop_table("model_traces")
    op.drop_table("model_profile_idempotency_keys")
    op.drop_table("model_credential_idempotency_keys")
    op.drop_table("model_profiles")
    op.drop_table("provider_credentials")
