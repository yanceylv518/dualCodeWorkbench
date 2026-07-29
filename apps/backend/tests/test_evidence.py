import pytest
from pydantic import ValidationError

from dualcode.evidence import (
    MAX_SUMMARY_LENGTH,
    EvidenceItem,
    from_agent_run,
    from_file_change,
    from_handoff,
    from_test_run,
)
from dualcode.models import AgentRun, FileChange, HandoffPackage, RunState
from dualcode.models import TestRun as PersistedTestRun

LARGE_FIELD_MARKER = "MUST_NOT_ENTER_EVIDENCE"


def test_projects_test_run_without_output() -> None:
    item = from_test_run(
        PersistedTestRun(
            id="test-1",
            thread_id="thread-1",
            command="pytest -q",
            output=LARGE_FIELD_MARKER,
            exit_code=1,
        )
    )

    assert item == EvidenceItem(
        kind="test",
        source_id="test-1",
        thread_id="thread-1",
        summary="pytest -q → exit 1",
        command="pytest -q",
        exit_code=1,
    )
    assert LARGE_FIELD_MARKER not in item.model_dump_json()


def test_projects_file_change_without_diff() -> None:
    item = from_file_change(
        FileChange(
            id="change-1",
            thread_id="thread-1",
            path="src/example.py",
            diff=LARGE_FIELD_MARKER,
        )
    )

    assert item == EvidenceItem(
        kind="file_change",
        source_id="change-1",
        thread_id="thread-1",
        summary="src/example.py",
        path="src/example.py",
    )
    assert LARGE_FIELD_MARKER not in item.model_dump_json()


def test_projects_agent_run_without_output_or_diffs() -> None:
    item = from_agent_run(
        AgentRun(
            id="run-1",
            thread_id="thread-1",
            agent="codex",
            state=RunState.COMPLETED,
            output=LARGE_FIELD_MARKER,
            before_diff=LARGE_FIELD_MARKER,
            after_diff=LARGE_FIELD_MARKER,
        )
    )

    assert item == EvidenceItem(
        kind="agent_run",
        source_id="run-1",
        thread_id="thread-1",
        summary="codex → COMPLETED",
        agent="codex",
        status="COMPLETED",
    )
    assert LARGE_FIELD_MARKER not in item.model_dump_json()


def test_projects_handoff_without_payload() -> None:
    item = from_handoff(
        HandoffPackage(
            id="handoff-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            recipient="claude",
            purpose="review",
            payload=LARGE_FIELD_MARKER,
            status="PREPARED",
        )
    )

    assert item == EvidenceItem(
        kind="handoff",
        source_id="handoff-1",
        thread_id="thread-1",
        summary="claude ← review → PREPARED",
        status="PREPARED",
    )
    assert LARGE_FIELD_MARKER not in item.model_dump_json()


def test_summary_is_single_line_and_limited_to_200_characters() -> None:
    command = f"{'x' * 100}\n{'y' * 120}"
    item = from_test_run(
        PersistedTestRun(
            id="test-1",
            thread_id="thread-1",
            command=command,
            output="",
            exit_code=0,
        )
    )

    assert len(item.summary) == MAX_SUMMARY_LENGTH
    assert item.summary.endswith("…")
    assert "\n" not in item.summary


def test_short_summary_is_not_truncated() -> None:
    item = from_file_change(
        FileChange(
            id="change-1",
            thread_id="thread-1",
            path="README.md",
            diff="",
        )
    )

    assert item.summary == "README.md"


def test_evidence_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EvidenceItem.model_validate(
            {
                "kind": "test",
                "source_id": "test-1",
                "thread_id": "thread-1",
                "summary": "passed",
                "unknown": True,
            }
        )


def test_evidence_rejects_fields_for_another_kind() -> None:
    with pytest.raises(ValidationError, match="Fields not allowed"):
        EvidenceItem(
            kind="file_change",
            source_id="change-1",
            thread_id="thread-1",
            summary="README.md",
            command="pytest",
        )
