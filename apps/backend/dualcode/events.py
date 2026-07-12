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
    ERROR = "error"


class AgentEvent(BaseModel):
    type: EventType
    thread_id: str
    run_id: str | None = None
    sequence: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, object] = Field(default_factory=dict)
