from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode.models import (
    AgentRun,
    Approval,
    AuditLog,
    Base,
    FileChange,
    HandoffPackage,
    Message,
    RunState,
    Thread,
    Workspace,
)
from dualcode.scheduler import RunScheduler
from dualcode.task_classifier import classify


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "expected_agent", "expects_collaboration"),
    [
        ("解释一下这个字段是什么", "codex", False),
        ("增加项目收藏功能", "codex", True),
        ("设计新的系统架构", "claude", True),
    ],
)
async def test_smart_mode_routes_dual_agent_through_collaboration_loop(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    expected_agent: str,
    expects_collaboration: bool,
) -> None:
    scheduler = RunScheduler.__new__(RunScheduler)
    calls: list[tuple[str, object]] = []

    async def record_route(thread_id, decision):
        calls.append(("route", decision.category))

    async def execute_chat(thread_id, run_id, current_prompt, agent, attachment_ids):
        calls.append(("execute", agent))

    async def execute_collaboration(thread_id, current_prompt, attachment_ids, decision):
        calls.append(("collaboration", decision.category))

    async def upgrade_after_diff(thread_id, run_id, agent, decision):
        calls.append(("diff_check", decision.category))
        return False

    monkeypatch.setattr(scheduler, "_record_smart_route", record_route)
    monkeypatch.setattr(scheduler, "_execute_chat", execute_chat)
    monkeypatch.setattr(
        scheduler, "_execute_smart_collaboration", execute_collaboration
    )
    monkeypatch.setattr(scheduler, "_upgrade_after_diff", upgrade_after_diff)

    await scheduler._execute("thread-1", "run-1", prompt, "smart", [])

    assert any(kind == "route" for kind, _ in calls)
    assert any(kind == "collaboration" for kind, _ in calls) is expects_collaboration
    assert any(kind == "execute" for kind, _ in calls) is (not expects_collaboration)
    assert any(kind == "diff_check" for kind, _ in calls) is (
        not expects_collaboration
    )
    if not expects_collaboration:
        assert ("execute", expected_agent) in calls


@pytest.mark.asyncio
async def test_explicit_agent_mode_does_not_enter_smart_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = RunScheduler.__new__(RunScheduler)
    calls: list[str] = []

    async def execute_chat(thread_id, run_id, prompt, agent, attachment_ids):
        calls.append(agent)

    monkeypatch.setattr(scheduler, "_execute_chat", execute_chat)

    await scheduler._execute("thread-1", "run-1", "保持原行为", "claude", [])

    assert calls == ["claude"]


@pytest.mark.asyncio
async def test_feature_route_persists_audit_system_messages_and_prepared_handoff(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
    )
    (repository / "README.md").write_text("project", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=DualCode Test",
            "-c",
            "user.email=dualcode@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'smart.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workspace = Workspace(name="Project", path=str(repository))
    async with sessions() as db:
        db.add(workspace)
        await db.flush()
        thread = Thread(workspace_id=workspace.id, title="Task")
        db.add(thread)
        await db.flush()
        run = AgentRun(
            id="run-1",
            thread_id=thread.id,
            agent="codex",
            state=RunState.COMPLETED,
        )
        db.add(run)
        await db.commit()
        thread_id = thread.id

    monkeypatch.setattr("dualcode.scheduler.SessionLocal", sessions)

    async def ignore_publish(event):
        return None

    monkeypatch.setattr("dualcode.scheduler.manager.publish", ignore_publish)
    scheduler = RunScheduler.__new__(RunScheduler)
    decision = classify("增加项目收藏功能")
    await scheduler._record_smart_route(thread_id, decision)
    await scheduler._prepare_review_handoff(
        thread_id, "run-1", "codex", decision
    )

    async with sessions() as db:
        messages = list(
            await db.scalars(
                select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at)
            )
        )
        audits = list(
            await db.scalars(
                select(AuditLog).where(AuditLog.thread_id == thread_id)
            )
        )
        handoff = await db.scalar(
            select(HandoffPackage).where(HandoffPackage.thread_id == thread_id)
        )

    assert [message.role for message in messages] == ["system", "system"]
    assert messages[0].content.startswith("智能路由：普通功能开发")
    assert messages[1].content.startswith("已准备审查交接")
    assert {audit.event for audit in audits} == {
        "collaboration.routing_decision",
        "handoff.prepared",
    }
    assert handoff is not None
    assert handoff.status == "PREPARED"
    assert handoff.recipient == "claude"
    assert handoff.purpose == "review"
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category_prompt", "changed_count", "expected_upgrade"),
    [
        ("调整页面样式", 6, True),
        ("调整页面样式", 5, False),
        ("解释一下是什么", 0, False),
    ],
)
async def test_single_agent_route_upgrades_only_above_diff_threshold(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    category_prompt: str,
    changed_count: int,
    expected_upgrade: bool,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / f'upgrade-{changed_count}.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        workspace = Workspace(name="Project", path=str(tmp_path))
        db.add(workspace)
        await db.flush()
        thread = Thread(workspace_id=workspace.id, title="Task")
        db.add(thread)
        await db.flush()
        db.add(
            AgentRun(
                id="run-1",
                thread_id=thread.id,
                agent="codex",
                state=RunState.COMPLETED,
            )
        )
        for index in range(changed_count):
            db.add(
                FileChange(
                    thread_id=thread.id,
                    path=f"src/file-{index}.py",
                    diff="+change",
                )
            )
        await db.commit()
        thread_id = thread.id

    monkeypatch.setattr("dualcode.scheduler.SessionLocal", sessions)

    async def ignore_publish(event):
        return None

    prepared: list[str] = []

    async def prepare_handoff(thread_id, run_id, agent, decision):
        prepared.append(decision.category)

    monkeypatch.setattr("dualcode.scheduler.manager.publish", ignore_publish)
    scheduler = RunScheduler.__new__(RunScheduler)
    monkeypatch.setattr(scheduler, "_prepare_review_handoff", prepare_handoff)
    upgraded = await scheduler._upgrade_after_diff(
        thread_id, "run-1", "codex", classify(category_prompt)
    )

    async with sessions() as db:
        audits = list(
            await db.scalars(
                select(AuditLog).where(
                    AuditLog.thread_id == thread_id,
                    AuditLog.event == "collaboration.routing_decision",
                )
            )
        )
        system_messages = list(
            await db.scalars(
                select(Message).where(
                    Message.thread_id == thread_id,
                    Message.role == "system",
                )
            )
        )

    assert upgraded is expected_upgrade
    assert bool(prepared) is expected_upgrade
    assert bool(audits) is expected_upgrade
    assert bool(system_messages) is expected_upgrade
    if expected_upgrade:
        assert "事后 Diff 升级：6 个文件" in audits[0].detail
        assert "已升级为双 Agent 审查" in system_messages[0].content
    await engine.dispose()


@pytest.mark.asyncio
async def test_relay_sync_thread_grant_is_restored_from_audit(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'grant.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        workspace = Workspace(name="Project", path=str(tmp_path))
        db.add(workspace)
        await db.flush()
        thread = Thread(workspace_id=workspace.id, title="Task")
        db.add(thread)
        await db.flush()
        approval = Approval(
            thread_id=thread.id,
            action="relay_shadow_sync",
            status="APPROVED",
            reason="允许本任务自动同步影子快照到 VPS？",
        )
        db.add(approval)
        await db.flush()
        db.add(
            AuditLog(
                workspace_id=workspace.id,
                thread_id=thread.id,
                event="approval.decided",
                detail=f"{approval.id}:APPROVED:scope=thread:accepted",
            )
        )
        await db.commit()
        thread_id = thread.id

    scheduler = RunScheduler.__new__(RunScheduler)
    scheduler._thread_grants = set()
    async with sessions() as db:
        assert (
            await scheduler._has_thread_grant(
                db, thread_id, "relay_shadow_sync"
            )
            is True
        )
    assert (thread_id, "relay_shadow_sync") in scheduler._thread_grants

    scheduler._thread_grants.clear()
    async with sessions() as db:
        assert (
            await scheduler._has_thread_grant(
                db, thread_id, "relay_shadow_sync"
            )
            is True
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_relay_sync_requests_task_approval(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approval.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        workspace = Workspace(name="Project", path=str(tmp_path))
        db.add(workspace)
        await db.flush()
        thread = Thread(workspace_id=workspace.id, title="Task")
        db.add(thread)
        await db.commit()
        thread_id = thread.id

    prepared: list[str] = []
    emitted: list[tuple[object, dict[str, object]]] = []
    lifecycle: list[bool | None] = []

    async def approve(approval_id: str) -> bool:
        return True

    async def emit(kind, payload):
        emitted.append((kind, payload))

    async def approval_lifecycle(action, reason, approved):
        assert action == "relay_shadow_sync"
        assert reason
        lifecycle.append(approved)

    monkeypatch.setattr(
        "dualcode.scheduler.approval_gate.prepare", prepared.append
    )
    monkeypatch.setattr("dualcode.scheduler.approval_gate.wait", approve)
    scheduler = RunScheduler.__new__(RunScheduler)
    scheduler._thread_grants = set()
    async with sessions() as db:
        assert (
            await scheduler.ensure_relay_sync_approval(
                db,
                thread_id,
                emit,
                approval_lifecycle=approval_lifecycle,
            )
            is True
        )
    async with sessions() as db:
        approval = await db.scalar(
            select(Approval).where(Approval.thread_id == thread_id)
        )

    assert approval is not None
    assert approval.action == "relay_shadow_sync"
    assert approval.reason == "允许本任务自动同步影子快照到 VPS？"
    assert prepared == [approval.id]
    assert emitted[0][1]["action"] == "relay_shadow_sync"
    assert lifecycle == [None, True]
    await engine.dispose()
