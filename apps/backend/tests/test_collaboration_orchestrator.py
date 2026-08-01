from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode.collaboration_orchestrator import (
    advance,
    cancel,
    recover_interrupted_runs,
    resume,
    start_run,
)
from dualcode.collaboration_protocol import CollaborationState
from dualcode.models import (
    AuditLog,
    Base,
    CollaborationRun,
    Message,
    TaskContract,
    Thread,
    Workspace,
)
from dualcode.task_classifier import classify


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'orchestrator.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _project(session, *, ready: bool) -> tuple[Workspace, Thread]:
    workspace = Workspace(name="Project", path="D:/project")
    session.add(workspace)
    await session.flush()
    thread = Thread(workspace_id=workspace.id, title="Task")
    session.add(thread)
    await session.flush()
    if ready:
        session.add(
            TaskContract(
                thread_id=thread.id,
                goal="Implement feature",
                acceptance=json.dumps(["Tests pass"]),
            )
        )
        await session.flush()
    return workspace, thread


@pytest.mark.asyncio
async def test_start_run_goes_directly_ready_with_audit(sessions, monkeypatch: pytest.MonkeyPatch):
    events = []

    async def publish(event):
        events.append(event)

    monkeypatch.setattr("dualcode.collaboration_orchestrator.manager.publish", publish)
    async with sessions() as session:
        workspace, thread = await _project(session, ready=True)
        run = await start_run(
            session,
            workspace,
            thread,
            decision=classify("实现登录功能"),
        )
        await session.commit()
        audits = list(
            await session.scalars(select(AuditLog).where(AuditLog.thread_id == thread.id))
        )

    assert run.mode == "smart"
    assert run.state == CollaborationState.READY.value
    assert run.round == 1
    assert run.max_rounds == 3
    assert run.current_agent == "codex"
    assert len(audits) == 1
    detail = json.loads(audits[0].detail)
    assert (detail["from_state"], detail["to_state"]) == ("DRAFT", "READY")
    assert [event.type.value for event in events] == [
        "collaboration.started",
        "collaboration.stage_changed",
    ]


@pytest.mark.asyncio
async def test_start_run_drafts_acceptance_from_a_clear_goal(
    sessions, monkeypatch: pytest.MonkeyPatch
):
    async def publish(_event):
        return None

    monkeypatch.setattr("dualcode.collaboration_orchestrator.manager.publish", publish)
    async with sessions() as session:
        workspace, thread = await _project(session, ready=False)
        contract = TaskContract(thread_id=thread.id, goal="修复保存后界面没有反馈")
        session.add(contract)
        await session.flush()
        run = await start_run(
            session,
            workspace,
            thread,
            decision=classify(contract.goal),
        )
        await session.commit()

    assert run.state == CollaborationState.READY.value
    assert contract.status == "READY"
    acceptance = json.loads(contract.acceptance)
    assert len(acceptance) >= 2
    assert any("验证" in item for item in acceptance)


@pytest.mark.asyncio
async def test_incomplete_contract_clarifies_then_waits_without_agent(
    sessions, monkeypatch: pytest.MonkeyPatch
):
    events = []

    async def publish(event):
        events.append(event)

    monkeypatch.setattr("dualcode.collaboration_orchestrator.manager.publish", publish)
    async with sessions() as session:
        workspace, thread = await _project(session, ready=False)
        run = await start_run(
            session,
            workspace,
            thread,
            decision=classify("设计系统架构"),
        )
        await session.commit()
        audits = list(
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.thread_id == thread.id)
                .order_by(AuditLog.created_at)
            )
        )
        messages = list(
            await session.scalars(select(Message).where(Message.thread_id == thread.id))
        )

    assert run.state == CollaborationState.WAITING_USER.value
    assert [json.loads(item.detail)["to_state"] for item in audits] == [
        "CLARIFYING",
        "WAITING_USER",
    ]
    assert [message.role for message in messages] == ["system"]
    assert "无需填写固定格式" in messages[0].content
    assert [event.type.value for event in events] == [
        "collaboration.started",
        "collaboration.stage_changed",
        "message.created",
        "collaboration.stage_changed",
        "collaboration.waiting_user",
    ]


@pytest.mark.asyncio
async def test_advance_rejects_illegal_transition_and_audits_legal_one(
    sessions, monkeypatch: pytest.MonkeyPatch
):
    async def publish(_event):
        return None

    monkeypatch.setattr("dualcode.collaboration_orchestrator.manager.publish", publish)
    async with sessions() as session:
        workspace, thread = await _project(session, ready=True)
        run = await start_run(session, workspace, thread, decision=classify("实现功能"))
        await advance(
            session,
            run,
            CollaborationState.IMPLEMENTING,
            reason="开始实现",
        )
        with pytest.raises(ValueError, match="Illegal collaboration transition"):
            await advance(
                session,
                run,
                CollaborationState.COMPLETED,
                reason="非法跳过验证",
            )
        await session.commit()
        audits = list(
            await session.scalars(select(AuditLog).where(AuditLog.thread_id == thread.id))
        )

    assert run.state == CollaborationState.IMPLEMENTING.value
    assert len(audits) == 2


@pytest.mark.asyncio
async def test_suspend_resume_returns_to_recorded_source_state(
    sessions, monkeypatch: pytest.MonkeyPatch
):
    async def publish(_event):
        return None

    monkeypatch.setattr("dualcode.collaboration_orchestrator.manager.publish", publish)
    async with sessions() as session:
        workspace, thread = await _project(session, ready=True)
        run = await start_run(session, workspace, thread, decision=classify("实现功能"))
        await advance(
            session,
            run,
            CollaborationState.IMPLEMENTING,
            reason="开始实现",
        )
        await advance(
            session,
            run,
            CollaborationState.WAITING_APPROVAL,
            reason="等待审批",
        )
        assert json.loads(run.budget_json)["resume_state"] == "IMPLEMENTING"
        await resume(session, run, reason="审批完成")

    assert run.state == CollaborationState.IMPLEMENTING.value


@pytest.mark.asyncio
async def test_cancel_stops_agent_and_marks_terminal(sessions, monkeypatch: pytest.MonkeyPatch):
    async def publish(_event):
        return None

    monkeypatch.setattr("dualcode.collaboration_orchestrator.manager.publish", publish)
    cancelled: list[str] = []

    async def cancel_agent(thread_id: str):
        cancelled.append(thread_id)

    async with sessions() as session:
        workspace, thread = await _project(session, ready=True)
        run = await start_run(session, workspace, thread, decision=classify("实现功能"))
        await cancel(
            session,
            run,
            reason="用户取消",
            cancel_agent=cancel_agent,
        )

    assert cancelled == [thread.id]
    assert run.state == CollaborationState.CANCELLED.value
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_startup_recovery_blocks_running_and_preserves_waiting(
    sessions, monkeypatch: pytest.MonkeyPatch
):
    async def publish(_event):
        return None

    monkeypatch.setattr("dualcode.collaboration_orchestrator.manager.publish", publish)
    async with sessions() as session:
        workspace, thread = await _project(session, ready=True)
        running = CollaborationRun(
            workspace_id=workspace.id,
            thread_id=thread.id,
            mode="smart",
            state=CollaborationState.REVIEWING.value,
            round=2,
            max_rounds=3,
        )
        waiting = CollaborationRun(
            workspace_id=workspace.id,
            thread_id=thread.id,
            mode="smart",
            state=CollaborationState.WAITING_USER.value,
            round=1,
            max_rounds=3,
        )
        session.add_all([running, waiting])
        await session.flush()
        recovered = await recover_interrupted_runs(session)
        await session.commit()
        audits = list(
            await session.scalars(select(AuditLog).where(AuditLog.thread_id == thread.id))
        )

    assert recovered == [running.id]
    assert running.state == CollaborationState.BLOCKED.value
    assert running.error == "应用重启中断"
    assert json.loads(running.budget_json)["resume_state"] == "REVIEWING"
    assert waiting.state == CollaborationState.WAITING_USER.value
    assert len(audits) == 1
