"""Versioned contracts for future cross-agent collaboration orchestration.

``CollaborationState`` is the future ``collaboration_runs.state`` value and
describes orchestration across agents. The existing ``RunState`` continues to
describe one ``AgentRun`` lifecycle. They are intentionally neither merged nor
converted here.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


FactKind = Literal[
    "requirement",
    "decision",
    "repository",
    "evidence",
    "risk",
    "assumption",
]
FactSource = Literal["user", "git", "test", "codex", "claude", "system"]
FactConfidence = Literal["confirmed", "verified", "unverified", "stale"]

FACT_CONFIDENCE_RANK: dict[FactConfidence, int] = {
    "confirmed": 3,
    "verified": 2,
    "unverified": 1,
    "stale": 0,
}

# Memory facts may carry acceptance criteria, so their governed limit is
# intentionally larger than the 200-character evidence summary limit.
MEMORY_FACT_CONTENT_MAX_LENGTH = 500


class MemoryFactContent(StrictModel):
    content: str

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        # Local import avoids a module cycle: evidence reuses StrictModel.
        from .evidence import summarize_single_line

        return summarize_single_line(value, MEMORY_FACT_CONTENT_MAX_LENGTH)


class HandoffTask(StrictModel):
    goal: str
    non_goals: list[str]
    acceptance: list[str]
    constraints: list[str]


class HandoffRepository(StrictModel):
    base_sha: str
    snapshot_sha: str
    branch: str
    changed_files: list[str]
    diff_stats: dict[str, int]


class HandoffEvidence(StrictModel):
    type: str
    command: str
    exit_code: int
    summary: str


class HandoffV2(StrictModel):
    schema_version: Literal["handoff.v2"] = Field(alias="schema")
    purpose: Literal["implement", "review", "fix", "verify"]
    sender: str
    recipient: str
    task: HandoffTask
    repository: HandoffRepository
    claims: list[str]
    evidence: list[HandoffEvidence]
    open_findings: list[str]
    risks: list[str]
    requested_action: str


class ReviewFinding(StrictModel):
    id: str
    type: Literal["missing", "partial", "regression", "risk", "architecture", "evidence"]
    severity: Literal["blocking", "advisory"]
    file: str | None = None
    line: str | None = None
    description: str
    acceptance: str


class ReviewV1(StrictModel):
    schema_version: Literal["review.v1"] = Field(alias="schema")
    verdict: Literal["pass", "blocking", "needs_user"]
    summary: str
    findings: list[ReviewFinding]


class CollaborationState(str, enum.Enum):
    DRAFT = "DRAFT"
    CLARIFYING = "CLARIFYING"
    READY = "READY"
    IMPLEMENTING = "IMPLEMENTING"
    VERIFYING = "VERIFYING"
    SYNCING_REVIEW_SNAPSHOT = "SYNCING_REVIEW_SNAPSHOT"
    REVIEWING = "REVIEWING"
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    FIXING = "FIXING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_USER = "WAITING_USER"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


COLLABORATION_TRANSITIONS: dict[CollaborationState, frozenset[CollaborationState]] = {
    CollaborationState.DRAFT: frozenset(
        {
            CollaborationState.CLARIFYING,
            CollaborationState.READY,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.CLARIFYING: frozenset(
        {
            CollaborationState.READY,
            CollaborationState.WAITING_USER,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.READY: frozenset(
        {CollaborationState.IMPLEMENTING, CollaborationState.CANCELLED}
    ),
    CollaborationState.IMPLEMENTING: frozenset(
        {
            CollaborationState.VERIFYING,
            CollaborationState.WAITING_APPROVAL,
            CollaborationState.WAITING_USER,
            CollaborationState.BLOCKED,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.VERIFYING: frozenset(
        {
            CollaborationState.SYNCING_REVIEW_SNAPSHOT,
            CollaborationState.WAITING_APPROVAL,
            CollaborationState.WAITING_USER,
            CollaborationState.BLOCKED,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.SYNCING_REVIEW_SNAPSHOT: frozenset(
        {
            CollaborationState.REVIEWING,
            CollaborationState.WAITING_APPROVAL,
            CollaborationState.WAITING_USER,
            CollaborationState.BLOCKED,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.REVIEWING: frozenset(
        {
            CollaborationState.ACCEPTED,
            CollaborationState.CHANGES_REQUESTED,
            CollaborationState.WAITING_APPROVAL,
            CollaborationState.WAITING_USER,
            CollaborationState.BLOCKED,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.ACCEPTED: frozenset(
        {CollaborationState.COMPLETED, CollaborationState.CANCELLED}
    ),
    CollaborationState.CHANGES_REQUESTED: frozenset(
        {CollaborationState.FIXING, CollaborationState.CANCELLED}
    ),
    CollaborationState.FIXING: frozenset(
        {
            CollaborationState.VERIFYING,
            CollaborationState.WAITING_APPROVAL,
            CollaborationState.WAITING_USER,
            CollaborationState.BLOCKED,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.WAITING_APPROVAL: frozenset(
        {
            CollaborationState.IMPLEMENTING,
            CollaborationState.VERIFYING,
            CollaborationState.SYNCING_REVIEW_SNAPSHOT,
            CollaborationState.REVIEWING,
            CollaborationState.FIXING,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.WAITING_USER: frozenset(
        {
            CollaborationState.READY,
            CollaborationState.FIXING,
            CollaborationState.CANCELLED,
        }
    ),
    CollaborationState.BLOCKED: frozenset(
        {
            CollaborationState.IMPLEMENTING,
            CollaborationState.VERIFYING,
            CollaborationState.SYNCING_REVIEW_SNAPSHOT,
            CollaborationState.REVIEWING,
            CollaborationState.FIXING,
            CollaborationState.CANCELLED,
        }
    ),
}


def transition(current: CollaborationState, target: CollaborationState) -> CollaborationState:
    """Return the target for a legal protocol transition."""

    if target not in COLLABORATION_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"Illegal collaboration transition: {current.value} -> {target.value}")
    return target


class RoutingRule(StrictModel):
    primary_agent: str
    collaborator: str
    process: str


ROUTING_MATRIX: dict[str, RoutingRule] = {
    "简单问答、解释": RoutingRule(
        primary_agent="最匹配单 Agent", collaborator="无", process="直接完成"
    ),
    "小型样式或单点修复": RoutingRule(
        primary_agent="Codex", collaborator="按风险决定", process="实现 → 验证"
    ),
    "普通功能开发": RoutingRule(
        primary_agent="Codex", collaborator="Claude", process="实现 → 审查 → 必要整改"
    ),
    "需求不清或产品设计": RoutingRule(
        primary_agent="Claude",
        collaborator="Codex 可实现性复核",
        process="澄清 → 确认 → 实现",
    ),
    "架构迁移": RoutingRule(
        primary_agent="Claude", collaborator="Codex", process="方案 → 可行性 → 实现 → 审查"
    ),
    "Bug 与故障恢复": RoutingRule(
        primary_agent="Codex", collaborator="Claude", process="根因 → 修复 → 回归审查"
    ),
    "安全、高风险、数据迁移": RoutingRule(
        primary_agent="Claude 先审",
        collaborator="Codex 后执行",
        process="风险审查 → 用户批准 → 实现",
    ),
    "测试、构建、打包": RoutingRule(
        primary_agent="Codex", collaborator="Claude 可验收", process="执行 → 证据归档"
    ),
}

DUAL_AGENT_COMPLEXITY_CONDITIONS: tuple[str, ...] = (
    "修改跨越两个以上领域或五个以上文件",
    "涉及数据库迁移、权限、安全、Git 副作用或远程执行",
    "修改架构边界、公共协议或交付规则",
    "用户明确要求方案审查、专业交付或完整产品实现",
    "同类问题已返工一次",
)

SINGLE_AGENT_DEFAULT_CONDITIONS: tuple[str, ...] = (
    "解释",
    "只读查询",
    "文案",
    "低风险局部样式",
)


def route_for(request_category: str) -> RoutingRule:
    """Look up a frozen routing rule without classifying the request."""

    try:
        return ROUTING_MATRIX[request_category]
    except KeyError as exc:
        raise ValueError(f"Unknown request category: {request_category}") from exc
