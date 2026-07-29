"""Persistence service for compact, auditable collaboration memory."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .collaboration_audit import MemoryChangeDetail, build_memory_change_audit
from .collaboration_protocol import (
    FACT_CONFIDENCE_RANK,
    FactConfidence,
    FactKind,
    FactSource,
    MemoryFactContent,
)
from .evidence import from_test_run
from .git_service import GitService
from .models import MemoryFact, TaskContract, TestRun, Thread, Workspace, utc_now


async def record_fact(
    db: AsyncSession,
    *,
    workspace_id: str,
    thread_id: str | None,
    kind: FactKind,
    content: str,
    source: FactSource,
    confidence: FactConfidence,
    commit_sha: str | None = None,
    supersedes_id: str | None = None,
) -> MemoryFact:
    """Record one governed fact and enforce monotonic replacement confidence."""

    governed = MemoryFactContent(content=content)
    replaced: MemoryFact | None = None
    if supersedes_id is not None:
        replaced = await db.get(MemoryFact, supersedes_id)
        if replaced is None:
            raise ValueError("Superseded fact does not exist")
        if replaced.workspace_id != workspace_id or replaced.thread_id != thread_id:
            raise ValueError("Superseded fact belongs to a different scope")
        if replaced.invalidated_at is not None:
            raise ValueError("Superseded fact is already invalidated")
        if FACT_CONFIDENCE_RANK[confidence] < FACT_CONFIDENCE_RANK[replaced.confidence]:
            raise ValueError("Replacement confidence must not decrease")
        if (
            source in {"codex", "claude"}
            and confidence == "unverified"
            and replaced.confidence in {"confirmed", "verified"}
        ):
            raise ValueError("Unverified agent facts cannot replace trusted facts")

    fact = MemoryFact(
        workspace_id=workspace_id,
        thread_id=thread_id,
        kind=kind,
        content_json=governed.model_dump_json(),
        source=source,
        confidence=confidence,
        commit_sha=commit_sha,
        supersedes_id=supersedes_id,
    )
    db.add(fact)
    await db.flush()
    action = "created"
    if replaced is not None:
        replaced.invalidated_at = utc_now()
        action = "superseded"
    db.add(
        build_memory_change_audit(
            workspace_id,
            thread_id,
            MemoryChangeDetail(
                fact_id=fact.id,
                kind=kind,
                action=action,
                source=source,
                confidence=confidence,
            ),
        )
    )
    return fact


async def mark_stale_for_commit(
    db: AsyncSession,
    thread_id: str,
    current_sha: str,
) -> list[MemoryFact]:
    """Mark active repository facts from older commits as stale."""

    facts = list(
        (
            await db.scalars(
                select(MemoryFact).where(
                    MemoryFact.thread_id == thread_id,
                    MemoryFact.kind == "repository",
                    MemoryFact.invalidated_at.is_(None),
                    MemoryFact.commit_sha.is_not(None),
                    MemoryFact.commit_sha != current_sha,
                )
            )
        ).all()
    )
    for fact in facts:
        fact.confidence = "stale"
        db.add(
            build_memory_change_audit(
                fact.workspace_id,
                fact.thread_id,
                MemoryChangeDetail(
                    fact_id=fact.id,
                    kind="repository",
                    action="invalidated",
                    source=fact.source,
                    confidence="stale",
                ),
            )
        )
    return facts


def _json_strings(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = value
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    return [str(parsed)] if str(parsed).strip() else []


async def _current_commit(workspace: Workspace) -> str:
    repository = Path(workspace.path)
    result = await GitService(repository.parent).run(
        repository, "rev-parse", "--verify", "HEAD", check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


async def _record_if_missing(
    db: AsyncSession,
    *,
    workspace_id: str,
    thread_id: str,
    kind: FactKind,
    content: str,
    source: FactSource,
    confidence: FactConfidence,
    commit_sha: str | None = None,
) -> MemoryFact | None:
    content_json = MemoryFactContent(content=content).model_dump_json()
    existing = await db.scalar(
        select(MemoryFact.id).where(
            MemoryFact.workspace_id == workspace_id,
            MemoryFact.thread_id == thread_id,
            MemoryFact.kind == kind,
            MemoryFact.content_json == content_json,
            MemoryFact.commit_sha == commit_sha,
            MemoryFact.invalidated_at.is_(None),
        )
    )
    if existing is not None:
        return None
    return await record_fact(
        db,
        workspace_id=workspace_id,
        thread_id=thread_id,
        kind=kind,
        content=content,
        source=source,
        confidence=confidence,
        commit_sha=commit_sha,
    )


async def snapshot_thread_facts(
    db: AsyncSession,
    workspace: Workspace,
    thread: Thread,
) -> list[MemoryFact]:
    """Project the current task, repository and test state into compact facts."""

    created: list[MemoryFact] = []
    contract = await db.scalar(
        select(TaskContract).where(TaskContract.thread_id == thread.id)
    )
    candidates: list[
        tuple[FactKind, str, FactSource, FactConfidence, str | None]
    ] = []
    if contract is not None:
        if contract.goal.strip():
            candidates.append(("requirement", contract.goal, "user", "confirmed", None))
        candidates.extend(
            ("requirement", item, "user", "confirmed", None)
            for item in _json_strings(contract.acceptance)
        )
        candidates.extend(
            ("risk", item, "user", "confirmed", None)
            for item in _json_strings(contract.risks)
        )

    commit_sha = await _current_commit(workspace)
    if commit_sha:
        candidates.append(
            ("repository", f"Current commit: {commit_sha}", "git", "verified", commit_sha)
        )
    test_runs = (
        await db.scalars(
            select(TestRun).where(TestRun.thread_id == thread.id).order_by(TestRun.id)
        )
    ).all()
    candidates.extend(
        ("evidence", from_test_run(row).summary, "test", "verified", commit_sha or None)
        for row in test_runs
    )

    for kind, content, source, confidence, sha in candidates:
        fact = await _record_if_missing(
            db,
            workspace_id=workspace.id,
            thread_id=thread.id,
            kind=kind,
            content=content,
            source=source,
            confidence=confidence,
            commit_sha=sha,
        )
        if fact is not None:
            created.append(fact)
    return created
