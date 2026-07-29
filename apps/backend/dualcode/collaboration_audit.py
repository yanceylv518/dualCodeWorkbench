"""Strict, side-effect-free builders for future collaboration audit events."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from .collaboration_protocol import (
    CollaborationState,
    FactConfidence,
    FactKind,
    FactSource,
    StrictModel,
    route_for,
    transition,
)
from .evidence import summarize_single_line
from .models import AuditLog

EVENT_STATE_TRANSITION = "collaboration.state_transition"
EVENT_ROUTING_DECISION = "collaboration.routing_decision"
EVENT_MEMORY_CHANGE = "collaboration.memory_change"


class StateTransitionDetail(StrictModel):
    run_id: str
    from_state: CollaborationState
    to_state: CollaborationState
    round: int
    reason: str

    @field_validator("run_id", "reason")
    @classmethod
    def normalize_strings(cls, value: str) -> str:
        return summarize_single_line(value)


class RoutingDecisionDetail(StrictModel):
    category: str
    primary_agent: str
    collaborator: str
    reason: str

    @field_validator("category", "primary_agent", "collaborator", "reason")
    @classmethod
    def normalize_strings(cls, value: str) -> str:
        return summarize_single_line(value)


class MemoryChangeDetail(StrictModel):
    fact_id: str
    kind: FactKind
    action: Literal["created", "superseded", "invalidated"]
    source: FactSource
    confidence: FactConfidence

    @field_validator("fact_id")
    @classmethod
    def normalize_fact_id(cls, value: str) -> str:
        return summarize_single_line(value)


def build_state_transition_audit(
    workspace_id: str,
    thread_id: str,
    detail: StateTransitionDetail,
) -> AuditLog:
    transition(detail.from_state, detail.to_state)
    return AuditLog(
        workspace_id=workspace_id,
        thread_id=thread_id,
        event=EVENT_STATE_TRANSITION,
        detail=detail.model_dump_json(),
    )


def build_routing_decision_audit(
    workspace_id: str,
    thread_id: str,
    detail: RoutingDecisionDetail,
) -> AuditLog:
    route_for(detail.category)
    return AuditLog(
        workspace_id=workspace_id,
        thread_id=thread_id,
        event=EVENT_ROUTING_DECISION,
        detail=detail.model_dump_json(),
    )


def build_memory_change_audit(
    workspace_id: str,
    thread_id: str | None,
    detail: MemoryChangeDetail,
) -> AuditLog:
    return AuditLog(
        workspace_id=workspace_id,
        thread_id=thread_id,
        event=EVENT_MEMORY_CHANGE,
        detail=detail.model_dump_json(),
    )
