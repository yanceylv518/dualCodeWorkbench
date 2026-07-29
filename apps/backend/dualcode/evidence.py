"""Small, strict evidence projections over existing persistence models.

The projectors are intentionally pure: they do not query or write the database,
and they never copy large source payloads into collaboration context.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .collaboration_protocol import StrictModel
from .models import AgentRun, FileChange, HandoffPackage, TestRun

MAX_SUMMARY_LENGTH = 200


class EvidenceItem(StrictModel):
    kind: Literal["agent_run", "handoff", "test", "file_change"]
    source_id: str
    thread_id: str
    summary: str
    command: str | None = None
    exit_code: int | None = None
    path: str | None = None
    agent: str | None = None
    status: str | None = None

    @model_validator(mode="after")
    def reject_fields_for_other_kinds(self) -> EvidenceItem:
        allowed = {
            "agent_run": {"agent", "status"},
            "handoff": {"status"},
            "test": {"command", "exit_code"},
            "file_change": {"path"},
        }[self.kind]
        optional_fields = {"command", "exit_code", "path", "agent", "status"}
        mismatched = sorted(
            field
            for field in optional_fields - allowed
            if getattr(self, field) is not None
        )
        if mismatched:
            raise ValueError(
                f"Fields not allowed for evidence kind {self.kind}: {', '.join(mismatched)}"
            )
        return self


def summarize_single_line(value: str) -> str:
    single_line = " ".join(value.split())
    if len(single_line) <= MAX_SUMMARY_LENGTH:
        return single_line
    return f"{single_line[: MAX_SUMMARY_LENGTH - 1]}…"


def from_test_run(row: TestRun) -> EvidenceItem:
    return EvidenceItem(
        kind="test",
        source_id=row.id,
        thread_id=row.thread_id,
        summary=summarize_single_line(f"{row.command} → exit {row.exit_code}"),
        command=row.command,
        exit_code=row.exit_code,
    )


def from_file_change(row: FileChange) -> EvidenceItem:
    return EvidenceItem(
        kind="file_change",
        source_id=row.id,
        thread_id=row.thread_id,
        summary=summarize_single_line(row.path),
        path=row.path,
    )


def from_agent_run(row: AgentRun) -> EvidenceItem:
    status = row.state.value
    return EvidenceItem(
        kind="agent_run",
        source_id=row.id,
        thread_id=row.thread_id,
        summary=summarize_single_line(f"{row.agent} → {status}"),
        agent=row.agent,
        status=status,
    )


def from_handoff(row: HandoffPackage) -> EvidenceItem:
    return EvidenceItem(
        kind="handoff",
        source_id=row.id,
        thread_id=row.thread_id,
        summary=summarize_single_line(f"{row.recipient} ← {row.purpose} → {row.status}"),
        status=row.status,
    )
