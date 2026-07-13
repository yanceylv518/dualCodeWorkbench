import asyncio
import json
from pathlib import Path
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from .approvals import approval_gate
from .connections import manager
from .database import get_session
from .events import AgentEvent, EventType
from .execution_jobs import decide_job
from .git_service import GitError
from .governance_defaults import (
    DEFAULT_DELIVERABLES,
    DEFAULT_PROJECT_RULES,
    PRODUCT_RULE,
    recommended_deliverables,
    recommended_rules,
)
from .models import (
    AgentSession,
    AgentRun,
    Approval,
    AuditLog,
    FileChange,
    HandoffPackage,
    Message,
    ProjectGovernance,
    TestRun,
    Thread,
    TaskContract,
    Workspace,
)
from .scheduler import scheduler
from .schemas import ApprovalDecision, GovernanceUpdate, HandoffCreate, TaskContractUpdate

from .api_jobs import _execute_retry_job
from .api_runtime import git_tasks as _git_tasks, json_list as _json_list
from .handoff_prompt import handoff_prompt as _handoff_prompt

router = APIRouter(prefix="/api")

def _workspace_query():
    return select(Workspace).options(
        selectinload(Workspace.threads).selectinload(Thread.messages).selectinload(Message.attachments)
    )


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/approvals")
async def list_approvals(
    workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    items = (
        await db.scalars(
            select(Approval)
            .where(Approval.thread_id == thread_id, Approval.status == "PENDING")
            .order_by(Approval.id)
        )
    ).all()
    return [
        {"id": item.id, "action": item.action, "reason": item.reason, "status": item.status}
        for item in items
    ]


async def _handoff_payload(db: AsyncSession, workspace: Workspace, thread_id: str) -> dict[str, object]:
    governance = await db.scalar(select(ProjectGovernance).where(ProjectGovernance.workspace_id == workspace.id))
    contract = await db.scalar(select(TaskContract).where(TaskContract.thread_id == thread_id))
    changes = (await db.scalars(select(FileChange).where(FileChange.thread_id == thread_id))).all()
    tests = (await db.scalars(select(TestRun).where(TestRun.thread_id == thread_id))).all()
    repository = await scheduler._git.repository_status(Path(workspace.path))
    return {
        "contract": {
            "product_goal": governance.product_goal if governance else "",
            "product_boundary": governance.product_boundary if governance else "",
            "project_rules": recommended_rules(_json_list(governance.rules)) if governance else DEFAULT_PROJECT_RULES,
            "deliverables": recommended_deliverables(_json_list(governance.deliverables)) if governance else DEFAULT_DELIVERABLES,
            "task_goal": contract.goal if contract else "", "non_goals": _json_list(contract.non_goals) if contract else [],
            "acceptance": _json_list(contract.acceptance) if contract else [], "constraints": _json_list(contract.constraints) if contract else [],
            "known_risks": _json_list(contract.risks) if contract else [], "status": contract.status if contract else "DRAFT",
        },
        "repository": {"branch": repository["branch"], "head": repository["head"], "upstream": repository["upstream"],
                       "changed_files": [item.path for item in changes]},
        "diff": changes[0].diff[:200_000] if changes else "",
        "tests": [{"command": item.command, "exit_code": item.exit_code, "output": item.output[-20_000:]} for item in tests[-10:]],
    }


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/handoffs", status_code=201)
async def prepare_handoff(workspace_id: str, thread_id: str, body: HandoffCreate, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    if not workspace or not thread:
        raise HTTPException(404, "项目与任务不匹配")
    try:
        payload = await _handoff_payload(db, workspace, thread_id)
    except GitError as exc:
        raise HTTPException(400, f"无法生成交接包：{exc}") from exc
    item = HandoffPackage(workspace_id=workspace_id, thread_id=thread_id, recipient=body.recipient,
                          purpose=body.purpose, payload=json.dumps(payload, ensure_ascii=False))
    db.add(item)
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="handoff.prepared",
                    detail=f"recipient={body.recipient};purpose={body.purpose}"))
    await db.commit()
    return {"id": item.id, "recipient": item.recipient, "purpose": item.purpose, "status": item.status, "payload": payload}


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/handoffs")
async def list_handoffs(workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)):
    items = (await db.scalars(select(HandoffPackage).where(HandoffPackage.workspace_id == workspace_id,
                                                           HandoffPackage.thread_id == thread_id)
                              .order_by(HandoffPackage.created_at.desc()).limit(20))).all()
    return [{"id": item.id, "recipient": item.recipient, "purpose": item.purpose, "status": item.status,
             "payload": json.loads(item.payload), "created_at": item.created_at.isoformat()} for item in items]


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/handoffs/{handoff_id}/send", status_code=202)
async def send_handoff(workspace_id: str, thread_id: str, handoff_id: str, db: AsyncSession = Depends(get_session)):
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    item = await db.scalar(select(HandoffPackage).where(HandoffPackage.id == handoff_id,
                                                        HandoffPackage.workspace_id == workspace_id,
                                                        HandoffPackage.thread_id == thread_id))
    if not thread or not item:
        raise HTTPException(404, "未找到交接包")
    if item.status != "PREPARED":
        raise HTTPException(409, "交接包已经发送")
    run_id = await scheduler.start(thread_id, _handoff_prompt(item), item.recipient, [])
    item.status = "SENT"
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="handoff.sent",
                    detail=f"handoff={item.id};recipient={item.recipient};run={run_id}"))
    await db.commit()
    return {"run_id": run_id, "status": item.status}


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/contract")
async def get_contract(workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)):
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    governance = await db.scalar(select(ProjectGovernance).where(ProjectGovernance.workspace_id == workspace_id))
    if not governance:
        governance = ProjectGovernance(workspace_id=workspace_id, rules=json.dumps(DEFAULT_PROJECT_RULES, ensure_ascii=False), deliverables=json.dumps(DEFAULT_DELIVERABLES, ensure_ascii=False))
        db.add(governance)
    else:
        stored_rules = _json_list(governance.rules)
        stored_deliverables = _json_list(governance.deliverables)
        migrated_rules = recommended_rules(stored_rules)
        migrated_deliverables = recommended_deliverables(stored_deliverables)
        if migrated_rules != stored_rules:
            governance.rules = json.dumps(migrated_rules, ensure_ascii=False)
        if migrated_deliverables != stored_deliverables:
            governance.deliverables = json.dumps(migrated_deliverables, ensure_ascii=False)
    contract = await db.scalar(select(TaskContract).where(TaskContract.thread_id == thread_id))
    if not contract:
        contract = TaskContract(thread_id=thread_id)
        db.add(contract)
    await db.commit()
    rules = _json_list(governance.rules)
    acceptance = _json_list(contract.acceptance)
    return {
        "governance": {"product_goal": governance.product_goal, "product_boundary": governance.product_boundary,
                       "rules": rules, "deliverables": _json_list(governance.deliverables)},
        "task": {"goal": contract.goal, "non_goals": _json_list(contract.non_goals),
                 "acceptance": acceptance, "constraints": _json_list(contract.constraints),
                 "risks": _json_list(contract.risks), "status": contract.status},
        "gate": {"ready_for_implementation": bool(governance.product_goal.strip() and contract.goal.strip() and acceptance and PRODUCT_RULE in rules),
                 "missing": [label for valid, label in [
                     (bool(governance.product_goal.strip()), "产品目标"), (bool(contract.goal.strip()), "任务目标"),
                     (bool(acceptance), "验收标准"), (PRODUCT_RULE in rules, "产品级实现原则")
                 ] if not valid]},
    }


@router.put("/workspaces/{workspace_id}/governance")
async def update_governance(workspace_id: str, body: GovernanceUpdate, db: AsyncSession = Depends(get_session)):
    if not await db.get(Workspace, workspace_id):
        raise HTTPException(404, "未找到指定项目")
    item = await db.scalar(select(ProjectGovernance).where(ProjectGovernance.workspace_id == workspace_id))
    if not item:
        item = ProjectGovernance(workspace_id=workspace_id)
        db.add(item)
    rules = list(dict.fromkeys([PRODUCT_RULE, *[rule.strip() for rule in body.rules if rule.strip()]]))
    item.product_goal, item.product_boundary = body.product_goal.strip(), body.product_boundary.strip()
    item.rules = json.dumps(rules, ensure_ascii=False)
    item.deliverables = json.dumps([value.strip() for value in body.deliverables if value.strip()], ensure_ascii=False)
    db.add(AuditLog(workspace_id=workspace_id, event="governance.updated", detail=f"rules={len(rules)}"))
    await db.commit()
    return {"status": "saved"}


@router.put("/workspaces/{workspace_id}/threads/{thread_id}/contract")
async def update_contract(workspace_id: str, thread_id: str, body: TaskContractUpdate, db: AsyncSession = Depends(get_session)):
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    item = await db.scalar(select(TaskContract).where(TaskContract.thread_id == thread_id))
    if not item:
        item = TaskContract(thread_id=thread_id)
        db.add(item)
    item.goal, item.status = body.goal.strip(), body.status
    for name in ("non_goals", "acceptance", "constraints", "risks"):
        setattr(item, name, json.dumps([value.strip() for value in getattr(body, name) if value.strip()], ensure_ascii=False))
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="task.contract.updated", detail=f"status={body.status};acceptance={len(body.acceptance)}"))
    await db.commit()
    return {"status": "saved"}


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/details")
async def thread_details(
    workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    changes = (
        await db.scalars(select(FileChange).where(FileChange.thread_id == thread_id))
    ).all()
    tests = (
        await db.scalars(select(TestRun).where(TestRun.thread_id == thread_id))
    ).all()
    session = await db.scalar(
        select(AgentSession)
        .where(AgentSession.thread_id == thread_id, AgentSession.agent == "codex")
        .order_by(AgentSession.id.desc())
    )
    runs = (await db.scalars(
        select(AgentRun).where(AgentRun.thread_id == thread_id).order_by(AgentRun.id.desc()).limit(20)
    )).all()
    return {
        "files": [{"path": item.path} for item in changes],
        "diff": changes[0].diff if changes else "",
        "tests": [
            {
                "command": item.command,
                "output": item.output,
                "exit_code": item.exit_code,
            }
            for item in tests
        ],
        "worktree": session.workspace_path if session else "",
        "codex_session_id": session.external_session_id if session else "",
        "runs": [
            {"id": item.id, "agent": item.agent, "state": item.state.value, "output": item.output[:2000],
             "can_undo": item.agent == "codex" and bool(item.after_diff) and item.after_diff != item.before_diff}
            for item in runs
        ],
    }


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/approvals/{approval_id}")
async def decide_approval(
    workspace_id: str,
    thread_id: str,
    approval_id: str,
    body: ApprovalDecision,
    db: AsyncSession = Depends(get_session),
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    approval = await db.scalar(
        select(Approval).where(
            Approval.id == approval_id,
            Approval.thread_id == thread_id,
            Approval.status == "PENDING",
        )
    )
    if not thread or not approval:
        raise HTTPException(404, "未找到待处理审批")
    approval.status = "APPROVED" if body.approved else "REJECTED"
    if body.approved and body.scope == "thread" and approval.action in {"edit_files", "remote_edit_files"}:
        scheduler.grant_for_thread(thread_id, approval.action)
    job = await decide_job(db, approval.id, body.approved)
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event="approval.decided",
            detail=f"{approval.id}:{approval.status}:scope={body.scope}:{body.note}",
        )
    )
    await db.commit()
    delivered = approval_gate.resolve(approval.id, body.approved)
    if body.approved and job and job.status == "READY" and not delivered:
        # Approval state is durable, so execution must not depend solely on an
        # in-memory waiter surviving. Claiming the READY job is atomic; if the
        # original waiter resumes concurrently, exactly one executor wins.
        task = asyncio.create_task(_execute_retry_job(job.id))
        _git_tasks.add(task)
        task.add_done_callback(_git_tasks.discard)
    await manager.publish(
        AgentEvent(
            type=EventType.APPROVAL_DECIDED,
            thread_id=thread_id,
            payload={"id": approval.id, "approved": body.approved},
        )
    )
    return {"status": approval.status, "delivered": delivered}


