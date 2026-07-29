import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode.collaboration_audit import EVENT_MEMORY_CHANGE, MemoryChangeDetail
from dualcode.memory_service import (
    mark_stale_for_commit,
    record_fact,
    snapshot_thread_facts,
)
from dualcode.models import (
    AuditLog,
    Base,
    MemoryFact,
    TaskContract,
    TestRun as RunRecord,
    Thread,
    Workspace,
)


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _scope(session, tmp_path) -> tuple[Workspace, Thread]:
    workspace = Workspace(name="Project", path=str(tmp_path))
    session.add(workspace)
    await session.flush()
    thread = Thread(workspace_id=workspace.id, title="Task")
    session.add(thread)
    await session.flush()
    return workspace, thread


@pytest.mark.asyncio
async def test_record_fact_persists_governed_content_and_audit(session, tmp_path) -> None:
    workspace, thread = await _scope(session, tmp_path)
    fact = await record_fact(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        kind="requirement",
        content="one\n two",
        source="user",
        confidence="confirmed",
    )
    await session.flush()

    assert json.loads(fact.content_json) == {"content": "one two"}
    audit = await session.scalar(
        select(AuditLog).where(AuditLog.event == EVENT_MEMORY_CHANGE)
    )
    assert audit is not None
    assert MemoryChangeDetail.model_validate_json(audit.detail) == MemoryChangeDetail(
        fact_id=fact.id,
        kind="requirement",
        action="created",
        source="user",
        confidence="confirmed",
    )


@pytest.mark.asyncio
async def test_record_fact_allows_equal_or_higher_confidence_override(
    session, tmp_path
) -> None:
    workspace, thread = await _scope(session, tmp_path)
    old = await record_fact(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        kind="decision",
        content="draft",
        source="codex",
        confidence="unverified",
    )
    new = await record_fact(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        kind="decision",
        content="accepted",
        source="user",
        confidence="confirmed",
        supersedes_id=old.id,
    )
    await session.flush()

    assert old.invalidated_at is not None
    assert new.supersedes_id == old.id
    details = [
        MemoryChangeDetail.model_validate_json(value)
        for value in (
            await session.scalars(
                select(AuditLog.detail)
                .where(AuditLog.event == EVENT_MEMORY_CHANGE)
                .order_by(AuditLog.created_at)
            )
        ).all()
    ]
    assert [detail.action for detail in details] == ["created", "superseded"]


@pytest.mark.asyncio
async def test_record_fact_rejects_lower_confidence_override(session, tmp_path) -> None:
    workspace, thread = await _scope(session, tmp_path)
    old = await record_fact(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        kind="risk",
        content="confirmed risk",
        source="user",
        confidence="confirmed",
    )
    with pytest.raises(ValueError, match="must not decrease"):
        await record_fact(
            session,
            workspace_id=workspace.id,
            thread_id=thread.id,
            kind="risk",
            content="agent guess",
            source="claude",
            confidence="unverified",
            supersedes_id=old.id,
        )
    assert old.invalidated_at is None


@pytest.mark.asyncio
async def test_mark_stale_for_commit_updates_only_old_repository_facts(
    session, tmp_path
) -> None:
    workspace, thread = await _scope(session, tmp_path)
    old = await record_fact(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        kind="repository",
        content="old",
        source="git",
        confidence="verified",
        commit_sha="old-sha",
    )
    current = await record_fact(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        kind="repository",
        content="current",
        source="git",
        confidence="verified",
        commit_sha="new-sha",
    )
    changed = await mark_stale_for_commit(session, thread.id, "new-sha")
    await session.flush()

    assert changed == [old]
    assert old.confidence == "stale"
    assert current.confidence == "verified"
    details = [
        MemoryChangeDetail.model_validate_json(value)
        for value in (
            await session.scalars(
                select(AuditLog.detail).where(AuditLog.event == EVENT_MEMORY_CHANGE)
            )
        ).all()
    ]
    assert any(
        detail.fact_id == old.id
        and detail.action == "invalidated"
        and detail.confidence == "stale"
        for detail in details
    )


@pytest.mark.asyncio
async def test_snapshot_is_idempotent_and_excludes_large_source_fields(
    session, tmp_path, monkeypatch
) -> None:
    workspace, thread = await _scope(session, tmp_path)
    session.add(
        TaskContract(
            thread_id=thread.id,
            goal="Ship the product",
            acceptance=json.dumps(["passes tests"]),
            risks=json.dumps(["migration risk"]),
            constraints=json.dumps(["secret constraint " + "x" * 1000]),
        )
    )
    session.add(
        RunRecord(
            thread_id=thread.id,
            command="pytest -q",
            output="SECRET OUTPUT " + "x" * 5000,
            exit_code=0,
        )
    )
    await session.flush()

    async def current_commit(_workspace) -> str:
        return "abc123"

    monkeypatch.setattr("dualcode.memory_service._current_commit", current_commit)
    first = await snapshot_thread_facts(session, workspace, thread)
    second = await snapshot_thread_facts(session, workspace, thread)
    await session.flush()

    facts = (
        await session.scalars(select(MemoryFact).order_by(MemoryFact.kind))
    ).all()
    assert len(first) == 5
    assert second == []
    assert len(facts) == 5
    serialized = " ".join(fact.content_json for fact in facts)
    assert "Ship the product" in serialized
    assert "passes tests" in serialized
    assert "migration risk" in serialized
    assert "Current commit: abc123" in serialized
    assert "pytest -q → exit 0" in serialized
    assert "SECRET OUTPUT" not in serialized
    assert "secret constraint" not in serialized
