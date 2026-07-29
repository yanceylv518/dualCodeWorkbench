import pytest
from pydantic import ValidationError

from dualcode.collaboration_protocol import (
    COLLABORATION_TRANSITIONS,
    DUAL_AGENT_COMPLEXITY_CONDITIONS,
    ROUTING_MATRIX,
    CollaborationState,
    HandoffV2,
    ReviewV1,
    route_for,
    transition,
)


def handoff_payload() -> dict:
    return {
        "schema": "handoff.v2",
        "purpose": "review",
        "sender": "codex",
        "recipient": "claude",
        "task": {
            "goal": "Freeze collaboration contracts",
            "non_goals": ["Wire the orchestrator"],
            "acceptance": ["Models round-trip"],
            "constraints": ["No behavior changes"],
        },
        "repository": {
            "base_sha": "abc123",
            "snapshot_sha": "def456",
            "branch": "main",
            "changed_files": ["protocol.py"],
            "diff_stats": {"insertions": 10, "deletions": 0},
        },
        "claims": ["Protocol is strict"],
        "evidence": [
            {"type": "test", "command": "pytest -q", "exit_code": 0, "summary": "passed"}
        ],
        "open_findings": [],
        "risks": [],
        "requested_action": "Review implementation and evidence",
    }


def review_payload() -> dict:
    return {
        "schema": "review.v1",
        "verdict": "blocking",
        "summary": "One finding",
        "findings": [
            {
                "id": "F-1",
                "type": "evidence",
                "severity": "blocking",
                "file": "protocol.py",
                "line": "42",
                "description": "Missing negative test",
                "acceptance": "Negative test passes",
            }
        ],
    }


def test_handoff_and_review_round_trip() -> None:
    handoff = HandoffV2.model_validate(handoff_payload())
    review = ReviewV1.model_validate(review_payload())

    assert handoff.model_dump()["schema"] == "handoff.v2"
    assert review.model_dump()["schema"] == "review.v1"
    assert HandoffV2.model_validate_json(handoff.model_dump_json()) == handoff
    assert ReviewV1.model_validate_json(review.model_dump_json()) == review


@pytest.mark.parametrize(
    ("factory", "mutation"),
    [
        (handoff_payload, lambda payload: payload.pop("requested_action")),
        (handoff_payload, lambda payload: payload.update(purpose="plan")),
        (handoff_payload, lambda payload: payload.update(unknown=True)),
        (handoff_payload, lambda payload: payload["task"].update(unknown=True)),
        (handoff_payload, lambda payload: payload.update(schema="handoff.v1")),
        (review_payload, lambda payload: payload.pop("summary")),
        (review_payload, lambda payload: payload.update(verdict="maybe")),
        (review_payload, lambda payload: payload["findings"][0].update(type="style")),
        (review_payload, lambda payload: payload["findings"][0].update(unknown=True)),
        (review_payload, lambda payload: payload.update(schema="review.v2")),
    ],
)
def test_protocol_rejects_invalid_payloads(factory, mutation) -> None:
    payload = factory()
    mutation(payload)
    model = HandoffV2 if "purpose" in payload else ReviewV1

    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_success_path_reaches_completed() -> None:
    path = [
        CollaborationState.DRAFT,
        CollaborationState.CLARIFYING,
        CollaborationState.READY,
        CollaborationState.IMPLEMENTING,
        CollaborationState.VERIFYING,
        CollaborationState.SYNCING_REVIEW_SNAPSHOT,
        CollaborationState.REVIEWING,
        CollaborationState.ACCEPTED,
        CollaborationState.COMPLETED,
    ]

    for current, target in zip(path[:-1], path[1:], strict=True):
        assert transition(current, target) is target


def test_fix_path_returns_to_verification() -> None:
    assert (
        transition(CollaborationState.REVIEWING, CollaborationState.CHANGES_REQUESTED)
        is CollaborationState.CHANGES_REQUESTED
    )
    assert (
        transition(CollaborationState.CHANGES_REQUESTED, CollaborationState.FIXING)
        is CollaborationState.FIXING
    )
    assert (
        transition(CollaborationState.FIXING, CollaborationState.VERIFYING)
        is CollaborationState.VERIFYING
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CollaborationState.DRAFT, CollaborationState.COMPLETED),
        (CollaborationState.COMPLETED, CollaborationState.DRAFT),
        (CollaborationState.CANCELLED, CollaborationState.READY),
    ],
)
def test_illegal_transitions_raise(current: CollaborationState, target: CollaborationState) -> None:
    with pytest.raises(ValueError, match="Illegal collaboration transition"):
        transition(current, target)


def test_transition_table_covers_every_non_terminal_documented_state() -> None:
    expected_sources = {
        CollaborationState.DRAFT,
        CollaborationState.CLARIFYING,
        CollaborationState.READY,
        CollaborationState.IMPLEMENTING,
        CollaborationState.VERIFYING,
        CollaborationState.SYNCING_REVIEW_SNAPSHOT,
        CollaborationState.REVIEWING,
        CollaborationState.ACCEPTED,
        CollaborationState.CHANGES_REQUESTED,
        CollaborationState.FIXING,
    }
    assert set(COLLABORATION_TRANSITIONS) == expected_sources


def test_every_documented_state_is_reachable_from_draft() -> None:
    reachable = {CollaborationState.DRAFT}
    frontier = [CollaborationState.DRAFT]
    while frontier:
        current = frontier.pop()
        for target in COLLABORATION_TRANSITIONS.get(current, frozenset()):
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)

    assert reachable == set(CollaborationState)


def test_routing_matrix_matches_all_documented_rows() -> None:
    expected = {
        "简单问答、解释": ("最匹配单 Agent", "无", "直接完成"),
        "小型样式或单点修复": ("Codex", "按风险决定", "实现 → 验证"),
        "普通功能开发": ("Codex", "Claude", "实现 → 审查 → 必要整改"),
        "需求不清或产品设计": ("Claude", "Codex 可实现性复核", "澄清 → 确认 → 实现"),
        "架构迁移": ("Claude", "Codex", "方案 → 可行性 → 实现 → 审查"),
        "Bug 与故障恢复": ("Codex", "Claude", "根因 → 修复 → 回归审查"),
        "安全、高风险、数据迁移": (
            "Claude 先审",
            "Codex 后执行",
            "风险审查 → 用户批准 → 实现",
        ),
        "测试、构建、打包": ("Codex", "Claude 可验收", "执行 → 证据归档"),
    }

    assert set(ROUTING_MATRIX) == set(expected)
    for category, values in expected.items():
        first = route_for(category)
        second = route_for(category)
        assert (first.primary_agent, first.collaborator, first.process) == values
        assert first == second


def test_complexity_conditions_are_frozen() -> None:
    assert len(DUAL_AGENT_COMPLEXITY_CONDITIONS) == 5
    assert "同类问题已返工一次" in DUAL_AGENT_COMPLEXITY_CONDITIONS


def test_unknown_route_raises() -> None:
    with pytest.raises(ValueError, match="Unknown request category"):
        route_for("未知类别")
