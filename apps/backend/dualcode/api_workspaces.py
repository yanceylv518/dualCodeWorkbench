import asyncio
import shutil
import subprocess
from pathlib import Path
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from .config import settings
from .approvals import approval_gate
from .connections import manager
from .database import SessionLocal, get_session
from .events import AgentEvent, EventType
from .execution_jobs import claim_job, create_job, mark_job
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
from .schemas import MessageCreate, ThreadCreate, ThreadUpdate, WorkspaceCreate, WorkspaceProvision, WorkspaceRead
from .workspace_remote import WorkspaceRemoteSettings, workspace_remote_store

from .api_runtime import git_tasks as _git_tasks

router = APIRouter(prefix="/api")
_thread_create_locks: dict[str, asyncio.Lock] = {}


def _workspace_query():
    return select(Workspace).options(
        selectinload(Workspace.threads).selectinload(Thread.messages).selectinload(Message.attachments)
    )

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


