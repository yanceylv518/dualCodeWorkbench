from pydantic import BaseModel, ConfigDict, Field
from .models import RunState


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    mode: str = Field(default="collaboration", pattern="^(auto|codex|claude|collaboration)$")
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)


class ThreadCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class WorkspaceCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=1000)


class GitActionCreate(BaseModel):
    action: str = Field(pattern="^(commit|push|pull)$")
    message: str = Field(default="", max_length=200)


class RemoteGitActionCreate(BaseModel):
    action: str = Field(pattern="^(fetch|pull)$")


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str


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
