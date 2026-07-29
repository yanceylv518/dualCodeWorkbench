import json

import pytest
from pydantic import ValidationError

from dualcode.collaboration_audit import (
    EVENT_ROUTING_DECISION,
    EVENT_STATE_TRANSITION,
    RoutingDecisionDetail,
    StateTransitionDetail,
    build_routing_decision_audit,
    build_state_transition_audit,
)
from dualcode.collaboration_protocol import CollaborationState
from dualcode.evidence import MAX_SUMMARY_LENGTH


def test_builds_state_transition_audit_and_round_trips_detail() -> None:
    detail = StateTransitionDetail(
        run_id="run-1",
        from_state=CollaborationState.IMPLEMENTING,
        to_state=CollaborationState.VERIFYING,
        round=2,
        reason="implementation completed",
    )

    row = build_state_transition_audit("workspace-1", "thread-1", detail)

    assert row.workspace_id == "workspace-1"
    assert row.thread_id == "thread-1"
    assert row.event == EVENT_STATE_TRANSITION
    assert json.loads(row.detail) == {
        "run_id": "run-1",
        "from_state": "IMPLEMENTING",
        "to_state": "VERIFYING",
        "round": 2,
        "reason": "implementation completed",
    }
    assert StateTransitionDetail.model_validate_json(row.detail) == detail


def test_builds_routing_decision_audit_and_round_trips_detail() -> None:
    detail = RoutingDecisionDetail(
        category="feature",
        primary_agent="Codex",
        collaborator="Claude",
        reason="requires implementation and review",
    )

    row = build_routing_decision_audit("workspace-1", "thread-1", detail)

    assert row.workspace_id == "workspace-1"
    assert row.thread_id == "thread-1"
    assert row.event == EVENT_ROUTING_DECISION
    assert json.loads(row.detail) == {
        "category": "feature",
        "primary_agent": "Codex",
        "collaborator": "Claude",
        "reason": "requires implementation and review",
    }
    assert RoutingDecisionDetail.model_validate_json(row.detail) == detail


def test_rejects_illegal_transition_before_building_audit() -> None:
    detail = StateTransitionDetail(
        run_id="run-1",
        from_state=CollaborationState.COMPLETED,
        to_state=CollaborationState.IMPLEMENTING,
        round=1,
        reason="invalid resume",
    )

    with pytest.raises(ValueError, match="Illegal collaboration transition"):
        build_state_transition_audit("workspace-1", "thread-1", detail)


def test_rejects_unknown_routing_category_before_building_audit() -> None:
    detail = RoutingDecisionDetail(
        category="未知类别",
        primary_agent="Codex",
        collaborator="无",
        reason="unknown",
    )

    with pytest.raises(ValueError, match="Unknown request category"):
        build_routing_decision_audit("workspace-1", "thread-1", detail)


def test_detail_strings_are_single_line_and_truncated() -> None:
    long_value = f"{'x' * 100}\n{'y' * 120}"
    state_detail = StateTransitionDetail(
        run_id=long_value,
        from_state=CollaborationState.READY,
        to_state=CollaborationState.IMPLEMENTING,
        round=1,
        reason=long_value,
    )
    routing_detail = RoutingDecisionDetail(
        category="feature",
        primary_agent=long_value,
        collaborator=long_value,
        reason=long_value,
    )
    long_category_detail = RoutingDecisionDetail(
        category=long_value,
        primary_agent="Codex",
        collaborator="Claude",
        reason="classification",
    )

    for value in (
        state_detail.run_id,
        state_detail.reason,
        routing_detail.primary_agent,
        routing_detail.collaborator,
        routing_detail.reason,
        long_category_detail.category,
    ):
        assert len(value) == MAX_SUMMARY_LENGTH
        assert value.endswith("…")
        assert "\n" not in value


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            StateTransitionDetail,
            {
                "run_id": "run-1",
                "from_state": "READY",
                "to_state": "IMPLEMENTING",
                "round": 1,
                "reason": "ready",
                "prompt": "must be rejected",
            },
        ),
        (
            RoutingDecisionDetail,
            {
                "category": "feature",
                "primary_agent": "Codex",
                "collaborator": "Claude",
                "reason": "review required",
                "credential": "must be rejected",
            },
        ),
    ],
)
def test_detail_models_reject_unknown_fields(model, payload) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model.model_validate(payload)
