import enum
import uuid
from datetime import UTC, datetime
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def uid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Return an aware UTC timestamp suitable for persisted model defaults."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC timestamps while preserving awareness with SQLite.

    SQLite stores ``DATETIME`` values without timezone metadata. Existing
    databases therefore contain naive values that historically represented
    UTC. Normalizing on bind and restoring UTC on result keeps those rows
    compatible and gives callers consistently aware timestamps.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class RunState(str, enum.Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    REVIEWING = "REVIEWING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    FALLBACK_TO_CODEX = "FALLBACK_TO_CODEX"


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    threads: Mapped[list["Thread"]] = relationship(cascade="all, delete-orphan")


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    state: Mapped[RunState] = mapped_column(Enum(RunState), default=RunState.CREATED)
    messages: Mapped[list["Message"]] = relationship(cascade="all, delete-orphan")


class ProjectGovernance(Base):
    __tablename__ = "project_governance"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, index=True
    )
    product_goal: Mapped[str] = mapped_column(Text, default="")
    product_boundary: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[str] = mapped_column(Text, default="[]")
    deliverables: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class TaskContract(Base):
    __tablename__ = "task_contracts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), unique=True, index=True
    )
    goal: Mapped[str] = mapped_column(Text, default="")
    non_goals: Mapped[str] = mapped_column(Text, default="[]")
    acceptance: Mapped[str] = mapped_column(Text, default="[]")
    constraints: Mapped[str] = mapped_column(Text, default="[]")
    risks: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class HandoffPackage(Base):
    __tablename__ = "handoff_packages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    recipient: Mapped[str] = mapped_column(String(20))
    purpose: Mapped[str] = mapped_column(String(30))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="PREPARED")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    attachments: Mapped[list["Attachment"]] = relationship()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(30))
    state: Mapped[RunState] = mapped_column(Enum(RunState))
    output: Mapped[str] = mapped_column(Text, default="")
    before_diff: Mapped[str] = mapped_column(Text, default="")
    after_diff: Mapped[str] = mapped_column(Text, default="")


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(30))
    external_session_id: Mapped[str] = mapped_column(String(255), index=True)
    workspace_path: Mapped[str] = mapped_column(String(1024))


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)


class FileChange(Base):
    __tablename__ = "file_changes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    diff: Mapped[str] = mapped_column(Text)


class TestRun(Base):
    __tablename__ = "test_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    command: Mapped[str] = mapped_column(String(500))
    output: Mapped[str] = mapped_column(Text)
    exit_code: Mapped[int] = mapped_column(Integer)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    reason: Mapped[str] = mapped_column(Text)


class ExecutionJob(Base):
    """Durable description of an operation guarded by an approval.

    Payload is JSON text rather than a pickle so database contents remain
    inspectable and migrations do not execute untrusted data.
    """
    __tablename__ = "execution_jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), unique=True, index=True)
    workspace_id: Mapped[str] = mapped_column(String, index=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="WAITING_APPROVAL", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(String, index=True)
    thread_id: Mapped[str | None] = mapped_column(String, index=True)
    event: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
