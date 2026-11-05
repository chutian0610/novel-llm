"""The Job and Workflow Run runtime (ADR-0003): workflow runs bound to
code-owned definitions, transactionally claimed jobs with leases, heartbeats,
fencing tokens, attempts, steps (stable idempotency keys), and checkpoints.
Per-Job-Type timeout/heartbeat/retry/backoff settings live in job_types —
seeded here with the default configuration only, never sample data.

Revision ID: 0004_job_runtime
Revises: 0003_domain_events
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_job_runtime"
down_revision: str | None = "0003_domain_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed literal so every fresh install seeds the identical default row.
_TRIVIAL_JOB_TYPE_ID = "01900000-0000-7000-8000-000000000001"


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_key", sa.Text(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
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
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_runs_status",
        ),
        sa.CheckConstraint(
            "btrim(definition_key) <> ''", name="ck_workflow_runs_definition_key_not_blank"
        ),
    )
    # Keyset pagination on (project_id, id); UUIDv7 ids are time-ordered.
    op.create_index(
        "ix_workflow_runs_project_id_id",
        "workflow_runs",
        ["project_id", sa.text("id ASC")],
    )

    op.create_table(
        "workflow_run_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_workflow_run_idempotency_keys_key"),
    )
    op.create_index(
        "ix_workflow_run_idempotency_keys_run_id",
        "workflow_run_idempotency_keys",
        ["run_id"],
    )

    op.create_table(
        "job_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("heartbeat_seconds", sa.Integer(), nullable=False),
        sa.Column("lease_seconds", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("backoff_seconds", sa.Integer(), nullable=False),
        sa.Column("backoff_multiplier", sa.Numeric(), nullable=False),
        sa.Column("backoff_max_seconds", sa.Integer(), nullable=False),
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
        sa.UniqueConstraint("job_type", name="uq_job_types_job_type"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_job_types_timeout_positive"),
        sa.CheckConstraint("heartbeat_seconds > 0", name="ck_job_types_heartbeat_positive"),
        sa.CheckConstraint("lease_seconds > 0", name="ck_job_types_lease_positive"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_job_types_max_attempts_at_least_one"),
        sa.CheckConstraint("backoff_seconds >= 0", name="ck_job_types_backoff_non_negative"),
        sa.CheckConstraint(
            "backoff_multiplier >= 1.0", name="ck_job_types_backoff_multiplier_at_least_one"
        ),
        sa.CheckConstraint("backoff_max_seconds > 0", name="ck_job_types_backoff_max_positive"),
        sa.CheckConstraint("btrim(job_type) <> ''", name="ck_job_types_job_type_not_blank"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="ready"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_class", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ("
            "'ready', 'leased', 'running', 'pause_requested', 'paused', "
            "'cancel_requested', 'cancelling', 'interrupted', "
            "'succeeded', 'failed', 'cancelled', 'skipped')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "failure_class IS NULL OR failure_class IN ("
            "'transient', 'permanent', 'cancelled', 'timeout', "
            "'quota_exhausted', 'upstream_invalid')",
            name="ck_jobs_failure_class",
        ),
        sa.CheckConstraint("btrim(job_type) <> ''", name="ck_jobs_job_type_not_blank"),
    )
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_workflow_run_id", "jobs", ["workflow_run_id"])
    # The claim scan: ready jobs due for work, oldest first.
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"])

    op.create_table(
        "job_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("failure_class", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "attempt", name="uq_job_attempts_job_id_attempt"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ("
            "'succeeded', 'failed', 'cancelled', 'abandoned', 'interrupted')",
            name="ck_job_attempts_outcome",
        ),
        sa.CheckConstraint(
            "failure_class IS NULL OR failure_class IN ("
            "'transient', 'permanent', 'cancelled', 'timeout', "
            "'quota_exhausted', 'upstream_invalid')",
            name="ck_job_attempts_failure_class",
        ),
    )

    op.create_table(
        "job_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="started"),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("job_id", "step_key", name="uq_job_steps_job_id_step_key"),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed')", name="ck_job_steps_status"
        ),
        sa.CheckConstraint("btrim(step_key) <> ''", name="ck_job_steps_step_key_not_blank"),
    )

    op.create_table(
        "job_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("checkpoint_key", sa.Text(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "btrim(checkpoint_key) <> ''", name="ck_job_checkpoints_checkpoint_key_not_blank"
        ),
    )
    op.create_index(
        "ix_job_checkpoints_job_id_id",
        "job_checkpoints",
        ["job_id", sa.text("id ASC")],
    )

    # Seed: default job configuration only (ADR-0002/0003). The trivial job
    # type's lease comfortably covers a heartbeat interval; retry/backoff are
    # database-owned so they can be tuned without a deploy.
    op.execute(
        f"""
        INSERT INTO job_types (
            id, job_type, timeout_seconds, heartbeat_seconds, lease_seconds,
            max_attempts, backoff_seconds, backoff_multiplier, backoff_max_seconds
        ) VALUES (
            '{_TRIVIAL_JOB_TYPE_ID}', 'trivial_work', 120, 2, 6, 3, 1, 2.0, 30
        )
        ON CONFLICT (job_type) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("job_checkpoints")
    op.drop_table("job_steps")
    op.drop_table("job_attempts")
    op.drop_table("jobs")
    op.drop_table("job_types")
    op.drop_table("workflow_run_idempotency_keys")
    op.drop_table("workflow_runs")
