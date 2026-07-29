"""Compile persisted project state into the frozen handoff.v2 protocol."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .collaboration_protocol import (
    HandoffEvidence,
    HandoffRepository,
    HandoffTask,
    HandoffV2,
)
from .evidence import from_test_run
from .git_service import GitService
from .models import (
    FileChange,
    HandoffPackage,
    ReviewFinding,
    TaskContract,
    TestRun,
    Thread,
    Workspace,
)


def _json_list(value: str) -> list[str]:
    parsed = json.loads(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


async def compile_handoff_v2(
    db: AsyncSession,
    workspace: Workspace,
    thread: Thread,
    *,
    purpose: str,
    sender: str,
    recipient: str,
) -> HandoffV2:
    contract = await db.scalar(
        select(TaskContract).where(TaskContract.thread_id == thread.id)
    )
    changes = (
        await db.scalars(
            select(FileChange)
            .where(FileChange.thread_id == thread.id)
            .order_by(FileChange.path)
        )
    ).all()
    tests = (
        await db.scalars(
            select(TestRun).where(TestRun.thread_id == thread.id).order_by(TestRun.id)
        )
    ).all()
    open_findings = (
        await db.scalars(
            select(ReviewFinding.description)
            .join(
                HandoffPackage,
                ReviewFinding.source_handoff_id == HandoffPackage.id,
            )
            .where(
                HandoffPackage.thread_id == thread.id,
                ReviewFinding.status == "open",
            )
            .order_by(ReviewFinding.id)
        )
    ).all()
    repository_path = Path(workspace.path)
    repository = await GitService(repository_path.parent).repository_status(
        repository_path
    )
    base_sha = str(repository["head"])
    changed_files = list(dict.fromkeys(item.path for item in changes))
    evidence = [
        HandoffEvidence(
            type="test",
            command=projection.command or "",
            exit_code=projection.exit_code or 0,
            summary=projection.summary,
        )
        for projection in (from_test_run(row) for row in tests)
    ]

    return HandoffV2(
        schema="handoff.v2",
        purpose=purpose,
        sender=sender,
        recipient=recipient,
        task=HandoffTask(
            goal=contract.goal if contract else "",
            non_goals=_json_list(contract.non_goals) if contract else [],
            acceptance=_json_list(contract.acceptance) if contract else [],
            constraints=_json_list(contract.constraints) if contract else [],
        ),
        repository=HandoffRepository(
            base_sha=base_sha,
            # C4 will replace this with the immutable shadow snapshot SHA.
            snapshot_sha=base_sha,
            branch=str(repository["branch"]),
            changed_files=changed_files,
            diff_stats={"files": len(changed_files)},
        ),
        claims=[],
        evidence=evidence,
        open_findings=list(open_findings),
        risks=_json_list(contract.risks) if contract else [],
        requested_action=purpose,
    )
