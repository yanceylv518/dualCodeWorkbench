import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode.execution_jobs import (claim_job, create_job, decide_job, record_job_evidence,
                                     recover_execution_jobs, request_retry)
from dualcode.models import Approval, Base, ExecutionJob, Thread, Workspace


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        workspace = Workspace(name="test", path="C:/repo")
        session.add(workspace)
        await session.flush()
        thread = Thread(workspace_id=workspace.id, title="task")
        session.add(thread)
        await session.commit()
        yield session, workspace, thread
    await engine.dispose()


@pytest.mark.asyncio
async def test_approval_decision_advances_durable_job(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="git_push", reason="push")
    session.add(approval)
    await session.flush()
    job = await create_job(session, approval=approval, workspace_id=workspace.id,
                           kind="git_action", payload={"action": "push"})
    await session.commit()

    assert json.loads(job.payload) == {"action": "push"}
    await decide_job(session, approval.id, True)
    await session.commit()
    assert (await session.get(ExecutionJob, job.id)).status == "READY"


@pytest.mark.asyncio
async def test_explicit_click_authorization_creates_ready_auditable_job(db):
    session, workspace, thread = db
    approval = Approval(
        thread_id=thread.id,
        action="remote_git_provision",
        reason="clone repository after explicit button click",
        status="APPROVED",
    )
    session.add(approval)
    await session.flush()

    job = await create_job(
        session,
        approval=approval,
        workspace_id=workspace.id,
        kind="remote_git",
        payload={"action": "provision", "repository": "/home/user/work/product"},
        initial_status="READY",
    )
    await session.commit()

    persisted = await session.get(ExecutionJob, job.id)
    assert persisted is not None
    assert persisted.status == "READY"
    assert persisted.approval_id == approval.id
    assert (await session.get(Approval, approval.id)).status == "APPROVED"


@pytest.mark.asyncio
async def test_ready_job_rejects_unapproved_authorization(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="remote_git_provision", reason="clone")
    session.add(approval)
    await session.flush()

    with pytest.raises(ValueError, match="requires an approved authorization"):
        await create_job(
            session,
            approval=approval,
            workspace_id=workspace.id,
            kind="remote_git",
            payload={"action": "provision"},
            initial_status="READY",
        )


@pytest.mark.asyncio
async def test_recovery_reconciles_decision_and_does_not_replay_running_job(db):
    session, workspace, thread = db
    approved = Approval(thread_id=thread.id, action="git_pull", reason="pull", status="APPROVED")
    running_approval = Approval(thread_id=thread.id, action="git_push", reason="push", status="APPROVED")
    session.add_all([approved, running_approval])
    await session.flush()
    ready = await create_job(session, approval=approved, workspace_id=workspace.id,
                             kind="git_action", payload={"action": "pull"})
    running = await create_job(session, approval=running_approval, workspace_id=workspace.id,
                               kind="git_action", payload={"action": "push"})
    running.status = "RUNNING"
    await session.commit()

    counts = await recover_execution_jobs(session)

    assert counts == {"waiting": 1, "ready": 1, "interrupted": 1, "reconciled": 0}
    assert (await session.get(ExecutionJob, ready.id)).status == "READY"
    recovered = await session.get(ExecutionJob, running.id)
    assert recovered.status == "INTERRUPTED"
    assert "outcome is unknown" in recovered.last_error


@pytest.mark.asyncio
async def test_recovery_cancels_legacy_clone_approval(db):
    session, workspace, thread = db
    approval = Approval(
        thread_id=thread.id,
        action="remote_git_provision",
        reason="legacy duplicate clone approval",
        status="PENDING",
    )
    session.add(approval)
    await session.flush()
    job = await create_job(
        session,
        approval=approval,
        workspace_id=workspace.id,
        kind="remote_git",
        payload={"action": "provision"},
    )
    await session.commit()

    await recover_execution_jobs(session)

    assert (await session.get(Approval, approval.id)).status == "REJECTED"
    assert (await session.get(ExecutionJob, job.id)).status == "CANCELLED"


@pytest.mark.asyncio
async def test_recovery_marks_running_job_succeeded_only_with_verified_after_evidence(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="git_push", reason="push", status="APPROVED")
    session.add(approval)
    await session.flush()
    job = await create_job(session, approval=approval, workspace_id=workspace.id,
                           kind="git_action", payload={"action": "push"})
    job.status = "RUNNING"
    await record_job_evidence(session, approval.id, "before", {"head": "abc"})
    await record_job_evidence(session, approval.id, "after", {"head": "abc", "verified": True})
    await session.commit()

    counts = await recover_execution_jobs(session)

    assert counts == {"waiting": 0, "ready": 0, "interrupted": 0, "reconciled": 1}
    recovered = await session.get(ExecutionJob, job.id)
    assert recovered.status == "SUCCEEDED"
    assert json.loads(recovered.evidence)["after"]["verified"] is True


@pytest.mark.asyncio
async def test_unverified_after_evidence_never_claims_success(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="git_pull", reason="pull", status="APPROVED")
    session.add(approval)
    await session.flush()
    job = await create_job(session, approval=approval, workspace_id=workspace.id,
                           kind="git_action", payload={"action": "pull"})
    job.status = "RUNNING"
    await record_job_evidence(session, approval.id, "after", {"head": "def", "verified": False})
    await session.commit()

    await recover_execution_jobs(session)

    recovered = await session.get(ExecutionJob, job.id)
    assert recovered.status == "INTERRUPTED"


@pytest.mark.asyncio
async def test_explicit_retry_requires_existing_approval_and_claim_is_atomic(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="git_push", reason="push", status="APPROVED")
    session.add(approval)
    await session.flush()
    job = await create_job(session, approval=approval, workspace_id=workspace.id,
                           kind="git_action", payload={"action": "push"})
    job.status = "INTERRUPTED"
    job.attempts = 1
    await session.commit()

    assert await request_retry(session, job) is True
    await session.commit()
    assert await request_retry(session, job) is True
    await session.commit()

    claimed = await claim_job(session, job.id)
    assert claimed is not None
    assert claimed.status == "RUNNING"
    assert claimed.attempts == 2
    assert await claim_job(session, job.id) is None


@pytest.mark.asyncio
async def test_retry_rejects_unapproved_or_terminal_job(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="git_push", reason="push", status="PENDING")
    session.add(approval)
    await session.flush()
    job = await create_job(session, approval=approval, workspace_id=workspace.id,
                           kind="git_action", payload={"action": "push"})
    job.status = "FAILED"
    await session.commit()
    with pytest.raises(ValueError, match="approved authorization"):
        await request_retry(session, job)

    approval.status = "APPROVED"
    job.status = "SUCCEEDED"
    await session.commit()
    with pytest.raises(ValueError, match="cannot be retried"):
        await request_retry(session, job)


@pytest.mark.asyncio
async def test_execution_job_timestamps_round_trip_as_aware_utc(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="git_pull", reason="pull")
    session.add(approval)
    await session.flush()
    job = await create_job(session, approval=approval, workspace_id=workspace.id,
                           kind="git_action", payload={"action": "pull"})
    await session.commit()
    job_id = job.id
    session.expire_all()

    persisted = await session.get(ExecutionJob, job_id)
    assert persisted is not None
    assert persisted.created_at.tzinfo is UTC
    assert persisted.updated_at.tzinfo is UTC
    assert abs(datetime.now(UTC) - persisted.created_at) < timedelta(seconds=5)


@pytest.mark.asyncio
async def test_legacy_naive_sqlite_timestamp_is_interpreted_as_utc(db):
    session, workspace, thread = db
    approval = Approval(thread_id=thread.id, action="git_push", reason="push")
    session.add(approval)
    await session.flush()
    job = await create_job(session, approval=approval, workspace_id=workspace.id,
                           kind="git_action", payload={"action": "push"})
    await session.commit()
    job_id = job.id

    legacy_value = "2025-01-02 03:04:05.000000"
    await session.execute(
        text("UPDATE execution_jobs SET created_at = :value WHERE id = :job_id"),
        {"value": legacy_value, "job_id": job_id},
    )
    await session.commit()
    session.expire_all()

    persisted = await session.get(ExecutionJob, job_id)
    assert persisted is not None
    assert persisted.created_at == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
