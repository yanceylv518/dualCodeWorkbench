import asyncio
import re
from pathlib import Path, PurePosixPath
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .approvals import approval_gate
from .connections import manager
from .database import SessionLocal, get_session
from .events import AgentEvent, EventType
from .execution_jobs import claim_job, create_job, decide_job, decode_json_object, mark_job, record_job_evidence, request_retry
from .git_service import GitError
from .models import (
    Approval,
    AuditLog,
    ExecutionJob,
    RunState,
    TestRun,
    Thread,
    Workspace,
)
from .scheduler import scheduler
from .schemas import GitActionCreate, RemoteGitActionCreate
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig, RemoteRepositoryUnavailable
from .test_executor import TestCommand
from .runtime_settings import agent_settings_store
from .workspace_remote import WorkspaceRemoteSettings, derived_repository_path, workspace_remote_store

from .api_runtime import git_tasks as _git_tasks

router = APIRouter(prefix="/api")

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


