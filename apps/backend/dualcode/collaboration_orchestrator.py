"""Persistent lifecycle service for deterministic smart collaboration runs."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .collaboration_audit import (
    StateTransitionDetail,
    build_state_transition_audit,
)
from .collaboration_protocol import CollaborationState, transition
from .connections import manager
from .events import AgentEvent, EventType
from .models import (
    CollaborationRun,
    Message,
    TaskContract,
    Thread,
    Workspace,
)
from .task_classifier import RoutingDecision

RUNNING_STATES = frozenset(
    {
        CollaborationState.IMPLEMENTING,
        CollaborationState.VERIFYING,
        CollaborationState.SYNCING_REVIEW_SNAPSHOT,
        CollaborationState.REVIEWING,
        CollaborationState.FIXING,
    }
)
TERMINAL_STATES = frozenset(
    {CollaborationState.COMPLETED, CollaborationState.CANCELLED}
)
SUSPENDED_STATES = frozenset(
    {CollaborationState.WAITING_APPROVAL, CollaborationState.BLOCKED}
)


def _budget(run: CollaborationRun) -> dict[str, object]:
    try:
        value = json.loads(run.budget_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


async def _publish_stage(
    run: CollaborationRun,
    previous: CollaborationState,
    current: CollaborationState,
    reason: str,
) -> None:
    await manager.publish(
        AgentEvent(
            type=EventType.COLLABORATION_STAGE_CHANGED,
            thread_id=run.thread_id,
            run_id=run.id,
            payload={
                "collaboration_run_id": run.id,
                "from_state": previous.value,
                "state": current.value,
                "round": run.round,
                "reason": reason[:200],
            },
        )
    )


async def advance(
    db: AsyncSession,
    run: CollaborationRun,
    target: CollaborationState,
    *,
    reason: str,
) -> CollaborationRun:
    """Persist one legal transition, audit it, and broadcast its summary."""

    previous = CollaborationState(run.state)
    resolved = transition(previous, target)
    budget = _budget(run)
    if resolved in SUSPENDED_STATES and previous not in SUSPENDED_STATES:
        budget["resume_state"] = previous.value
        run.budget_json = json.dumps(budget, ensure_ascii=False)
    run.state = resolved.value
    if resolved in TERMINAL_STATES:
        run.completed_at = datetime.now(timezone.utc)
    db.add(
        build_state_transition_audit(
            run.workspace_id,
            run.thread_id,
            StateTransitionDetail(
                run_id=run.id,
                from_state=previous,
                to_state=resolved,
                round=run.round,
                reason=reason,
            ),
        )
    )
    await db.flush()
    await _publish_stage(run, previous, resolved, reason)
    return run


async def start_run(
    db: AsyncSession,
    workspace: Workspace,
    thread: Thread,
    *,
    decision: RoutingDecision,
) -> CollaborationRun:
    """Create a run and stop for clarification when its contract is incomplete."""

    contract = await db.scalar(
        select(TaskContract).where(TaskContract.thread_id == thread.id)
    )
    acceptance: list[object] = []
    if contract:
        try:
            parsed = json.loads(contract.acceptance or "[]")
            acceptance = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            acceptance = []
    ready = bool(contract and contract.goal.strip() and acceptance)
    run = CollaborationRun(
        workspace_id=workspace.id,
        thread_id=thread.id,
        mode="smart",
        state=CollaborationState.DRAFT.value,
        current_agent=(
            "claude" if decision.primary_agent.startswith("Claude") else "codex"
        ),
        round=1,
        max_rounds=3,
        budget_json=json.dumps(
            {
                "category": decision.category,
                "collaborator": decision.collaborator,
            },
            ensure_ascii=False,
        ),
    )
    db.add(run)
    await db.flush()
    if ready:
        await advance(
            db,
            run,
            CollaborationState.READY,
            reason="任务契约已满足启动条件",
        )
        return run

    await advance(
        db,
        run,
        CollaborationState.CLARIFYING,
        reason="任务契约缺少目标或验收标准",
    )
    message = Message(
        thread_id=thread.id,
        role="system",
        content="智能协作需要先补全任务目标和至少一条验收标准。",
    )
    db.add(message)
    await db.flush()
    await manager.publish(
        AgentEvent(
            type=EventType.MESSAGE_CREATED,
            thread_id=thread.id,
            run_id=run.id,
            payload={
                "id": message.id,
                "role": "system",
                "content": message.content,
            },
        )
    )
    await advance(
        db,
        run,
        CollaborationState.WAITING_USER,
        reason="等待用户补全任务契约",
    )
    return run


async def resume(
    db: AsyncSession, run: CollaborationRun, *, reason: str
) -> CollaborationRun:
    current = CollaborationState(run.state)
    if current not in SUSPENDED_STATES:
        raise ValueError("当前协作任务不处于可恢复的挂起状态")
    value = _budget(run).get("resume_state")
    try:
        target = CollaborationState(str(value))
    except ValueError as exc:
        raise ValueError("协作任务缺少有效的挂起前状态") from exc
    return await advance(db, run, target, reason=reason)


async def cancel(
    db: AsyncSession,
    run: CollaborationRun,
    *,
    reason: str,
    cancel_agent: Callable[[str], Awaitable[None] | None] | None = None,
) -> CollaborationRun:
    if CollaborationState(run.state) in TERMINAL_STATES:
        raise ValueError("协作任务已经结束")
    if cancel_agent:
        result = cancel_agent(run.thread_id)
        if inspect.isawaitable(result):
            await result
    return await advance(
        db, run, CollaborationState.CANCELLED, reason=reason
    )


async def recover_interrupted_runs(db: AsyncSession) -> list[str]:
    """Mark interrupted running stages blocked without replaying side effects."""

    rows = (
        await db.scalars(
            select(CollaborationRun).where(
                CollaborationRun.state.in_(
                    [state.value for state in RUNNING_STATES]
                )
            )
        )
    ).all()
    recovered: list[str] = []
    for run in rows:
        run.error = "应用重启中断"
        await advance(
            db,
            run,
            CollaborationState.BLOCKED,
            reason="应用重启中断",
        )
        recovered.append(run.id)
    return recovered

