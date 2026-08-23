"""PostgreSQL persistence models and session dependency."""

import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    """Keep the database schema in one metadata registry for migrations."""


class Request(Base):
    """Persist the immutable request inputs and its eventual resolution."""

    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(CHAR(8), primary_key=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    resolved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(Text)
    tool_args: Mapped[object] = mapped_column(JSONB)
    justification: Mapped[str] = mapped_column(Text)
    risk_class: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    dial_at_decision: Mapped[int | None] = mapped_column(SmallInteger)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_requests_created_at_desc", created_at.desc()),
        Index("ix_requests_tool_name_verdict", tool_name, verdict),
    )


class WardenAssessment(Base):
    """Keep the Warden's independently auditable proposal for a request."""

    __tablename__ = "warden_assessments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[str] = mapped_column(CHAR(8), ForeignKey("requests.id"))
    model: Mapped[str] = mapped_column(Text)
    risk_class: Mapped[str] = mapped_column(Text)
    reversible: Mapped[bool] = mapped_column(Boolean)
    blast_radius: Mapped[str] = mapped_column(Text)
    injection_suspected: Mapped[bool] = mapped_column(Boolean)
    reasoning: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[object] = mapped_column(JSONB)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Represent rows protected by the database's append-only trigger."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[object] = mapped_column(DateTime(timezone=True))
    event: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(CHAR(8))
    payload: Mapped[object] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(CHAR(64))
    row_hash: Mapped[str] = mapped_column(CHAR(64))


class Policy(Base):
    """Store the deterministic policy rows that can only narrow Warden output."""

    __tablename__ = "policies"

    tool_pattern: Mapped[str] = mapped_column(Text, primary_key=True)
    min_dial: Mapped[int] = mapped_column(SmallInteger)
    action: Mapped[str] = mapped_column(Text)
    relay_gated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    dwell_s: Mapped[int] = mapped_column(SmallInteger, server_default=text("60"))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str] = mapped_column(Text)


def database_url() -> str:
    """Require the explicit driver so deployments cannot fall back to psycopg2."""
    url = os.environ.get("DATABASE_URL")
    if url is None:
        raise RuntimeError("DATABASE_URL must use postgresql+pg8000://")
    if not url.startswith("postgresql+pg8000://"):
        raise RuntimeError("DATABASE_URL must use postgresql+pg8000://")
    return url


@lru_cache
def engine() -> Engine:
    """Share one engine because SQLAlchemy owns its connection pool."""
    return create_engine(database_url())


def session_factory() -> sessionmaker[Session]:
    """Construct sessions with state that remains valid after a commit."""
    return sessionmaker(bind=engine(), autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Close each request session even when FastAPI raises while handling it."""
    with session_factory()() as session:
        yield session
