from datetime import datetime, timezone
from enum import StrEnum
from pydantic import BaseModel, Field


class EventType(StrEnum):
    CONNECTED = "connected"
    MESSAGE_CREATED = "message.created"
    AGENT_DELTA = "agent.delta"
    TOOL_EVENT = "agent.tool"
    RUN_STATE_CHANGED = "run.state_changed"
    RUN_OUTPUT = "run.output"
    TEST_RESULT = "test.result"
    TERMINAL_OUTPUT = "terminal.output"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_DECIDED = "approval.decided"
    RUN_COMPLETED = "run.completed"
    COLLABORATION_STARTED = "collaboration.started"
    COLLABORATION_STAGE_CHANGED = "collaboration.stage_changed"
    COLLABORATION_AGENT_CHANGED = "collaboration.agent_changed"
    COLLABORATION_HANDOFF_PREPARED = "collaboration.handoff_prepared"
    COLLABORATION_REVIEW_COMPLETED = "collaboration.review_completed"
    COLLABORATION_FINDINGS_UPDATED = "collaboration.findings_updated"
    COLLABORATION_WAITING_USER = "collaboration.waiting_user"
    COLLABORATION_COMPLETED = "collaboration.completed"
    COLLABORATION_FAILED = "collaboration.failed"
    ERROR = "error"


class AgentEvent(BaseModel):
    type: EventType
    thread_id: str
    run_id: str | None = None
    sequence: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, object] = Field(default_factory=dict)
