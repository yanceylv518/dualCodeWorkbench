import enum
import uuid
from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uid() -> str:
    return str(uuid.uuid4())


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


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(30))
    state: Mapped[RunState] = mapped_column(Enum(RunState))
    output: Mapped[str] = mapped_column(Text, default="")


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


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(String, index=True)
    thread_id: Mapped[str | None] = mapped_column(String, index=True)
    event: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
