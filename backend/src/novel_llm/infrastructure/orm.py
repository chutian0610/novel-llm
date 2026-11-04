"""ORM rows for domain tables (ADR-0002: PostgreSQL is authoritative).

Domain tables arrive with their Phase 1 tickets; the metadata carries a naming
convention so constraint names match the hand-written Alembic migrations.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, MetaData, Text, text
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
