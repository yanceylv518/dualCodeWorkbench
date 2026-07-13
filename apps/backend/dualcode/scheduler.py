import asyncio
import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from .adapters import AgentAttachment, AgentRequest, AgentResponse, MockClaudeAdapter, MockCodexAdapter
from .approvals import approval_gate
from .cli_adapters import ClaudeCliAdapter
from .codex_app_server import CodexAppServerAdapter
from .connections import manager
from .database import SessionLocal
from .events import AgentEvent, EventType
from .git_service import GitService
from .models import (
    Approval,
    AgentRun,
    AgentSession,
    Attachment,
    AuditLog,
    FileChange,
    Message,
    ProjectGovernance,
    RunState,
    Thread,
    TaskContract,
    Workspace,
)
from .config import settings
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig
from .runtime_settings import AgentSettings, agent_settings_store
from .workspace_remote import derived_repository_path, workspace_remote_store
from .test_executor import TestExecutor


class RunScheduler:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._thread_grants: set[tuple[str, str]] = set()
        self.configure(agent_settings_store.load())

    def configure(self, runtime: AgentSettings) -> None:
        self.runtime = runtime
        self.real_agents_enabled = runtime.enable_real_agents
        self._codex = (
            CodexAppServerAdapter(runtime.codex_executable, model=runtime.codex_model, reasoning_effort=runtime.codex_reasoning_effort, permission_mode=runtime.codex_permission_mode)
            if runtime.enable_real_agents
            else MockCodexAdapter()
        )
        if runtime.enable_real_agents and runtime.claude_ssh_enabled:
            self._claude = ClaudeSshAdapter(
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
        elif runtime.enable_real_agents:
            self._claude = ClaudeCliAdapter(runtime.claude_executable, model=runtime.claude_model, reasoning_effort=runtime.claude_reasoning_effort)
        else:
            self._claude = MockClaudeAdapter()
        self._git = GitService(settings.data_dir / "worktrees")
        self._tests = TestExecutor()

    def has_active_runs(self) -> bool:
        return any(not task.done() for task in self._tasks.values())

    def grant_for_thread(self, thread_id: str, action: str) -> None:
        self._thread_grants.add((thread_id, action))

    async def _has_thread_grant(self, db, thread_id: str, action: str) -> bool:
        if (thread_id, action) in self._thread_grants:
            return True
        decisions = (await db.scalars(
            select(AuditLog.detail).where(
                AuditLog.thread_id == thread_id,
                AuditLog.event == "approval.decided",
            )
        )).all()
        approval_ids = {
            detail.split(":", 1)[0]
            for detail in decisions
            if ":APPROVED:scope=thread:" in detail
        }
        if not approval_ids:
            return False
        approved = await db.scalar(
            select(Approval.id).where(
                Approval.id.in_(approval_ids),
                Approval.thread_id == thread_id,
                Approval.action == action,
                Approval.status == "APPROVED",
            ).limit(1)
        )
        if approved:
            self._thread_grants.add((thread_id, action))
            return True
        return False

    async def start(self, thread_id: str, prompt: str, mode: str, attachment_ids: list[str] | None = None) -> str:
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            raise RuntimeError("A run is already active for this thread")
        run_id = str(uuid.uuid4())
        self._tasks[thread_id] = asyncio.create_task(self._execute(thread_id, run_id, prompt, mode, attachment_ids or []))
        return run_id

    async def cancel(self, thread_id: str) -> None:
        task = self._tasks.get(thread_id)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.CancelledError, TimeoutError):
                pass
        self._tasks.pop(thread_id, None)

    async def _execute(
        self,
        thread_id: str,
        run_id: str,
        prompt: str,
        mode: str,
        attachment_ids: list[str],
    ) -> None:
        """Dispatch a single explicitly selected Agent turn."""
        await self._execute_chat(thread_id, run_id, prompt, mode, attachment_ids)

    async def _execute_chat(self, thread_id: str, run_id: str, prompt: str, agent: str, attachment_ids: list[str]) -> None:
        """Run one turn for the selected agent without advancing an orchestration pipeline."""
        async with SessionLocal() as db:
            thread = await db.scalar(select(Thread).where(Thread.id == thread_id))
            if not thread:
                return
            workspace = await db.get(Workspace, thread.workspace_id)
            if not workspace:
                return
            run_state = RunState.IMPLEMENTING if agent == "codex" else RunState.PLANNING
            selected_model = (self.runtime.codex_model or "cli-default") if agent == "codex" else self.runtime.claude_model
            run = AgentRun(id=run_id, thread_id=thread_id, agent=agent, state=run_state)
            thread.state = run_state
            db.add(run)
            db.add(AuditLog(
                workspace_id=workspace.id,
                thread_id=thread_id,
                event="agent.chat.started",
                detail=(
                    f"run={run_id};agent={agent};model="
                    f"{selected_model};"
                    f"effort={self.runtime.codex_reasoning_effort if agent == 'codex' else self.runtime.claude_reasoning_effort}"
                ),
            ))
            await db.commit()

            async def emit(kind: EventType, payload: dict[str, object]) -> None:
                await manager.publish(AgentEvent(type=kind, thread_id=thread_id, run_id=run_id, payload=payload))

            async def approve(action: str, reason: str) -> bool:
                if await self._has_thread_grant(db, thread_id, action):
                    return True
                item = Approval(thread_id=thread_id, action=action, reason=reason)
                db.add(item)
                await db.flush()
                approval_gate.prepare(item.id)
                await db.commit()
                await emit(EventType.APPROVAL_REQUIRED, {"id": item.id, "action": action, "reason": reason})
                return await approval_gate.wait(item.id)

            await emit(EventType.RUN_STATE_CHANGED, {"state": run_state.value, "agent": agent})
            try:
                remote_settings = workspace_remote_store.get(workspace.id)
                if not remote_settings.vps_repo_path:
                    runtime = agent_settings_store.load()
                    remote_settings = remote_settings.model_copy(update={"vps_repo_path": derived_repository_path(runtime.claude_ssh_projects_root, remote_settings.remote_url, workspace.name)})
                has_remote_repo = agent == "claude" and bool(remote_settings.vps_repo_path)
                action = "remote_edit_files" if has_remote_repo else "network_access"
                reason = "允许 VPS Claude 在已配置远端仓库中处理本轮请求" if has_remote_repo else "允许向 VPS Claude 发送本轮对话"
                if agent != "codex" and not await approve(action, reason):
                    thread.state = RunState.CREATED
                    run.state = RunState.CANCELLED
                    await db.commit()
                    await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})
                    return
                recent = (await db.scalars(
                    select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at.desc()).limit(20)
                )).all()
                transcript = "\n".join(f"{item.role}: {item.content}" for item in reversed(recent))
                governance = await db.scalar(select(ProjectGovernance).where(ProjectGovernance.workspace_id == workspace.id))
                task_contract = await db.scalar(select(TaskContract).where(TaskContract.thread_id == thread_id))
                governance_context = {
                    "product_goal": governance.product_goal if governance else "",
                    "product_boundary": governance.product_boundary if governance else "",
                    "project_rules": json.loads(governance.rules) if governance else [],
                    "required_deliverables": json.loads(governance.deliverables) if governance else [],
                    "task_goal": task_contract.goal if task_contract else "",
                    "non_goals": json.loads(task_contract.non_goals) if task_contract else [],
                    "acceptance": json.loads(task_contract.acceptance) if task_contract else [],
                    "constraints": json.loads(task_contract.constraints) if task_contract else [],
                    "known_risks": json.loads(task_contract.risks) if task_contract else [],
                }
                request_prompt = (
                    "Continue this development conversation. Respond only as the selected agent. "
                    "Do not hand off to another agent or automatically advance a workflow. "
                    "Image generation is not available in DualCode; do not invoke imageGeneration or claim that an image was generated.\n\n"
                    "This is production product development, not a demo. Do not use temporary, simulated, hard-coded, bypass, or unsustainable architecture merely to complete the current feature. "
                    "Identify requirements not covered by the implementation, potential problems, regression risks, and missing evidence. "
                    "If the existing architecture is insufficient, propose a formal architectural change instead of disguising a temporary patch as complete.\n\n"
                    f"PROJECT AND TASK CONTRACT:\n{json.dumps(governance_context, ensure_ascii=False)}\n\n"
                    f"RECENT CONVERSATION:\n{transcript}\n\nCURRENT REQUEST:\n{prompt}"
                )
                context = {"workspace_path": workspace.path}
                async def native_codex_approval(method: str, params: dict) -> bool:
                    if method == "item/commandExecution/requestApproval":
                        command = str(params.get("command") or "执行命令")
                        return await approve("codex_command", command[:1000])
                    if method == "item/fileChange/requestApproval":
                        approval_reason = str(params.get("reason") or "Codex 请求修改当前项目文件")
                        return await approve("codex_file_change", approval_reason[:1000])
                    return await approve("codex_permissions", "Codex 请求扩大当前轮次的文件系统或网络权限")
                if agent == "codex":
                    context["approval_callback"] = native_codex_approval
                attachments: list[AgentAttachment] = []
                text_attachments: list[str] = []
                if attachment_ids:
                    records = (await db.scalars(select(Attachment).where(
                        Attachment.id.in_(attachment_ids),
                        Attachment.workspace_id == workspace.id,
                        Attachment.thread_id == thread_id,
                    ))).all()
                    attachment_root = (settings.data_dir / "attachments").resolve()
                    context["attachment_root"] = str(attachment_root)
                    for item in records:
                        local_path = (attachment_root / item.storage_key).resolve(strict=True)
                        if item.media_type == "text/plain":
                            text_attachments.append(f"ATTACHMENT {item.name}:\n{local_path.read_text(encoding='utf-8', errors='replace')[:200_000]}")
                            continue
                        attachments.append(AgentAttachment(
                            id=item.id,
                            local_path=local_path,
                            media_type=item.media_type,
                            size=item.size,
                            sha256=hashlib.sha256(local_path.read_bytes()).hexdigest(),
                        ))
                if text_attachments:
                    request_prompt += "\n\n" + "\n\n".join(text_attachments)
                previous_session = await db.scalar(
                    select(AgentSession)
                    .where(AgentSession.thread_id == thread_id, AgentSession.agent == agent)
                    .limit(1)
                )
                if previous_session:
                    context["session_id"] = previous_session.external_session_id
                if has_remote_repo:
                    context["remote_workspace_path"] = remote_settings.vps_repo_path
                    context["allow_remote_write"] = True
                adapter = self._codex if agent == "codex" else self._claude
                if agent == "codex":
                    run.before_diff = await self._git.diff(Path(workspace.path))
                    await db.commit()
                try:
                    response = await self._stream_agent(adapter, AgentRequest(thread_id, request_prompt, context, attachments), agent, emit)
                except Exception:
                    if "session_id" not in context:
                        raise
                    context.pop("session_id")
                    response = await self._stream_agent(adapter, AgentRequest(thread_id, request_prompt, context, attachments), agent, emit)
                    db.add(AuditLog(workspace_id=workspace.id, thread_id=thread_id, event="agent.session.fallback", detail=f"agent={agent};previous={previous_session.external_session_id if previous_session else ''}"))
                if agent == "codex":
                    diff = await self._git.diff(Path(workspace.path))
                    run.after_diff = diff
                    changed = await self._git.changed_files(Path(workspace.path))
                    old_changes = (await db.scalars(select(FileChange).where(FileChange.thread_id == thread_id))).all()
                    for old_change in old_changes:
                        await db.delete(old_change)
                    for path in changed:
                        db.add(FileChange(thread_id=thread_id, path=path, diff=diff))
                    await emit(EventType.RUN_OUTPUT, {"kind": "workspace_changes", "files": changed, "diff": diff})
                db.add(Message(thread_id=thread_id, role=agent, content=response.content))
                old_sessions = (await db.scalars(select(AgentSession).where(AgentSession.thread_id == thread_id, AgentSession.agent == agent))).all()
                for old_session in old_sessions:
                    await db.delete(old_session)
                db.add(AgentSession(
                    thread_id=thread_id,
                    agent=agent,
                    external_session_id=response.run_id,
                    workspace_path=workspace.path,
                ))
                run.output = response.content
                run.state = RunState.COMPLETED
                thread.state = RunState.CREATED
                await db.commit()
                await emit(EventType.MESSAGE_CREATED, {"role": agent, "content": response.content})
                await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})
                await emit(EventType.RUN_COMPLETED, {"status": "idle", "agent": agent})
            except asyncio.CancelledError:
                thread.state = RunState.CREATED
                run.state = RunState.CANCELLED
                await db.commit()
                await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})
            except Exception as exc:
                thread.state = RunState.CREATED
                run.state = RunState.FAILED
                run.output = str(exc)
                await db.commit()
                await emit(EventType.ERROR, {"message": str(exc)})
                await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})

    async def _stream_agent(self, adapter, request: AgentRequest, agent: str, emit) -> AgentResponse:
        session_id = ""
        content_parts: list[str] = []
        async for chunk in adapter.stream(request):
            text = ""
            try:
                event = json.loads(chunk)
            except json.JSONDecodeError:
                event = None
                text = chunk
            if isinstance(event, dict):
                session_id = str(event.get("thread_id") or event.get("session_id") or session_id)
                if agent == "codex":
                    if event.get("type") == "agent_message.delta":
                        text = str(event.get("text") or "")
                    elif event.get("type") == "activity.event":
                        item = event.get("item")
                        if isinstance(item, dict):
                            await emit(EventType.TOOL_EVENT, {"agent": agent, "event": event.get("event"), "item": item})
                    elif event.get("type") == "activity.delta":
                        item = event.get("item")
                        if isinstance(item, dict):
                            await emit(EventType.TOOL_EVENT, {"agent": agent, "event": "delta", "item": item})
                    elif event.get("type") == "terminal.delta":
                        await emit(EventType.TERMINAL_OUTPUT, {"channel": "codex", "text": str(event.get("text") or "")})
                    item = event.get("item")
                    if not text and isinstance(item, dict) and event.get("type") == "item.completed" and item.get("type") == "agent_message":
                        text = str(item.get("text") or "")
                    elif isinstance(item, dict) and item.get("type") in {
                        "reasoning", "command_execution", "file_change", "mcp_tool_call", "web_search"
                    }:
                        await emit(EventType.TOOL_EVENT, {"agent": agent, "event": event.get("type"), "item": item})
                else:
                    if event.get("type") == "assistant":
                        message = event.get("message")
                        if isinstance(message, dict):
                            blocks = message.get("content", [])
                            text = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text")
                    elif event.get("type") == "result" and not content_parts:
                        text = str(event.get("result") or "")
            if text:
                content_parts.append(text)
                await emit(EventType.AGENT_DELTA, {"agent": agent, "text": text})
        return AgentResponse(session_id or str(uuid.uuid4()), "".join(content_parts))


scheduler = RunScheduler()
