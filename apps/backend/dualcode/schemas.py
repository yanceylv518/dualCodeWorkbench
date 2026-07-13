from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import RunState


class MessageCreate(BaseModel):
    content: str = Field(default="", max_length=50_000)
    mode: str = Field(default="codex", pattern="^(codex|claude)$")
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_content_or_attachment(self):
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("Message text or attachment is required")
        return self


class GovernanceUpdate(BaseModel):
    product_goal: str = Field(default="", max_length=20_000)
    product_boundary: str = Field(default="", max_length=20_000)
    rules: list[str] = Field(default_factory=list, max_length=100)
    deliverables: list[str] = Field(default_factory=list, max_length=100)


class TaskContractUpdate(BaseModel):
    goal: str = Field(default="", max_length=20_000)
    non_goals: list[str] = Field(default_factory=list, max_length=100)
    acceptance: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    status: str = Field(default="DRAFT", pattern="^(DRAFT|CLARIFYING|READY|IMPLEMENTING|REVIEWING|CONDITIONAL_PASS|PASSED|BLOCKED)$")


class HandoffCreate(BaseModel):
    recipient: str = Field(pattern="^(codex|claude)$")
    purpose: str = Field(pattern="^(verify|review)$")


class ThreadCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class WorkspaceCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class WorkspaceProvision(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    remote_url: str = Field(default="", max_length=2048)
    mode: str = Field(pattern="^(init|clone)$")
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=1000)
    scope: str = Field(default="once", pattern="^(once|thread)$")


class GitActionCreate(BaseModel):
    action: str = Field(pattern="^(commit|push|pull)$")
    message: str = Field(default="", max_length=200)


class RemoteGitActionCreate(BaseModel):
    action: str = Field(pattern="^(provision|repair_provision|fetch|pull)$")


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    media_type: str
    size: int


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    attachments: list[AttachmentRead] = []


class ThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    state: RunState
    messages: list[MessageRead] = []


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    path: str
    threads: list[ThreadRead] = []
