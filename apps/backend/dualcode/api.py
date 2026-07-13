import asyncio
import io
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from PIL import Image
from .config import settings
from .cli_adapters import ClaudeCliAdapter
from .codex_app_server import CodexAppServerAdapter
from .approvals import approval_gate
from .connections import manager
from .database import SessionLocal, get_session
from .events import AgentEvent, EventType
from .execution_jobs import claim_job, create_job, decide_job, decode_json_object, mark_job, record_job_evidence, request_retry
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
    Attachment,
    AuditLog,
    FileChange,
    HandoffPackage,
    ExecutionJob,
    Message,
    ProjectGovernance,
    RunState,
    TestRun,
    Thread,
    TaskContract,
    Workspace,
)
from .scheduler import scheduler
from .schemas import ApprovalDecision, GitActionCreate, GovernanceUpdate, HandoffCreate, MessageCreate, RemoteGitActionCreate, TaskContractUpdate, ThreadCreate, ThreadUpdate, WorkspaceCreate, WorkspaceProvision, WorkspaceRead
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig, RemoteRepositoryUnavailable
from .test_executor import TestCommand
from .runtime_settings import AgentSettings, agent_settings_store
from .workspace_remote import WorkspaceRemoteSettings, derived_repository_path, workspace_remote_store

# Compatibility name retained for integrations that patch the health adapter.
CodexCliAdapter = CodexAppServerAdapter
router = APIRouter(prefix="/api")
_thread_create_locks: dict[str, asyncio.Lock] = {}
_git_tasks: set[asyncio.Task[None]] = set()
def _json_list(value: str) -> list[str]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


async def _persist_evidence(approval_id: str, phase: str, value: dict[str, object]) -> None:
    async with SessionLocal() as evidence_db:
        await record_job_evidence(evidence_db, approval_id, phase, value)
        await evidence_db.commit()


async def _local_git_evidence(repository: Path, *, verified: bool = False) -> dict[str, object]:
    status = await scheduler._git.repository_status(repository)
    return {"verified": verified, "head": status["head"], "branch": status["branch"],
            "upstream": status["upstream"], "ahead": status["ahead"], "behind": status["behind"],
            "remote": status["remote"]}


def _job_response(job: ExecutionJob) -> dict[str, object]:
    evidence = decode_json_object(job.evidence)
    safe_evidence: dict[str, object] = {}
    for phase in ("before", "after"):
        snapshot = evidence.get(phase)
        if isinstance(snapshot, dict):
            safe_evidence[phase] = {key: snapshot[key] for key in (
                "verified", "head", "branch", "upstream", "ahead", "behind"
            ) if key in snapshot}
    error = ""
    if job.last_error:
        if job.kind == "remote_git":
            # Remote Git stderr is required for an actionable UI, but redact
            # credentials if a user accidentally configured an authenticated
            # HTTPS URL and cap untrusted process output.
            error = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", job.last_error).strip()[:800]
        else:
            error = "Execution failed; inspect the terminal and repository state before retrying"
    return {
        "id": job.id, "approval_id": job.approval_id,
        "workspace_id": job.workspace_id, "thread_id": job.thread_id,
        "kind": job.kind, "payload": {"action": decode_json_object(job.payload).get("action")},
        "status": job.status, "attempts": job.attempts,
        "last_error": error,
        "evidence": safe_evidence, "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


async def _execute_retry_job(job_id: str) -> None:
    """Claim and execute an explicitly retried durable job."""
    async with SessionLocal() as db:
        job = await claim_job(db, job_id)
        if not job:
            return
        payload = decode_json_object(job.payload)
        workspace_id, thread_id, approval_id = job.workspace_id, job.thread_id, job.approval_id
        kind = job.kind
    output = ""
    succeeded = False
    event = "execution.failed"
    try:
        if kind == "git_action":
            repository = Path(str(payload["repository"]))
            action = str(payload["action"])
            await _persist_evidence(approval_id, "before", await _local_git_evidence(repository))
            if action == "commit":
                output = await scheduler._git.commit_all(repository, str(payload.get("message", "")))
            elif action == "push":
                output = await scheduler._git.push(repository)
            elif action == "pull":
                output = await scheduler._git.pull_ff_only(repository)
            else:
                raise ValueError(f"Unsupported Git action: {action}")
            await _persist_evidence(approval_id, "after", await _local_git_evidence(repository, verified=True))
            event = "git.action.completed"
        elif kind == "test_run":
            repository = Path(str(payload["repository"]))
            command = TestCommand(executable=Path(str(payload["executable"])),
                                  arguments=tuple(str(item) for item in payload.get("arguments", [])), cwd=repository)
            async def terminal(channel: str, text: str) -> None:
                await manager.publish(AgentEvent(type=EventType.TERMINAL_OUTPUT, thread_id=thread_id,
                                                 payload={"channel": channel, "text": text}))
            result = await scheduler._tests.execute(command, repository, terminal)
            output = result.stdout + result.stderr
            async with SessionLocal() as db:
                db.add(TestRun(thread_id=thread_id, command=" ".join(result.command), output=output,
                               exit_code=result.exit_code))
                await db.commit()
            if result.exit_code != 0:
                raise RuntimeError(f"Tests exited with code {result.exit_code}")
            event = "test.completed"
        elif kind == "remote_git":
            runtime = agent_settings_store.load()
            adapter = ClaudeSshAdapter(ClaudeSshConfig(
                host=runtime.claude_ssh_host, username=runtime.claude_ssh_username,
                port=runtime.claude_ssh_port, known_hosts=Path(runtime.claude_ssh_known_hosts),
                client_keys=(Path(runtime.claude_ssh_client_key),) if runtime.claude_ssh_client_key else (),
                remote_root=PurePosixPath(runtime.claude_remote_root),
                claude_executable=PurePosixPath(runtime.claude_ssh_executable),
                model=runtime.claude_model, reasoning_effort=runtime.claude_reasoning_effort))
            remote_repository = PurePosixPath(str(payload["repository"]))
            remote_action = str(payload["action"])
            if remote_action not in {"provision", "repair_provision"}:
                await _persist_evidence(approval_id, "before", await adapter.repository_status(remote_repository))
            remote_url = str(payload.get("remote_url") or workspace_remote_store.get(workspace_id).remote_url)
            output = await adapter.repository_update(remote_repository, remote_action, remote_url)
            after = await adapter.repository_status(remote_repository)
            after["verified"] = True
            await _persist_evidence(approval_id, "after", after)
            event = "remote.git.completed"
        else:
            raise ValueError(f"Unsupported execution job kind: {kind}")
        succeeded = True
    except Exception as exc:
        output = str(exc)
    async with SessionLocal() as db:
        await mark_job(db, approval_id, "SUCCEEDED" if succeeded else "FAILED", "" if succeeded else output)
        db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event=event if succeeded else "execution.failed",
                        detail=f"job={job_id};retry=true;success={str(succeeded).lower()}"))
        await db.commit()
    await manager.publish(AgentEvent(type=EventType.RUN_OUTPUT if succeeded else EventType.ERROR,
                                     thread_id=thread_id, payload={"kind": kind, "success": succeeded,
                                                                   "output": output, "job_id": job_id}))


def _schedule_retry(job_id: str) -> None:
    task = asyncio.create_task(_execute_retry_job(job_id))
    _git_tasks.add(task)
    task.add_done_callback(_git_tasks.discard)


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/jobs")
async def list_execution_jobs(workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)):
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    if not thread:
        raise HTTPException(404, "未找到指定项目或任务")
    jobs = (await db.scalars(select(ExecutionJob).where(
        ExecutionJob.workspace_id == workspace_id, ExecutionJob.thread_id == thread_id
    ).order_by(ExecutionJob.created_at.desc()))).all()
    return [_job_response(job) for job in jobs]


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/jobs/{job_id}")
async def get_execution_job(workspace_id: str, thread_id: str, job_id: str,
                            db: AsyncSession = Depends(get_session)):
    job = await db.scalar(select(ExecutionJob).where(
        ExecutionJob.id == job_id, ExecutionJob.workspace_id == workspace_id,
        ExecutionJob.thread_id == thread_id))
    if not job:
        raise HTTPException(404, "未找到执行任务")
    return _job_response(job)


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/jobs/{job_id}/retry", status_code=202)
async def retry_execution_job(workspace_id: str, thread_id: str, job_id: str,
                              db: AsyncSession = Depends(get_session)):
    job = await db.scalar(select(ExecutionJob).where(
        ExecutionJob.id == job_id, ExecutionJob.workspace_id == workspace_id,
        ExecutionJob.thread_id == thread_id))
    if not job:
        raise HTTPException(404, "未找到执行任务")
    try:
        changed = await request_retry(db, job)
    except ValueError as exc:
        raise HTTPException(409, f"无法重试执行任务：{exc}") from exc
    await db.commit()
    if changed:
        _schedule_retry(job.id)
    return {"job_id": job.id, "status": job.status, "scheduled": changed}


@router.get("/workspaces/{workspace_id}/git/status")
async def workspace_git_status(workspace_id: str, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "未找到指定项目")
    try:
        return await scheduler._git.repository_status(Path(workspace.path))
    except (GitError, OSError, ValueError) as exc:
        raise HTTPException(400, f"无法保存 VPS 仓库配置：{exc}") from exc


@router.get("/workspaces/{workspace_id}/remote")
async def workspace_remote_status(workspace_id: str, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "未找到指定项目")
    value = workspace_remote_store.get(workspace_id)
    runtime = agent_settings_store.load()
    if not value.vps_repo_path:
        value = value.model_copy(update={"vps_repo_path": derived_repository_path(runtime.claude_ssh_projects_root, value.remote_url or "", workspace.name)})
    local = await scheduler._git.repository_status(Path(workspace.path))
    result: dict[str, object] = {"settings": value.model_dump(), "local": local, "vps": None, "same_remote": False, "same_commit": False}
    if not value.vps_repo_path:
        return result
    if not (runtime.claude_ssh_enabled and runtime.claude_ssh_host and runtime.claude_ssh_username and runtime.claude_ssh_known_hosts):
        return result
    adapter = ClaudeSshAdapter(ClaudeSshConfig(
        host=runtime.claude_ssh_host,
        username=runtime.claude_ssh_username,
        port=runtime.claude_ssh_port,
        known_hosts=Path(runtime.claude_ssh_known_hosts),
        client_keys=(Path(runtime.claude_ssh_client_key),) if runtime.claude_ssh_client_key else (),
        remote_root=PurePosixPath(runtime.claude_remote_root),
        claude_executable=PurePosixPath(runtime.claude_ssh_executable),
        model=runtime.claude_model,
        reasoning_effort=runtime.claude_reasoning_effort,
    ))
    try:
        remote = await adapter.repository_status(PurePosixPath(value.vps_repo_path))
        result["vps"] = remote
        expected_remote = value.remote_url or str(local.get("remote", ""))
        def normalize(item: object) -> str:
            return str(item).strip().lower().removesuffix(".git").rstrip("/")
        result["same_remote"] = bool(expected_remote) and normalize(expected_remote) == normalize(remote["remote"])
        result["same_commit"] = bool(local.get("head")) and str(local["head"]).lower() == remote["head"][:10].lower()
    except RemoteRepositoryUnavailable:
        result["state"] = "not_cloned"
    except Exception as exc:
        result["error"] = str(exc)
    return result


@router.put("/workspaces/{workspace_id}/remote", response_model=WorkspaceRemoteSettings)
async def update_workspace_remote(workspace_id: str, value: WorkspaceRemoteSettings, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "未找到指定项目")
    runtime = agent_settings_store.load()
    value = value.model_copy(update={"vps_repo_path": derived_repository_path(runtime.claude_ssh_projects_root, value.remote_url, workspace.name)})
    workspace_remote_store.save(workspace_id, value)
    db.add(AuditLog(workspace_id=workspace_id, thread_id=None, event="workspace.remote.updated", detail=f"remote={value.remote_url};vps_path={value.vps_repo_path}"))
    await db.commit()
    return value


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/remote/actions", status_code=202)
async def request_remote_git_action(
    workspace_id: str,
    thread_id: str,
    body: RemoteGitActionCreate,
    db: AsyncSession = Depends(get_session),
):
    workspace = await db.get(Workspace, workspace_id)
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    remote = workspace_remote_store.get(workspace_id)
    if not workspace or not thread:
        raise HTTPException(404, "未找到指定项目或任务")
    if not remote.vps_repo_path:
        raise HTTPException(400, "尚未配置 VPS 仓库路径")
    runtime = agent_settings_store.load()
    expected_repository = derived_repository_path(
        runtime.claude_ssh_projects_root,
        remote.remote_url,
        workspace.name,
    )
    if body.action == "repair_provision" and remote.vps_repo_path != expected_repository:
        raise HTTPException(400, "拒绝修复并非由当前项目自动生成的 VPS 路径")
    adapter = ClaudeSshAdapter(ClaudeSshConfig(
        host=runtime.claude_ssh_host,
        username=runtime.claude_ssh_username,
        port=runtime.claude_ssh_port,
        known_hosts=Path(runtime.claude_ssh_known_hosts),
        client_keys=(Path(runtime.claude_ssh_client_key),) if runtime.claude_ssh_client_key else (),
        remote_root=PurePosixPath(runtime.claude_remote_root),
        claude_executable=PurePosixPath(runtime.claude_ssh_executable),
        model=runtime.claude_model,
        reasoning_effort=runtime.claude_reasoning_effort,
    ))
    action_name = f"remote_git_{body.action}"
    reason = (
        "在 VPS 项目根目录中创建项目目录并克隆远程仓库"
        if body.action == "provision"
        else "删除当前项目自动派生目录中的无效残留内容，并重新克隆远程仓库；有效 Git 仓库不会被删除"
        if body.action == "repair_provision"
        else "在 VPS 仓库执行 git fetch --prune"
        if body.action == "fetch"
        else "在 VPS 仓库执行 git pull --ff-only，不自动合并"
    )
    # Clicking "clone to VPS" is the user's explicit authorization for this
    # one clone. Persist that decision for audit/recovery, but do not ask for
    # the same approval a second time. Fetch/pull retain the normal approval
    # workflow because they mutate an existing remote working copy.
    direct_authorization = body.action == "provision"
    active_jobs = (
        await db.scalars(
            select(ExecutionJob)
            .where(
                ExecutionJob.workspace_id == workspace_id,
                ExecutionJob.thread_id == thread_id,
                ExecutionJob.kind == "remote_git",
                ExecutionJob.status.in_(("WAITING_APPROVAL", "READY", "RUNNING")),
            )
            .order_by(ExecutionJob.created_at.desc())
        )
    ).all()
    existing = next(
        (item for item in active_jobs if decode_json_object(item.payload).get("action") == body.action),
        None,
    )
    if existing:
        return {
            "approval_id": existing.approval_id,
            "job_id": existing.id,
            "status": existing.status,
            "authorization": "existing_job",
            "reused": True,
        }
    if direct_authorization:
        stale_approvals = (
            await db.scalars(
                select(Approval).where(
                    Approval.thread_id == thread_id,
                    Approval.action == action_name,
                    Approval.status == "PENDING",
                )
            )
        ).all()
        for stale in stale_approvals:
            stale.status = "REJECTED"
            await decide_job(db, stale.id, False)
            db.add(AuditLog(
                workspace_id=workspace_id,
                thread_id=thread_id,
                event="approval.superseded",
                detail=f"approval={stale.id};action={action_name};reason=explicit_clone_click",
            ))
    approval = Approval(
        thread_id=thread_id,
        action=action_name,
        reason=reason,
        status="APPROVED" if direct_authorization else "PENDING",
    )
    db.add(approval)
    await db.flush()
    job = await create_job(db, approval=approval, workspace_id=workspace_id, kind="remote_git",
                           payload={"action": body.action, "repository": remote.vps_repo_path,
                                    "remote_url": remote.remote_url},
                           initial_status="READY" if direct_authorization else "WAITING_APPROVAL")
    if not direct_authorization:
        approval_gate.prepare(approval.id)
    db.add(AuditLog(
        workspace_id=workspace_id,
        thread_id=thread_id,
        event="remote.git.requested",
        detail=f"{body.action}:{approval.id};authorization={'explicit_click' if direct_authorization else 'pending'}",
    ))
    await db.commit()
    if not direct_authorization:
        await manager.publish(AgentEvent(type=EventType.APPROVAL_REQUIRED, thread_id=thread_id, payload={"id": approval.id, "action": action_name, "reason": reason}))

    async def execute_remote_action() -> None:
        if not direct_authorization and not await approval_gate.wait(approval.id):
            return
        await manager.publish(AgentEvent(type=EventType.RUN_OUTPUT, thread_id=thread_id, payload={"kind": "remote_git", "job_id": job.id, "action": body.action, "status": "RUNNING", "success": False, "output": "VPS 仓库操作正在执行"}))
        async with SessionLocal() as action_db:
            if not await claim_job(action_db, job.id):
                return
        event = "remote.git.completed"
        try:
            remote_repository = PurePosixPath(remote.vps_repo_path)
            if body.action not in {"provision", "repair_provision"}:
                await _persist_evidence(approval.id, "before", await adapter.repository_status(remote_repository))
            output = await adapter.repository_update(remote_repository, body.action, remote.remote_url)
            after = await adapter.repository_status(remote_repository)
            after["verified"] = True
            await _persist_evidence(approval.id, "after", after)
        except Exception as exc:
            event = "remote.git.failed"
            output = str(exc)
        async with SessionLocal() as action_db:
            await mark_job(action_db, approval.id, "SUCCEEDED" if event.endswith("completed") else "FAILED", "" if event.endswith("completed") else output)
            action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event=event,
                                   detail=f"action={body.action};success={str(event.endswith('completed')).lower()}"))
            await action_db.commit()
        await manager.publish(AgentEvent(type=EventType.RUN_OUTPUT, thread_id=thread_id, payload={"kind": "remote_git", "job_id": job.id, "action": body.action, "status": "SUCCEEDED" if event.endswith("completed") else "FAILED", "success": event.endswith("completed"), "output": output}))

    task = asyncio.create_task(execute_remote_action())
    _git_tasks.add(task)
    task.add_done_callback(_git_tasks.discard)
    return {
        "approval_id": approval.id,
        "job_id": job.id,
        "status": "READY" if direct_authorization else "PENDING",
        "authorization": "explicit_click" if direct_authorization else "approval_required",
    }


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/git/actions", status_code=202)
async def request_git_action(
    workspace_id: str,
    thread_id: str,
    body: GitActionCreate,
    db: AsyncSession = Depends(get_session),
):
    workspace = await db.get(Workspace, workspace_id)
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    if not workspace or not thread:
        raise HTTPException(404, "未找到指定项目或任务")
    reasons = {
        "commit": f"暂存当前全部变更并创建提交：{body.message.strip() or '(未填写提交说明)'}",
        "push": "将当前分支提交推送到已配置的远程仓库",
        "pull": "使用 fast-forward-only 拉取远程提交，不自动创建合并提交",
    }
    approval = Approval(thread_id=thread_id, action=f"git_{body.action}", reason=reasons[body.action])
    db.add(approval)
    await db.flush()
    job = await create_job(db, approval=approval, workspace_id=workspace_id, kind="git_action",
                           payload={"action": body.action, "message": body.message, "repository": workspace.path})
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="git.action.requested", detail=f"{body.action}:{approval.id}"))
    approval_gate.prepare(approval.id)
    repository_path = workspace.path
    await db.commit()
    await manager.publish(AgentEvent(type=EventType.APPROVAL_REQUIRED, thread_id=thread_id, payload={"id": approval.id, "action": approval.action, "reason": approval.reason}))

    async def execute_after_approval() -> None:
        approved = await approval_gate.wait(approval.id)
        if not approved:
            return
        async with SessionLocal() as action_db:
            if not await claim_job(action_db, job.id):
                return
        outcome = ""
        event = "git.action.completed"
        try:
            repository = Path(repository_path)
            await _persist_evidence(approval.id, "before", await _local_git_evidence(repository))
            if body.action == "commit":
                outcome = await scheduler._git.commit_all(repository, body.message)
            elif body.action == "push":
                outcome = await scheduler._git.push(repository)
            else:
                outcome = await scheduler._git.pull_ff_only(repository)
            await _persist_evidence(approval.id, "after", await _local_git_evidence(repository, verified=True))
        except Exception as exc:
            event = "git.action.failed"
            outcome = str(exc)
        async with SessionLocal() as action_db:
            await mark_job(action_db, approval.id, "SUCCEEDED" if event.endswith("completed") else "FAILED", "" if event.endswith("completed") else outcome)
            action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event=event,
                                   detail=f"action={body.action};success={str(event.endswith('completed')).lower()}"))
            await action_db.commit()
        await manager.publish(AgentEvent(type=EventType.RUN_OUTPUT, thread_id=thread_id, payload={"kind": "git_action", "action": body.action, "success": event.endswith("completed"), "output": outcome}))

    task = asyncio.create_task(execute_after_approval())
    _git_tasks.add(task)
    task.add_done_callback(_git_tasks.discard)
    return {"approval_id": approval.id, "status": "PENDING"}


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/tests/run", status_code=202)
async def request_test_run(workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    if not workspace or not thread:
        raise HTTPException(404, "未找到指定项目或任务")
    runtime = agent_settings_store.load()
    if not runtime.test_executable:
        raise HTTPException(400, "Agent 设置中尚未配置测试可执行文件")
    approval = Approval(thread_id=thread_id, action="run_test", reason=f"在本地仓库运行测试：{runtime.test_executable} {' '.join(runtime.test_arguments)}")
    db.add(approval)
    await db.flush()
    job = await create_job(db, approval=approval, workspace_id=workspace_id, kind="test_run",
                           payload={"executable": runtime.test_executable, "arguments": runtime.test_arguments,
                                    "repository": workspace.path})
    approval_gate.prepare(approval.id)
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="test.requested", detail=approval.reason))
    await db.commit()
    await manager.publish(AgentEvent(type=EventType.APPROVAL_REQUIRED, thread_id=thread_id, payload={"id": approval.id, "action": "run_test", "reason": approval.reason}))
    repository_path = workspace.path

    async def execute_after_approval() -> None:
        if not await approval_gate.wait(approval.id):
            return
        async with SessionLocal() as action_db:
            if not await claim_job(action_db, job.id):
                return
        async with SessionLocal() as action_db:
            current = await action_db.get(Thread, thread_id)
            if current:
                current.state = RunState.TESTING
                await action_db.commit()
        await manager.publish(AgentEvent(type=EventType.RUN_STATE_CHANGED, thread_id=thread_id, payload={"state": RunState.TESTING.value}))
        async def output(channel: str, text: str) -> None:
            await manager.publish(AgentEvent(type=EventType.TERMINAL_OUTPUT, thread_id=thread_id, payload={"channel": channel, "text": text}))
        try:
            result = await scheduler._tests.execute(
                TestCommand(executable=Path(runtime.test_executable), arguments=tuple(runtime.test_arguments), cwd=Path(repository_path)),
                Path(repository_path),
                output,
            )
            record = TestRun(thread_id=thread_id, command=" ".join(result.command), output=result.stdout + result.stderr, exit_code=result.exit_code)
            async with SessionLocal() as action_db:
                current = await action_db.get(Thread, thread_id)
                if current:
                    current.state = RunState.CREATED
                action_db.add(record)
                await mark_job(action_db, approval.id, "SUCCEEDED" if result.exit_code == 0 else "FAILED", "" if result.exit_code == 0 else f"exit_code={result.exit_code}")
                action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="test.completed", detail=f"exit_code={result.exit_code}"))
                await action_db.commit()
            await manager.publish(AgentEvent(type=EventType.TEST_RESULT, thread_id=thread_id, payload={"command": record.command, "output": record.output, "exit_code": record.exit_code}))
        except Exception as exc:
            async with SessionLocal() as action_db:
                current = await action_db.get(Thread, thread_id)
                if current:
                    current.state = RunState.CREATED
                await mark_job(action_db, approval.id, "FAILED", str(exc))
                action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="test.failed",
                                       detail="test execution failed; see terminal output"))
                await action_db.commit()
            await manager.publish(AgentEvent(type=EventType.ERROR, thread_id=thread_id, payload={"message": str(exc)}))
        await manager.publish(AgentEvent(type=EventType.RUN_STATE_CHANGED, thread_id=thread_id, payload={"state": RunState.CREATED.value}))

    task = asyncio.create_task(execute_after_approval())
    _git_tasks.add(task)
    task.add_done_callback(_git_tasks.discard)
    return {"approval_id": approval.id, "status": "PENDING"}


@router.get("/agents/models")
async def agent_models():
    """Return safe local model metadata without reading authentication material."""
    runtime = agent_settings_store.load()
    codex_models: list[dict[str, str]] = []
    cache = Path.home() / ".codex" / "models_cache.json"
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        for item in payload.get("models", []):
            slug = item.get("slug")
            if isinstance(slug, str) and slug:
                codex_models.append({
                    "id": slug,
                    "label": item.get("display_name") or slug,
                    "description": item.get("description") or "",
                    "default_reasoning": item.get("default_reasoning_level") or "medium",
                    "reasoning_levels": [level.get("effort") for level in item.get("supported_reasoning_levels", []) if level.get("effort")],
                })
    except (OSError, ValueError, TypeError):
        pass
    if runtime.codex_model and not any(item["id"] == runtime.codex_model for item in codex_models):
        codex_models.insert(0, {"id": runtime.codex_model, "label": runtime.codex_model, "description": "当前配置"})
    claude_models = [
        {"id": "fable", "label": "Fable 5"},
        {"id": "sonnet", "label": "Sonnet 5"},
        {"id": "haiku", "label": "Haiku 4.5"},
        {"id": "opus", "label": "Opus 4.8"},
        {"id": "claude-opus-4-7", "label": "Opus 4.7"},
        {"id": "claude-opus-4-6", "label": "Opus 4.6"},
        {"id": "claude-3-opus-20240229", "label": "Opus 3"},
        {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6"},
    ]
    current_claude = ""
    if runtime.claude_ssh_enabled and runtime.claude_ssh_host and runtime.claude_ssh_username and runtime.claude_ssh_known_hosts:
        try:
            remote_adapter = ClaudeSshAdapter(ClaudeSshConfig(
                host=runtime.claude_ssh_host,
                username=runtime.claude_ssh_username,
                port=runtime.claude_ssh_port,
                known_hosts=Path(runtime.claude_ssh_known_hosts),
                client_keys=(Path(runtime.claude_ssh_client_key),) if runtime.claude_ssh_client_key else (),
                remote_root=PurePosixPath(runtime.claude_remote_root),
                claude_executable=PurePosixPath(runtime.claude_ssh_executable),
                model=runtime.claude_model,
                reasoning_effort=runtime.claude_reasoning_effort,
            ))
            current_claude, _ = await remote_adapter.model_catalog()
        except Exception:  # Model discovery is optional; settings must remain usable offline.
            pass
    if runtime.claude_model and not any(item["id"] == runtime.claude_model for item in claude_models):
        claude_models.insert(0, {"id": runtime.claude_model, "label": runtime.claude_model})
    return {
        "codex": codex_models,
        "claude": [{**item, "description": f"VPS 当前默认：{current_claude}" if current_claude else "Claude 模型", "default_reasoning": "medium", "reasoning_levels": ["low", "medium", "high"]} for item in claude_models],
    }


@router.get("/agents/health")
async def agent_health():
    runtime = agent_settings_store.load()
    codex = CodexAppServerAdapter(runtime.codex_executable, model=runtime.codex_model, reasoning_effort=runtime.codex_reasoning_effort, permission_mode=runtime.codex_permission_mode)
    claude = ClaudeCliAdapter(runtime.claude_executable, model=runtime.claude_model, reasoning_effort=runtime.claude_reasoning_effort)
    codex_ok, claude_ok = await asyncio.gather(codex.health_check(), claude.health_check())
    remote: dict[str, object] = {"configured": False, "healthy": False, "vision": True}
    if (
        runtime.claude_ssh_enabled
        and runtime.claude_ssh_host
        and runtime.claude_ssh_username
        and runtime.claude_ssh_known_hosts
    ):
        remote_adapter = ClaudeSshAdapter(
            ClaudeSshConfig(
                host=runtime.claude_ssh_host,
                username=runtime.claude_ssh_username,
                port=runtime.claude_ssh_port,
                known_hosts=Path(runtime.claude_ssh_known_hosts),
                client_keys=(Path(runtime.claude_ssh_client_key),)
                if runtime.claude_ssh_client_key
                else (),
                remote_root=PurePosixPath(runtime.claude_remote_root),
                claude_executable=PurePosixPath(runtime.claude_ssh_executable),
                model=runtime.claude_model,
                reasoning_effort=runtime.claude_reasoning_effort,
            )
        )
        remote = {
            "configured": True,
            "healthy": await remote_adapter.health_check(),
            "vision": remote_adapter.capabilities.vision,
        }
    return {
        "real_agents_enabled": runtime.enable_real_agents,
        "codex": {"healthy": codex_ok, "vision": codex.capabilities.vision},
        "claude": {"healthy": claude_ok, "vision": claude.capabilities.vision},
        "claude_ssh": remote,
    }


@router.get("/settings/agents", response_model=AgentSettings)
async def get_agent_settings():
    return agent_settings_store.load()


@router.put("/settings/agents", response_model=AgentSettings)
async def update_agent_settings(value: AgentSettings, db: AsyncSession = Depends(get_session)):
    if scheduler.has_active_runs():
        raise HTTPException(409, "Agent 正在运行时不能修改设置")
    try:
        agent_settings_store.save(value)
        scheduler.configure(value)
    except ValueError as exc:
        raise HTTPException(400, f"Agent 设置无效：{exc}") from exc
    db.add(
        AuditLog(
            workspace_id="system",
            thread_id=None,
            event="agent.settings.updated",
            detail=(
                f"real={value.enable_real_agents};ssh={value.claude_ssh_enabled};"
                f"codex={value.codex_executable};codex_model={value.codex_model or 'default'};"
                f"codex_effort={value.codex_reasoning_effort};claude={value.claude_executable};"
                f"claude_model={value.claude_model or 'default'};claude_effort={value.claude_reasoning_effort}"
            ),
        )
    )
    await db.commit()
    return value


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


def _handoff_prompt(package: HandoffPackage) -> str:
    payload = package.payload
    if package.recipient == "claude":
        instruction = (
            "Perform an independent product-grade review of this structured handoff. Do not assume access to files not included. "
            "Return sections: verdict, completed, missing or not implemented, partial implementation, potential problems, regression risks, architecture or temporary-solution violations, evidence gaps, and required actions. "
            "Explicitly inspect whether Codex ignored requirements or used demo-style, hard-coded, simulated, bypass, or unsustainable framework choices."
        )
    else:
        instruction = (
            "Validate this plan or review package against the real local repository. Distinguish confirmed repository facts from suggestions. "
            "Report conflicts, missing requirements, affected files, formal architecture changes needed, tests required, and implementation readiness. "
            "Do not implement a temporary or demo-style workaround."
        )
    return f"{instruction}\n\nSTRUCTURED HANDOFF PACKAGE:\n{payload}"


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


@router.get("/workspaces", response_model=list[WorkspaceRead])
async def list_workspaces(db: AsyncSession = Depends(get_session)):
    return list((await db.scalars(_workspace_query())).unique().all())


@router.post("/workspaces", status_code=201, response_model=WorkspaceRead)
async def create_workspace(body: WorkspaceCreate, db: AsyncSession = Depends(get_session)):
    path = Path(body.path).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise HTTPException(400, "项目路径必须是文件夹")
    if not (path / ".git").exists():
        raise HTTPException(400, "项目必须是 Git 仓库")
    existing = await db.scalar(select(Workspace).where(Workspace.path == str(path)))
    if existing:
        return await db.scalar(_workspace_query().where(Workspace.id == existing.id))
    workspace = Workspace(name=body.name or path.name, path=str(path))
    workspace.threads = [Thread(title="新开发任务")]
    db.add(workspace)
    await db.commit()
    db.add(AuditLog(workspace_id=workspace.id, event="workspace.created", detail=str(path)))
    await db.commit()
    return await db.scalar(_workspace_query().where(Workspace.id == workspace.id))


async def _git_command(*arguments: str, cwd: Path | None = None) -> None:
    process = await asyncio.create_subprocess_exec(
        "git", *arguments, cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
        raise HTTPException(400, f"Git 命令执行失败：{detail}" if detail else "Git 命令执行失败")


async def _git_has_head(repository: Path) -> bool:
    process = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--verify", "HEAD", cwd=str(repository),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return await process.wait() == 0


async def _create_neutral_baseline(repository: Path, project_name: str) -> None:
    if await _git_has_head(repository):
        return
    readme = repository / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {project_name}\n\nProject goals, requirements, and technical decisions will be documented here.\n",
            encoding="utf-8",
        )
    await _git_command("add", "--", "README.md", cwd=repository)
    await _git_command(
        "-c", "user.name=DualCode Workbench",
        "-c", "user.email=workbench@local.invalid",
        "commit", "-m", "chore: initialize project", cwd=repository,
    )


@router.post("/workspaces/provision", status_code=201, response_model=WorkspaceRead)
async def provision_workspace(body: WorkspaceProvision, db: AsyncSession = Depends(get_session)):
    if any(char in body.remote_url for char in "\r\n\0"):
        raise HTTPException(400, "远程仓库 URL 无效")
    path = Path(body.path).expanduser().resolve()
    if path.parent == path:
        raise HTTPException(400, "不能将磁盘根目录用作项目目录")
    existing = await db.scalar(select(Workspace).where(Workspace.path == str(path)))
    if existing:
        return await db.scalar(_workspace_query().where(Workspace.id == existing.id))
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise HTTPException(409, "目标目录必须为空")
    if body.mode == "clone":
        if not body.remote_url.strip():
            raise HTTPException(400, "克隆项目时必须提供远程仓库 URL")
        path.parent.mkdir(parents=True, exist_ok=True)
        await _git_command("clone", "--", body.remote_url.strip(), str(path))
    else:
        path.mkdir(parents=True, exist_ok=True)
        await _git_command("init", "-b", "main", cwd=path)
        if body.remote_url.strip():
            await _git_command("remote", "add", "origin", body.remote_url.strip(), cwd=path)
    await _create_neutral_baseline(path, body.name or path.name)
    workspace = Workspace(name=body.name or path.name, path=str(path), threads=[Thread(title="新开发任务")])
    db.add(workspace)
    await db.commit()
    workspace_remote_store.save(workspace.id, WorkspaceRemoteSettings(remote_url=body.remote_url.strip()))
    db.add(AuditLog(workspace_id=workspace.id, event="workspace.provisioned", detail=f"mode={body.mode}"))
    await db.commit()
    return await db.scalar(_workspace_query().where(Workspace.id == workspace.id))


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def remove_workspace(workspace_id: str, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "未找到指定项目")
    thread_ids = list((await db.scalars(select(Thread.id).where(Thread.workspace_id == workspace_id))).all())
    if thread_ids:
        running = await db.scalar(select(Thread.id).where(Thread.id.in_(thread_ids), Thread.state.in_([
            RunState.PLANNING, RunState.WAITING_APPROVAL, RunState.IMPLEMENTING, RunState.TESTING,
            RunState.REVIEWING, RunState.FALLBACK_TO_CODEX,
        ])).limit(1))
        if running:
            raise HTTPException(409, "移除项目前请先停止正在运行的任务")
        for model in (ExecutionJob, AgentSession, AgentRun, Attachment, FileChange, TestRun, Approval, HandoffPackage, TaskContract, Message):
            column = model.thread_id
            await db.execute(delete(model).where(column.in_(thread_ids)))
        await db.execute(delete(Thread).where(Thread.id.in_(thread_ids)))
    await db.execute(delete(AuditLog).where(AuditLog.workspace_id == workspace_id))
    await db.execute(delete(ProjectGovernance).where(ProjectGovernance.workspace_id == workspace_id))
    await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
    await db.commit()
    workspace_remote_store.remove(workspace_id)


@router.post("/workspaces/{workspace_id}/threads", status_code=201)
async def create_thread(
    workspace_id: str, body: ThreadCreate, db: AsyncSession = Depends(get_session)
):
    lock = _thread_create_locks.setdefault(workspace_id, asyncio.Lock())
    async with lock:
        if not await db.get(Workspace, workspace_id):
            raise HTTPException(404, "未找到指定项目")
        existing = await db.scalar(
            select(Thread).where(
                Thread.workspace_id == workspace_id,
                Thread.state == RunState.CREATED,
                ~exists(select(Message.id).where(Message.thread_id == Thread.id)),
            ).limit(1)
        )
        if existing:
            return {"id": existing.id, "title": existing.title, "state": existing.state}
        thread = Thread(workspace_id=workspace_id, title=body.title)
        db.add(thread)
        await db.commit()
        await db.refresh(thread)
        return {"id": thread.id, "title": thread.title, "state": thread.state}


@router.patch("/workspaces/{workspace_id}/threads/{thread_id}")
async def update_thread(
    workspace_id: str,
    thread_id: str,
    body: ThreadUpdate,
    db: AsyncSession = Depends(get_session),
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "任务标题不能为空")
    thread.title = title
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event="thread.renamed",
            detail=f"length={len(thread.title)}",
        )
    )
    await db.commit()
    return {"id": thread.id, "title": thread.title, "state": thread.state}


@router.delete("/workspaces/{workspace_id}/threads/{thread_id}", status_code=204)
async def remove_thread(
    workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    if thread.state in {
        RunState.PLANNING,
        RunState.WAITING_APPROVAL,
        RunState.IMPLEMENTING,
        RunState.TESTING,
        RunState.REVIEWING,
        RunState.FALLBACK_TO_CODEX,
    }:
        raise HTTPException(409, "删除任务前请先停止当前运行")

    for model in (
        ExecutionJob,
        AgentSession,
        AgentRun,
        Attachment,
        FileChange,
        TestRun,
        Approval,
        HandoffPackage,
        TaskContract,
        Message,
    ):
        await db.execute(delete(model).where(model.thread_id == thread_id))
    await db.execute(delete(Thread).where(Thread.id == thread_id))
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event="thread.deleted",
            detail="task data and attachments removed",
        )
    )
    await db.commit()
    shutil.rmtree(settings.data_dir / "attachments" / workspace_id / thread_id, ignore_errors=True)


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/messages", status_code=202)
async def create_message(
    workspace_id: str, thread_id: str, body: MessageCreate, db: AsyncSession = Depends(get_session)
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    if thread.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
        thread.state = RunState.CREATED
    attachment_count = (
        len(
            (
                await db.scalars(
                    select(Attachment).where(
                        Attachment.id.in_(body.attachment_ids),
                        Attachment.workspace_id == workspace_id,
                        Attachment.thread_id == thread_id,
                    )
                )
            ).all()
        )
        if body.attachment_ids
        else 0
    )
    if attachment_count != len(body.attachment_ids):
        raise HTTPException(400, "附件不属于当前项目或任务")
    message = Message(thread_id=thread_id, role="user", content=body.content)
    db.add(message)
    await db.flush()
    attached_items = []
    if body.attachment_ids:
        attached_items = list((await db.scalars(select(Attachment).where(Attachment.id.in_(body.attachment_ids)))).all())
        await db.execute(update(Attachment).where(Attachment.id.in_(body.attachment_ids)).values(message_id=message.id))
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event="message.created",
            detail=body.mode,
        )
    )
    await db.commit()
    await manager.publish(
        AgentEvent(
            type=EventType.MESSAGE_CREATED,
            thread_id=thread_id,
            payload={"id": message.id, "role": "user", "content": message.content, "attachments": [
                {"id": item.id, "name": item.name, "media_type": item.media_type, "size": item.size}
                for item in attached_items
            ]},
        )
    )
    try:
        agent_prompt = body.content.strip() or "请查看并分析所附图片。"
        run_id = await scheduler.start(thread_id, agent_prompt, body.mode, body.attachment_ids)
    except RuntimeError as exc:
        raise HTTPException(409, f"无法撤销本轮修改：{exc}") from exc
    return {"message_id": message.id, "run_id": run_id, "attachments": [
        {"id": item.id, "name": item.name, "media_type": item.media_type, "size": item.size}
        for item in attached_items
    ]}


@router.post("/threads/{thread_id}/cancel", status_code=202)
async def cancel_run(thread_id: str, db: AsyncSession = Depends(get_session)):
    thread = await db.get(Thread, thread_id)
    if not thread:
        raise HTTPException(404, "未找到指定任务")
    thread.state = RunState.CREATED
    active_runs = (await db.scalars(select(AgentRun).where(
        AgentRun.thread_id == thread_id,
        AgentRun.state.in_([RunState.PLANNING, RunState.WAITING_APPROVAL, RunState.IMPLEMENTING, RunState.TESTING, RunState.REVIEWING]),
    ))).all()
    for run in active_runs:
        run.state = RunState.CANCELLED
    pending = (await db.scalars(select(Approval).where(Approval.thread_id == thread_id, Approval.status == "PENDING"))).all()
    for item in pending:
        item.status = "CANCELLED"
        approval_gate.resolve(item.id, False)
    db.add(AuditLog(workspace_id=thread.workspace_id, thread_id=thread_id, event="agent.run.cancelled", detail=f"pending_approvals={len(pending)}"))
    await db.commit()
    await manager.publish(AgentEvent(type=EventType.RUN_STATE_CHANGED, thread_id=thread_id, payload={"state": RunState.CREATED.value}))
    await manager.publish(AgentEvent(type=EventType.RUN_COMPLETED, thread_id=thread_id, payload={"status": "cancelled"}))
    await scheduler.cancel(thread_id)
    return {"status": "cancelled"}


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/runs/{run_id}/undo", status_code=202)
async def request_run_undo(workspace_id: str, thread_id: str, run_id: str, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.thread_id == thread_id, AgentRun.agent == "codex"))
    if not workspace or not run or not run.after_diff or run.after_diff == run.before_diff:
        raise HTTPException(404, "未找到可撤销的检查点")
    approval = Approval(thread_id=thread_id, action="undo_codex_run", reason="恢复到本轮 Codex 开始前的工作区状态；仅在当前 Diff 未发生后续变化时执行")
    db.add(approval)
    await db.flush()
    job = await create_job(db, approval=approval, workspace_id=workspace_id, kind="agent_undo",
                           payload={"run_id": run_id, "repository": workspace.path})
    approval_gate.prepare(approval.id)
    await db.commit()
    await manager.publish(AgentEvent(type=EventType.APPROVAL_REQUIRED, thread_id=thread_id,
                                     payload={"id": approval.id, "action": approval.action, "reason": approval.reason}))

    async def execute_after_approval() -> None:
        if not await approval_gate.wait(approval.id):
            return
        async with SessionLocal() as action_db:
            if not await claim_job(action_db, job.id):
                return
            stored = await action_db.get(AgentRun, run_id)
        succeeded, detail = False, ""
        try:
            if not stored:
                raise RuntimeError("Undo checkpoint no longer exists")
            repository = Path(workspace.path)
            current = await scheduler._git.diff(repository)
            if current != stored.after_diff:
                raise RuntimeError("工作区在该轮之后又发生了变化，已拒绝撤销以避免覆盖新修改")
            await scheduler._git.apply_diff(repository, stored.after_diff, reverse=True)
            if stored.before_diff:
                await scheduler._git.apply_diff(repository, stored.before_diff)
            succeeded = True
        except Exception as exc:
            detail = str(exc)
        async with SessionLocal() as action_db:
            await mark_job(action_db, approval.id, "SUCCEEDED" if succeeded else "FAILED", detail)
            action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id,
                                   event="agent.run.undo.completed" if succeeded else "agent.run.undo.failed",
                                   detail=f"run={run_id};success={str(succeeded).lower()}"))
            await action_db.commit()
        await manager.publish(AgentEvent(type=EventType.RUN_OUTPUT if succeeded else EventType.ERROR,
                                         thread_id=thread_id, payload={"kind": "agent_undo", "success": succeeded,
                                                                       "output": detail, "job_id": job.id}))

    task = asyncio.create_task(execute_after_approval())
    _git_tasks.add(task)
    task.add_done_callback(_git_tasks.discard)
    return {"approval_id": approval.id, "status": "PENDING"}


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/messages/{message_id}/retry", status_code=202)
async def retry_message(workspace_id: str, thread_id: str, message_id: str, db: AsyncSession = Depends(get_session)):
    thread = await db.scalar(select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id))
    message = await db.scalar(select(Message).where(Message.id == message_id, Message.thread_id == thread_id, Message.role == "user"))
    if not thread or not message:
        raise HTTPException(404, "未找到用户消息")
    if thread.state in {RunState.PLANNING, RunState.WAITING_APPROVAL, RunState.IMPLEMENTING, RunState.TESTING, RunState.REVIEWING}:
        raise HTTPException(409, "重试前请先停止当前运行")
    attachment_ids = list((await db.scalars(select(Attachment.id).where(Attachment.message_id == message_id))).all())
    prompt = message.content.strip() or "请查看并分析所附图片。"
    run_id = await scheduler.start(thread_id, prompt, "codex", attachment_ids)
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="message.retried", detail=f"message={message_id};run={run_id}"))
    await db.commit()
    return {"run_id": run_id}


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/attachments", status_code=201)
async def upload_attachment(
    workspace_id: str,
    thread_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    if file.content_type not in settings.allowed_attachment_types:
        raise HTTPException(415, "不支持此附件类型")
    content = await file.read(settings.max_attachment_bytes + 1)
    if len(content) > settings.max_attachment_bytes:
        raise HTTPException(413, "附件大小超过限制")
    if file.content_type in {"image/png", "image/jpeg", "image/webp"}:
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                output = io.BytesIO()
                format_name = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[file.content_type]
                if format_name == "JPEG" and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(output, format=format_name)
                content = output.getvalue()
        except (OSError, ValueError) as exc:
            raise HTTPException(400, "图片附件无效或已损坏") from exc
    attachment_suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "text/plain": ".txt",
    }.get(file.content_type, "")
    key = f"{workspace_id}/{thread_id}/{uuid.uuid4()}{attachment_suffix}"
    target = settings.data_dir / "attachments" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    item = Attachment(
        workspace_id=workspace_id,
        thread_id=thread_id,
        name=Path(file.filename or "attachment").name,
        media_type=file.content_type,
        size=len(content),
        storage_key=key,
    )
    db.add(item)
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event="attachment.created",
            detail=f"{item.name}:{len(content)}",
        )
    )
    await db.commit()
    return {"id": item.id, "name": item.name, "media_type": item.media_type, "size": item.size}


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/attachments/{attachment_id}/content")
async def attachment_content(workspace_id: str, thread_id: str, attachment_id: str, db: AsyncSession = Depends(get_session)):
    item = await db.scalar(select(Attachment).where(
        Attachment.id == attachment_id, Attachment.workspace_id == workspace_id, Attachment.thread_id == thread_id
    ))
    if not item:
        raise HTTPException(404, "未找到附件")
    root = (settings.data_dir / "attachments").resolve()
    target = (root / item.storage_key).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "未找到附件内容")
    return FileResponse(target, media_type=item.media_type, filename=item.name)


@router.websocket("/ws/threads/{thread_id}")
async def thread_events(ws: WebSocket, thread_id: str):
    await manager.connect(thread_id, ws)
    await ws.send_json(
        AgentEvent(type=EventType.CONNECTED, thread_id=thread_id).model_dump(mode="json")
    )
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        await manager.disconnect(thread_id, ws)
