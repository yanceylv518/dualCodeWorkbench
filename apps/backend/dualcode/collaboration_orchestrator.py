"""Persistent lifecycle service for deterministic smart collaboration runs."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
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
    ReviewFinding,
    TaskContract,
    Thread,
    Workspace,
)
from .task_classifier import RoutingDecision
from .review_findings import persist_review_findings
from .review_parser import parse_review

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


@dataclass(frozen=True)
class SnapshotEvidence:
    base_sha: str
    snapshot_sha: str


@dataclass(frozen=True)
class ReviewTurnResult:
    raw_text: str
    source_handoff_id: str


@dataclass(frozen=True)
class StageCallbacks:
    """Injected side effects; the orchestrator owns only deterministic policy."""

    run_codex: Callable[[str, CollaborationState], Awaitable[object]]
    run_tests: Callable[[], Awaitable[bool | None]]
    sync_snapshot: Callable[[], Awaitable[SnapshotEvidence]]
    run_review: Callable[[str, SnapshotEvidence], Awaitable[ReviewTurnResult]]
    record_system: Callable[[str], Awaitable[None]]


_REVIEW_SUFFIX = """

After the natural-language review, output exactly one fenced ```json object
matching review.v1. Use Chinese finding descriptions. Required shape:
{"schema":"review.v1","verdict":"pass|blocking|needs_user","summary":"...",
"findings":[{"id":"F-1","type":"missing|partial|regression|risk|architecture|evidence",
"severity":"blocking|advisory","file":null,"line":null,
"description":"...","acceptance":"..."}]}
""".strip()


def _turn_failure(result: object) -> str:
    status = str(getattr(result, "status", "failed"))
    if status == "completed":
        return ""
    return str(getattr(result, "error", "") or f"Agent 轮次未完成（{status}）")


def _finding_key(item: object) -> tuple[str, str, str, str, str]:
    return (
        str(getattr(item, "type", "")),
        str(getattr(item, "file", "") or ""),
        str(getattr(item, "line", "") or ""),
        str(getattr(item, "description", "")).strip(),
        str(getattr(item, "acceptance", "")).strip(),
    )


async def _open_blocking_findings(
    db: AsyncSession, run: CollaborationRun
) -> list[ReviewFinding]:
    return list(
        (
            await db.scalars(
                select(ReviewFinding)
                .where(
                    ReviewFinding.collaboration_run_id == run.id,
                    ReviewFinding.status == "open",
                    ReviewFinding.severity == "blocking",
                )
                .order_by(
                    ReviewFinding.type,
                    ReviewFinding.file,
                    ReviewFinding.line,
                    ReviewFinding.id,
                )
            )
        ).all()
    )


def _fix_prompt(findings: list[ReviewFinding]) -> str:
    rows = ["修复以下阻断审查问题。不得使用临时、演示或绕过方案："]
    for index, item in enumerate(findings, 1):
        location = item.file or "未指定文件"
        if item.line:
            location += f":{item.line}"
        rows.extend(
            [
                f"{index}. [{item.type}] {location}",
                f"   问题：{item.description}",
                f"   验收：{item.acceptance}",
            ]
        )
    return "\n".join(rows)


async def _resolve_absent_findings(
    db: AsyncSession,
    run: CollaborationRun,
    review_findings: list[object],
) -> None:
    """Resolve prior findings that the next independent review no longer reports."""

    current_keys = {_finding_key(item) for item in review_findings}
    old = await _open_blocking_findings(db, run)
    for item in old:
        if item.round < run.round and _finding_key(item) not in current_keys:
            item.status = "resolved"
            item.resolved_by_snapshot_sha = run.snapshot_sha


async def execute_pipeline(
    db: AsyncSession,
    run: CollaborationRun,
    *,
    initial_prompt: str,
    callbacks: StageCallbacks,
) -> CollaborationRun:
    """Execute one C5 collaboration loop using injected, already-approved effects."""

    if CollaborationState(run.state) is CollaborationState.READY:
        await advance(
            db, run, CollaborationState.IMPLEMENTING, reason="开始 Codex 实现轮次"
        )

    while CollaborationState(run.state) in {
        CollaborationState.IMPLEMENTING,
        CollaborationState.VERIFYING,
        CollaborationState.SYNCING_REVIEW_SNAPSHOT,
        CollaborationState.REVIEWING,
        CollaborationState.CHANGES_REQUESTED,
        CollaborationState.FIXING,
        CollaborationState.ACCEPTED,
    }:
        state = CollaborationState(run.state)
        if state is CollaborationState.IMPLEMENTING:
            turn = await callbacks.run_codex(initial_prompt, state)
            if error := _turn_failure(turn):
                run.error = error
                return await advance(db, run, CollaborationState.BLOCKED, reason=error)
            await advance(db, run, CollaborationState.VERIFYING, reason="实现轮次完成")
        elif state is CollaborationState.VERIFYING:
            verified = await callbacks.run_tests()
            if verified is None:
                await callbacks.record_system("尚未配置测试命令，本轮未生成测试证据。")
            elif not verified:
                run.error = "验证失败，请检查测试输出"
                return await advance(
                    db, run, CollaborationState.BLOCKED, reason=run.error
                )
            await advance(
                db,
                run,
                CollaborationState.SYNCING_REVIEW_SNAPSHOT,
                reason="验证阶段完成",
            )
        elif state is CollaborationState.SYNCING_REVIEW_SNAPSHOT:
            try:
                snapshot = await callbacks.sync_snapshot()
            except Exception as exc:
                run.error = f"审查快照同步失败：{str(exc)[:240]}"
                return await advance(
                    db, run, CollaborationState.BLOCKED, reason=run.error
                )
            run.base_sha = snapshot.base_sha
            run.snapshot_sha = snapshot.snapshot_sha
            await advance(
                db, run, CollaborationState.REVIEWING, reason="审查快照已同步"
            )
        elif state is CollaborationState.REVIEWING:
            snapshot = SnapshotEvidence(run.base_sha or "", run.snapshot_sha or "")
            review_turn = await callbacks.run_review(_REVIEW_SUFFIX, snapshot)
            parsed = parse_review(review_turn.raw_text)
            if parsed.outcome != "parsed" or parsed.review is None:
                await callbacks.record_system(review_turn.raw_text)
                return await advance(
                    db,
                    run,
                    CollaborationState.WAITING_USER,
                    reason=f"审查裁决解析失败：{parsed.outcome}",
                )
            review = parsed.review
            await _resolve_absent_findings(db, run, list(review.findings))
            await persist_review_findings(
                db,
                workspace_id=run.workspace_id,
                thread_id=run.thread_id,
                source_handoff_id=review_turn.source_handoff_id,
                review=review,
                collaboration_run_id=run.id,
                round=run.round,
            )
            if review.verdict == "needs_user":
                await callbacks.record_system(review_turn.raw_text)
                return await advance(
                    db, run, CollaborationState.WAITING_USER, reason=review.summary
                )
            if review.verdict == "blocking":
                await advance(
                    db,
                    run,
                    CollaborationState.CHANGES_REQUESTED,
                    reason=review.summary,
                )
            else:
                await advance(
                    db, run, CollaborationState.ACCEPTED, reason=review.summary
                )
        elif state is CollaborationState.CHANGES_REQUESTED:
            await advance(
                db, run, CollaborationState.FIXING, reason="编译阻断问题整改提示"
            )
        elif state is CollaborationState.FIXING:
            findings = await _open_blocking_findings(db, run)
            turn = await callbacks.run_codex(_fix_prompt(findings), state)
            if error := _turn_failure(turn):
                run.error = error
                return await advance(db, run, CollaborationState.BLOCKED, reason=error)
            run.round += 1
            await advance(db, run, CollaborationState.VERIFYING, reason="整改轮次完成")
        else:
            await callbacks.record_system(
                f"智能协作已完成，共经过 {run.round} 轮审查。"
            )
            await advance(
                db, run, CollaborationState.COMPLETED, reason="审查已通过"
            )
    return run


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
