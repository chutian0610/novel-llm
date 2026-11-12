"""ORM rows for domain tables (ADR-0002: PostgreSQL is authoritative).

Domain tables arrive with their Phase 1 tickets; the metadata carries a naming
convention so constraint names match the hand-written Alembic migrations.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB as PgJSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_naming_convention)


class ProjectRow(Base):
    """A single fictional work: the container for chapters, Story Bible,
    memory, workflow runs, and exports."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="status"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text())
    description: Mapped[str | None] = mapped_column(Text())
    language: Mapped[str] = mapped_column(Text(), server_default="zh-Hans")
    status: Mapped[str] = mapped_column(Text(), server_default="active")
    revision: Mapped[int] = mapped_column(BigInteger(), server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ProjectIdempotencyKeyRow(Base):
    """A create-attempt key for a Project; the unique key is what PostgreSQL
    enforces, the fingerprint detects reuse with a different payload."""

    __tablename__ = "project_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class DomainEventRow(Base):
    """One append-only outbox event, committed in the same transaction as the
    domain change it describes. There is no update or delete path."""

    __tablename__ = "domain_events"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    event_type: Mapped[str] = mapped_column(Text())
    payload: Mapped[dict] = mapped_column(PgJSONB())
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class WorkflowRunRow(Base):
    """One execution of a code-owned Workflow Definition (ADR-0003): the row
    binds the definition key, definition version, and payload schema version
    that created it, so code upgrades affect only new runs. Status is the
    business-facing subset of the Job states."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("btrim(definition_key) <> ''", name="definition_key_not_blank"),
        Index("ix_workflow_runs_project_id_id", "project_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    definition_key: Mapped[str] = mapped_column(Text())
    definition_version: Mapped[int] = mapped_column(Integer())
    payload_schema_version: Mapped[int] = mapped_column(Integer())
    payload: Mapped[dict] = mapped_column(PgJSONB())
    status: Mapped[str] = mapped_column(Text(), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class WorkflowRunIdempotencyKeyRow(Base):
    """A create-attempt key for a Workflow Run; the unique key is what
    PostgreSQL enforces, the fingerprint detects reuse with a different
    payload."""

    __tablename__ = "workflow_run_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    run_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class JobTypeRow(Base):
    """Database-owned per-Job-Type runtime settings (ADR-0003): timeout,
    heartbeat, lease, retry, and backoff live here, not in code, so the retry
    policy is observable and tunable without a deploy."""

    __tablename__ = "job_types"
    __table_args__ = (
        CheckConstraint("timeout_seconds > 0", name="timeout_positive"),
        CheckConstraint("heartbeat_seconds > 0", name="heartbeat_positive"),
        CheckConstraint("lease_seconds > 0", name="lease_positive"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_at_least_one"),
        CheckConstraint("backoff_seconds >= 0", name="backoff_non_negative"),
        CheckConstraint("backoff_multiplier >= 1.0", name="backoff_multiplier_at_least_one"),
        CheckConstraint("backoff_max_seconds > 0", name="backoff_max_positive"),
        CheckConstraint("btrim(job_type) <> ''", name="job_type_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    job_type: Mapped[str] = mapped_column(Text(), unique=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer())
    heartbeat_seconds: Mapped[int] = mapped_column(Integer())
    lease_seconds: Mapped[int] = mapped_column(Integer())
    max_attempts: Mapped[int] = mapped_column(Integer())
    backoff_seconds: Mapped[int] = mapped_column(Integer())
    backoff_multiplier: Mapped[float] = mapped_column(Numeric())
    backoff_max_seconds: Mapped[int] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class JobRow(Base):
    """A persistently queued unit of work (ADR-0003): claimed transactionally,
    held under a lease with heartbeats, executed through attempts. The
    fencing token is monotonic per claim; checkpoint and step writes must
    carry it. Status is the closed set from the spec, enforced here."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'ready', 'leased', 'running', 'pause_requested', 'paused', "
            "'cancel_requested', 'cancelling', 'interrupted', "
            "'succeeded', 'failed', 'cancelled', 'skipped')",
            name="status",
        ),
        CheckConstraint(
            "failure_class IS NULL OR failure_class IN ("
            "'transient', 'permanent', 'cancelled', 'timeout', "
            "'quota_exhausted', 'upstream_invalid')",
            name="failure_class",
        ),
        CheckConstraint("btrim(job_type) <> ''", name="job_type_not_blank"),
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    workflow_run_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    job_type: Mapped[str] = mapped_column(Text())
    payload: Mapped[dict] = mapped_column(PgJSONB())
    status: Mapped[str] = mapped_column(Text(), server_default="ready")
    # Attempt and fencing token both advance on every claim; the token is
    # what checkpoint and step writes must present (ADR-0003).
    attempt: Mapped[int] = mapped_column(Integer(), server_default="0")
    fencing_token: Mapped[int] = mapped_column(BigInteger(), server_default="0")
    # Earliest claim time (backoff scheduling); NULL means due now.
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_class: Mapped[str | None] = mapped_column(Text())
    last_error: Mapped[str | None] = mapped_column(Text())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class JobAttemptRow(Base):
    """One claim-and-execute of a Job. Terminal job states are immutable, so
    history grows here instead of being rewritten: recovery creates a new
    attempt row, never a reopened one."""

    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt", name="uq_job_attempts_job_id_attempt"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ("
            "'succeeded', 'failed', 'cancelled', 'abandoned', 'interrupted')",
            name="outcome",
        ),
        CheckConstraint(
            "failure_class IS NULL OR failure_class IN ("
            "'transient', 'permanent', 'cancelled', 'timeout', "
            "'quota_exhausted', 'upstream_invalid')",
            name="failure_class",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    attempt: Mapped[int] = mapped_column(Integer())
    fencing_token: Mapped[int] = mapped_column(BigInteger())
    worker_id: Mapped[str] = mapped_column(Text())
    outcome: Mapped[str | None] = mapped_column(Text())
    failure_class: Mapped[str | None] = mapped_column(Text())
    detail: Mapped[dict | None] = mapped_column(PgJSONB())
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobStepRow(Base):
    """One step of a Job. The (job_id, step_key) pair is the stable
    idempotency key PostgreSQL enforces: replaying a step finds the recorded
    outcome instead of duplicating its effect."""

    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "step_key", name="uq_job_steps_job_id_step_key"),
        CheckConstraint("status IN ('started', 'succeeded', 'failed')", name="status"),
        CheckConstraint("btrim(step_key) <> ''", name="step_key_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    step_key: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(Text(), server_default="started")
    attempt: Mapped[int] = mapped_column(Integer())
    fencing_token: Mapped[int] = mapped_column(BigInteger())
    result: Mapped[dict | None] = mapped_column(PgJSONB())
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class JobCheckpointRow(Base):
    """A resumable position recorded inside a Job attempt. Writes carry the
    attempt's fencing token, so a stale Worker cannot overwrite a newer
    attempt's results (ADR-0003). Append-only."""

    __tablename__ = "job_checkpoints"
    __table_args__ = (
        CheckConstraint("btrim(checkpoint_key) <> ''", name="checkpoint_key_not_blank"),
        Index("ix_job_checkpoints_job_id_id", "job_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    attempt: Mapped[int] = mapped_column(Integer())
    fencing_token: Mapped[int] = mapped_column(BigInteger())
    checkpoint_key: Mapped[str] = mapped_column(Text())
    state: Mapped[dict] = mapped_column(PgJSONB())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class VolumeRow(Base):
    """An ordered grouping of Chapters inside a Project (ADR-0004): stable
    identity, mutable position — reordering renumbers positions without
    touching identities. The project_id reference is a typed UUID column;
    domain services validate it transactionally (ADR-0009, no physical
    foreign keys)."""

    __tablename__ = "volumes"
    __table_args__ = (
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        Index("ix_volumes_project_id_position", "project_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text())
    position: Mapped[int] = mapped_column(Integer())
    revision: Mapped[int] = mapped_column(BigInteger(), server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class VolumeIdempotencyKeyRow(Base):
    """A create-attempt key for a Volume; the unique key is what PostgreSQL
    enforces, the fingerprint detects reuse with a different payload."""

    __tablename__ = "volume_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    volume_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ChapterRow(Base):
    """The primary editorial unit of a Project (ADR-0004): stable identity,
    mutable position inside its Volume. Later tickets point it at one
    Canonical Chapter Version. References (project_id, volume_id) are typed
    UUID columns validated transactionally by domain services (ADR-0009)."""

    __tablename__ = "chapters"
    __table_args__ = (
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        # Deferred in the migration: one reorder transaction renumbers
        # siblings without transient conflicts at commit.
        UniqueConstraint(
            "volume_id",
            "position",
            name="uq_chapters_volume_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_chapters_volume_id_position", "volume_id", "position"),
        Index("ix_chapters_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    volume_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text())
    position: Mapped[int] = mapped_column(Integer())
    revision: Mapped[int] = mapped_column(BigInteger(), server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ChapterIdempotencyKeyRow(Base):
    """A create-attempt key for a Chapter; the unique key is what PostgreSQL
    enforces, the fingerprint detects reuse with a different payload."""

    __tablename__ = "chapter_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    chapter_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class OutlineRow(Base):
    """One immutable Outline version (ADR-0004): work, volume, chapter,
    scene, foreshadowing, and constraint plans frozen at publication, bound
    to the payload schema version that shaped them. Append-only — changes
    publish a new version, history is never rewritten."""

    __tablename__ = "outlines"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "version_number", name="uq_outlines_project_id_version_number"
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        Index("ix_outlines_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    version_number: Mapped[int] = mapped_column(Integer())
    payload_schema_version: Mapped[int] = mapped_column(Integer())
    plans: Mapped[dict] = mapped_column(PgJSONB())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class OutlineIdempotencyKeyRow(Base):
    """A create-attempt key for an Outline publication; the unique key is
    what PostgreSQL enforces, the fingerprint detects reuse with a different
    payload."""

    __tablename__ = "outline_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    outline_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class BibleIdentityRow(Base):
    """A stable Story Bible identity (ADR-0004): the name and aliases persist
    across versions while each Published Bible Version freezes the entries
    that reference it. The (project_id, name) pair is unique."""

    __tablename__ = "bible_identities"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="ck_bible_identities_name_not_blank"),
        UniqueConstraint("project_id", "name", name="uq_bible_identities_project_id_name"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text())
    aliases: Mapped[list] = mapped_column(PgJSONB())
    revision: Mapped[int] = mapped_column(BigInteger(), server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class BibleEntryRow(Base):
    """One typed Bible Entry on the editable Bible Draft (ADR-0004): the
    entry_type is the nine-value closed set from the architecture doc; the
    detail validates against the type's contract in the domain. Entries are
    frozen into Published Bible Versions at publication; archive (not
    delete) removes one from the draft's working set."""

    __tablename__ = "bible_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ("
            "'character', 'relationship', 'location', 'organization', "
            "'item', 'rule', 'event', 'style', 'open_question')",
            name="ck_bible_entries_entry_type",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="ck_bible_entries_status"),
        Index("ix_bible_entries_project_id", "project_id"),
        Index("ix_bible_entries_identity_id", "identity_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    identity_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    entry_type: Mapped[str] = mapped_column(Text())
    detail: Mapped[dict] = mapped_column(PgJSONB())
    status: Mapped[str] = mapped_column(Text(), server_default="active")
    revision: Mapped[int] = mapped_column(BigInteger(), server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PublishedBibleVersionRow(Base):
    """One immutable Published Bible Version (ADR-0004): the frozen snapshot
    of the draft's identities and entries at publication. Append-only —
    generation-facing references resolve here, never to the draft."""

    __tablename__ = "published_bible_versions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "version_number",
            name="uq_published_bible_versions_project_id_version_number",
        ),
        CheckConstraint(
            "version_number >= 1", name="ck_published_bible_versions_version_number_positive"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    version_number: Mapped[int] = mapped_column(Integer())
    payload_schema_version: Mapped[int] = mapped_column(Integer())
    entries: Mapped[dict] = mapped_column(PgJSONB())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class BibleIdentityIdempotencyKeyRow(Base):
    __tablename__ = "bible_identity_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    identity_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class BibleEntryIdempotencyKeyRow(Base):
    __tablename__ = "bible_entry_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    entry_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class BiblePublishIdempotencyKeyRow(Base):
    __tablename__ = "bible_publish_idempotency_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text(), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(Text())
    version_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
