import asyncio
import io
import json
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
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from PIL import Image
from .config import settings
from .cli_adapters import ClaudeCliAdapter, CodexCliAdapter
from .approvals import approval_gate
from .connections import manager
from .database import SessionLocal, get_session
from .events import AgentEvent, EventType
from .git_service import GitError
from .models import (
    AgentSession,
    Approval,
    Attachment,
    AuditLog,
    FileChange,
    Message,
    RunState,
    TestRun,
    Thread,
    Workspace,
)
from .scheduler import scheduler
from .schemas import ApprovalDecision, GitActionCreate, MessageCreate, RemoteGitActionCreate, ThreadCreate, WorkspaceCreate, WorkspaceRead
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig
from .test_executor import TestCommand
from .runtime_settings import AgentSettings, agent_settings_store
from .workspace_remote import WorkspaceRemoteSettings, workspace_remote_store

router = APIRouter(prefix="/api")
_thread_create_locks: dict[str, asyncio.Lock] = {}
_git_tasks: set[asyncio.Task[None]] = set()


@router.get("/workspaces/{workspace_id}/git/status")
async def workspace_git_status(workspace_id: str, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    try:
        return await scheduler._git.repository_status(Path(workspace.path))
    except (GitError, OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workspaces/{workspace_id}/remote")
async def workspace_remote_status(workspace_id: str, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    value = workspace_remote_store.get(workspace_id)
    local = await scheduler._git.repository_status(Path(workspace.path))
    result: dict[str, object] = {"settings": value.model_dump(), "local": local, "vps": None, "same_remote": False, "same_commit": False}
    if not value.vps_repo_path:
        return result
    runtime = agent_settings_store.load()
    if not (runtime.claude_ssh_enabled and runtime.claude_ssh_host and runtime.claude_ssh_username and runtime.claude_ssh_known_hosts):
        return result
    adapter = ClaudeSshAdapter(ClaudeSshConfig(
        host=runtime.claude_ssh_host,
        username=runtime.claude_ssh_username,
        port=runtime.claude_ssh_port,
        known_hosts=Path(runtime.claude_ssh_known_hosts),
        client_keys=(Path(runtime.claude_ssh_client_key),) if runtime.claude_ssh_client_key else (),
        remote_root=PurePosixPath(runtime.claude_ssh_remote_root),
        claude_executable=PurePosixPath(runtime.claude_ssh_executable),
        model=runtime.claude_model,
        reasoning_effort=runtime.claude_reasoning_effort,
    ))
    try:
        remote = await adapter.repository_status(PurePosixPath(value.vps_repo_path))
        result["vps"] = remote
        expected_remote = value.remote_url or str(local.get("remote", ""))
        normalize = lambda item: str(item).strip().lower().removesuffix(".git").rstrip("/")
        result["same_remote"] = bool(expected_remote) and normalize(expected_remote) == normalize(remote["remote"])
        result["same_commit"] = bool(local.get("head")) and str(local["head"]).lower() == remote["head"][:10].lower()
    except Exception as exc:
        result["error"] = str(exc)
    return result


@router.put("/workspaces/{workspace_id}/remote", response_model=WorkspaceRemoteSettings)
async def update_workspace_remote(workspace_id: str, value: WorkspaceRemoteSettings, db: AsyncSession = Depends(get_session)):
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
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
        raise HTTPException(404, "Workspace or thread not found")
    if not remote.vps_repo_path:
        raise HTTPException(400, "VPS repository path is not configured")
    runtime = agent_settings_store.load()
    adapter = ClaudeSshAdapter(ClaudeSshConfig(
        host=runtime.claude_ssh_host,
        username=runtime.claude_ssh_username,
        port=runtime.claude_ssh_port,
        known_hosts=Path(runtime.claude_ssh_known_hosts),
        client_keys=(Path(runtime.claude_ssh_client_key),) if runtime.claude_ssh_client_key else (),
        remote_root=PurePosixPath(runtime.claude_ssh_remote_root),
        claude_executable=PurePosixPath(runtime.claude_ssh_executable),
        model=runtime.claude_model,
        reasoning_effort=runtime.claude_reasoning_effort,
    ))
    action_name = f"remote_git_{body.action}"
    reason = "在 VPS 仓库执行 git fetch --prune" if body.action == "fetch" else "在 VPS 仓库执行 git pull --ff-only，不自动合并"
    approval = Approval(thread_id=thread_id, action=action_name, reason=reason)
    db.add(approval)
    await db.flush()
    approval_gate.prepare(approval.id)
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="remote.git.requested", detail=f"{body.action}:{approval.id}"))
    await db.commit()
    await manager.publish(AgentEvent(type=EventType.APPROVAL_REQUIRED, thread_id=thread_id, payload={"id": approval.id, "action": action_name, "reason": reason}))

    async def execute_after_approval() -> None:
        if not await approval_gate.wait(approval.id):
            return
        event = "remote.git.completed"
        try:
            output = await adapter.repository_update(PurePosixPath(remote.vps_repo_path), body.action)
        except Exception as exc:
            event = "remote.git.failed"
            output = str(exc)
        async with SessionLocal() as action_db:
            action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event=event, detail=f"{body.action}:{output}"))
            await action_db.commit()
        await manager.publish(AgentEvent(type=EventType.RUN_OUTPUT, thread_id=thread_id, payload={"kind": "remote_git", "action": body.action, "success": event.endswith("completed"), "output": output}))

    task = asyncio.create_task(execute_after_approval())
    _git_tasks.add(task)
    task.add_done_callback(_git_tasks.discard)
    return {"approval_id": approval.id, "status": "PENDING"}


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
        raise HTTPException(404, "Workspace or thread not found")
    reasons = {
        "commit": f"暂存当前全部变更并创建提交：{body.message.strip() or '(未填写提交说明)'}",
        "push": "将当前分支提交推送到已配置的远程仓库",
        "pull": "使用 fast-forward-only 拉取远程提交，不自动创建合并提交",
    }
    approval = Approval(thread_id=thread_id, action=f"git_{body.action}", reason=reasons[body.action])
    db.add(approval)
    await db.flush()
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="git.action.requested", detail=f"{body.action}:{approval.id}"))
    approval_gate.prepare(approval.id)
    repository_path = workspace.path
    await db.commit()
    await manager.publish(AgentEvent(type=EventType.APPROVAL_REQUIRED, thread_id=thread_id, payload={"id": approval.id, "action": approval.action, "reason": approval.reason}))

    async def execute_after_approval() -> None:
        approved = await approval_gate.wait(approval.id)
        if not approved:
            return
        outcome = ""
        event = "git.action.completed"
        try:
            repository = Path(repository_path)
            if body.action == "commit":
                outcome = await scheduler._git.commit_all(repository, body.message)
            elif body.action == "push":
                outcome = await scheduler._git.push(repository)
            else:
                outcome = await scheduler._git.pull_ff_only(repository)
        except Exception as exc:
            event = "git.action.failed"
            outcome = str(exc)
        async with SessionLocal() as action_db:
            action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event=event, detail=f"{body.action}:{outcome}"))
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
        raise HTTPException(404, "Workspace or thread not found")
    runtime = agent_settings_store.load()
    if not runtime.test_executable:
        raise HTTPException(400, "Test executable is not configured in Agent settings")
    approval = Approval(thread_id=thread_id, action="run_test", reason=f"在本地仓库运行测试：{runtime.test_executable} {' '.join(runtime.test_arguments)}")
    db.add(approval)
    await db.flush()
    approval_gate.prepare(approval.id)
    db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="test.requested", detail=approval.reason))
    await db.commit()
    await manager.publish(AgentEvent(type=EventType.APPROVAL_REQUIRED, thread_id=thread_id, payload={"id": approval.id, "action": "run_test", "reason": approval.reason}))
    repository_path = workspace.path

    async def execute_after_approval() -> None:
        if not await approval_gate.wait(approval.id):
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
                action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="test.completed", detail=f"exit_code={result.exit_code}"))
                await action_db.commit()
            await manager.publish(AgentEvent(type=EventType.TEST_RESULT, thread_id=thread_id, payload={"command": record.command, "output": record.output, "exit_code": record.exit_code}))
        except Exception as exc:
            async with SessionLocal() as action_db:
                current = await action_db.get(Thread, thread_id)
                if current:
                    current.state = RunState.CREATED
                action_db.add(AuditLog(workspace_id=workspace_id, thread_id=thread_id, event="test.failed", detail=str(exc)))
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
                remote_root=PurePosixPath(runtime.claude_ssh_remote_root),
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
    codex = CodexCliAdapter(runtime.codex_executable, model=runtime.codex_model, reasoning_effort=runtime.codex_reasoning_effort)
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
                remote_root=PurePosixPath(runtime.claude_ssh_remote_root),
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
        raise HTTPException(409, "Agent settings cannot change while runs are active")
    try:
        agent_settings_store.save(value)
        scheduler.configure(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
    return select(Workspace).options(selectinload(Workspace.threads).selectinload(Thread.messages))


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/approvals")
async def list_approvals(
    workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "Workspace/Thread mismatch")
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


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/details")
async def thread_details(
    workspace_id: str, thread_id: str, db: AsyncSession = Depends(get_session)
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "Workspace/Thread mismatch")
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
        raise HTTPException(404, "Pending approval not found")
    approval.status = "APPROVED" if body.approved else "REJECTED"
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event="approval.decided",
            detail=f"{approval.id}:{approval.status}:{body.note}",
        )
    )
    await db.commit()
    delivered = approval_gate.resolve(approval.id, body.approved)
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
        raise HTTPException(400, "Workspace path must be a directory")
    if not (path / ".git").exists():
        raise HTTPException(400, "Workspace must be a Git repository")
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


@router.post("/workspaces/{workspace_id}/threads", status_code=201)
async def create_thread(
    workspace_id: str, body: ThreadCreate, db: AsyncSession = Depends(get_session)
):
    lock = _thread_create_locks.setdefault(workspace_id, asyncio.Lock())
    async with lock:
        if not await db.get(Workspace, workspace_id):
            raise HTTPException(404, "Workspace not found")
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


@router.post("/workspaces/{workspace_id}/threads/{thread_id}/messages", status_code=202)
async def create_message(
    workspace_id: str, thread_id: str, body: MessageCreate, db: AsyncSession = Depends(get_session)
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "Workspace/Thread mismatch")
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
        raise HTTPException(400, "Attachment ownership mismatch")
    message = Message(thread_id=thread_id, role="user", content=body.content)
    db.add(message)
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
            payload={"id": message.id, "role": "user", "content": message.content},
        )
    )
    try:
        run_id = await scheduler.start(thread_id, body.content, body.mode, body.attachment_ids)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"message_id": message.id, "run_id": run_id}


@router.post("/threads/{thread_id}/cancel", status_code=202)
async def cancel_run(thread_id: str, db: AsyncSession = Depends(get_session)):
    thread = await db.get(Thread, thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    await scheduler.cancel(thread_id)
    pending = (await db.scalars(select(Approval).where(Approval.thread_id == thread_id, Approval.status == "PENDING"))).all()
    for item in pending:
        item.status = "CANCELLED"
        approval_gate.resolve(item.id, False)
    db.add(AuditLog(workspace_id=thread.workspace_id, thread_id=thread_id, event="agent.run.cancelled", detail=f"pending_approvals={len(pending)}"))
    await db.commit()
    return {"status": "cancelling"}


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
        raise HTTPException(404, "Workspace/Thread mismatch")
    if file.content_type not in settings.allowed_attachment_types:
        raise HTTPException(415, "Unsupported attachment type")
    content = await file.read(settings.max_attachment_bytes + 1)
    if len(content) > settings.max_attachment_bytes:
        raise HTTPException(413, "Attachment too large")
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
            raise HTTPException(400, "Invalid image attachment") from exc
    key = f"{workspace_id}/{thread_id}/{uuid.uuid4()}"
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
