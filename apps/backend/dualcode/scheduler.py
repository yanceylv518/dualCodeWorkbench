import asyncio
import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from .adapters import AgentAttachment, AgentRequest, AgentResponse, MockClaudeAdapter, MockCodexAdapter
from .approvals import approval_gate
from .cli_adapters import ClaudeCliAdapter, CodexCliAdapter
from .connections import manager
from .database import SessionLocal
from .events import AgentEvent, EventType
from .git_service import GitService
from .models import (
    Approval,
    AgentRun,
    AgentSession,
    AuditLog,
    FileChange,
    Message,
    RunState,
    TestRun,
    Thread,
    Workspace,
)
from .config import settings
from .state_machine import transition
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig
from .runtime_settings import AgentSettings, agent_settings_store
from .workspace_remote import workspace_remote_store
from .test_executor import TestCommand, TestExecutor


class RunScheduler:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.configure(agent_settings_store.load())

    def configure(self, runtime: AgentSettings) -> None:
        self.runtime = runtime
        self.real_agents_enabled = runtime.enable_real_agents
        self._codex = (
            CodexCliAdapter(runtime.codex_executable, model=runtime.codex_model, reasoning_effort=runtime.codex_reasoning_effort)
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
                    remote_root=PurePosixPath(runtime.claude_ssh_remote_root),
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

    async def _execute(self, thread_id: str, run_id: str, prompt: str, mode: str, attachment_ids: list[str]) -> None:
        if mode in {"codex", "claude"}:
            await self._execute_chat(thread_id, run_id, prompt, mode, attachment_ids)
            return
        sequence = 0
        async with SessionLocal() as db:
            thread = await db.scalar(select(Thread).where(Thread.id == thread_id))
            if not thread:
                return
            run = AgentRun(id=run_id, thread_id=thread_id, agent=mode, state=thread.state)
            db.add(run)
            db.add(
                AuditLog(
                    workspace_id=thread.workspace_id,
                    thread_id=thread_id,
                    event="agent.run.started",
                    detail=(
                        f"run={run_id};mode={mode};real={self.real_agents_enabled};"
                        f"codex_model={self.runtime.codex_model or 'cli-default'};"
                        f"codex_effort={self.runtime.codex_reasoning_effort};"
                        f"claude_model={self.runtime.claude_model};"
                        f"claude_effort={self.runtime.claude_reasoning_effort};"
                        f"claude_location={'vps-ssh' if self.runtime.claude_ssh_enabled else 'local'}"
                    ),
                )
            )
            await db.commit()

            async def emit(kind: EventType, payload: dict[str, object]) -> None:
                nonlocal sequence
                sequence += 1
                await manager.publish(
                    AgentEvent(
                        type=kind,
                        thread_id=thread_id,
                        run_id=run_id,
                        sequence=sequence,
                        payload=payload,
                    )
                )

            async def change_state(target: RunState) -> None:
                thread.state = transition(thread.state, target)
                run.state = target
                db.add(
                    AuditLog(
                        workspace_id=thread.workspace_id,
                        thread_id=thread_id,
                        event="state.transition",
                        detail=f"{target.value}:{run_id}",
                    )
                )
                await db.commit()
                await emit(EventType.RUN_STATE_CHANGED, {"state": target.value})

            async def message(role: str, content: str) -> None:
                item = Message(thread_id=thread_id, role=role, content=content)
                db.add(item)
                await db.commit()
                await emit(
                    EventType.MESSAGE_CREATED, {"id": item.id, "role": role, "content": content}
                )

            async def request_approval(action: str, reason: str) -> bool:
                approval = Approval(thread_id=thread_id, action=action, reason=reason)
                db.add(approval)
                await db.flush()
                db.add(
                    AuditLog(
                        workspace_id=thread.workspace_id,
                        thread_id=thread_id,
                        event="approval.requested",
                        detail=f"{approval.id}:{action}:{run_id}",
                    )
                )
                await db.commit()
                approval_gate.prepare(approval.id)
                await emit(
                    EventType.APPROVAL_REQUIRED,
                    {"id": approval.id, "action": action, "reason": reason},
                )
                return await approval_gate.wait(approval.id)

            try:
                await change_state(RunState.PLANNING)
                context = {"workspace_path": thread.workspace_id}
                workspace = await db.get(Workspace, thread.workspace_id)
                if workspace:
                    context["workspace_path"] = workspace.path
                if self.real_agents_enabled:
                    await change_state(RunState.WAITING_APPROVAL)
                    if not await request_approval(
                        "plan_with_claude",
                        "让 Claude 根据任务描述生成实施计划；此阶段不会修改本地文件",
                    ):
                        await change_state(RunState.CANCELLED)
                        await message("system", "Network access was rejected by the user.")
                        return
                    await change_state(RunState.PLANNING)
                planning_prompt = (
                    "You are the remote read-only planner. The remote working directory is "
                    "intentionally empty: the local repository is never synchronized to the VPS. "
                    "Do not inspect the remote directory and do not infer that local files are missing. "
                    "Create a concise implementation and verification plan from the task description only. "
                    "Local Codex will inspect and modify the real repository after approval.\n\nTASK:\n"
                    + prompt
                )
                response = await self._claude.send(
                    AgentRequest(thread_id, planning_prompt, context)
                )
                await message("claude", response.content)
                await change_state(RunState.WAITING_APPROVAL)
                if not await request_approval(
                    "approve_plan",
                    "确认 Claude 的计划后，才允许进入 Codex 实现阶段",
                ):
                    await change_state(RunState.CANCELLED)
                    await message("system", "计划未获批准，任务已停止。")
                    return
                worktree = Path(str(context["workspace_path"]))
                if self.real_agents_enabled:
                    if not await request_approval(
                        "implement_plan",
                        "创建隔离 Git worktree，并让 Codex 按已批准计划修改文件",
                    ):
                        await change_state(RunState.CANCELLED)
                        await message("system", "实现步骤未获批准，任务已停止。")
                        return
                    worktree, branch = await self._git.create_worktree(
                        worktree, thread.workspace_id, thread_id
                    )
                    context["workspace_path"] = str(worktree)
                    await emit(
                        EventType.RUN_OUTPUT,
                        {"kind": "git", "worktree": str(worktree), "branch": branch},
                    )
                await change_state(RunState.IMPLEMENTING)
                codex_response = await self._codex.send(
                    AgentRequest(thread_id, response.content, context)
                )
                if self.real_agents_enabled:
                    db.add(
                        AgentSession(
                            thread_id=thread_id,
                            agent="codex",
                            external_session_id=codex_response.run_id,
                            workspace_path=str(worktree),
                        )
                    )
                    diff = await self._git.diff(worktree)
                    for path in await self._git.changed_files(worktree):
                        db.add(FileChange(thread_id=thread_id, path=path, diff=diff))
                    await db.commit()
                    await message("codex", codex_response.content)
                else:
                    diff = "Mock diff"
                    await message("codex", "已在隔离工作区完成 Mock 修改，并记录文件变化。")

                if self.real_agents_enabled:
                    await change_state(RunState.WAITING_APPROVAL)
                    if not await request_approval(
                        "run_test", "在隔离 worktree 中运行配置的测试命令"
                    ):
                        await change_state(RunState.CANCELLED)
                        await message("system", "Test execution was rejected by the user.")
                        return
                    if not self.runtime.test_executable:
                        raise RuntimeError("No test executable is configured")
                    await change_state(RunState.TESTING)

                    async def test_output(channel: str, text: str) -> None:
                        await emit(
                            EventType.TERMINAL_OUTPUT,
                            {"channel": channel, "text": text},
                        )

                    test_result = await self._tests.execute(
                        TestCommand(
                            executable=Path(self.runtime.test_executable),
                            arguments=tuple(self.runtime.test_arguments),
                            cwd=worktree,
                        ),
                        worktree,
                        test_output,
                    )
                    test = TestRun(
                        thread_id=thread_id,
                        command=" ".join(test_result.command),
                        output=test_result.stdout + test_result.stderr,
                        exit_code=test_result.exit_code,
                    )
                else:
                    await change_state(RunState.TESTING)
                    test = TestRun(
                        thread_id=thread_id, command="pytest", output="12 passed", exit_code=0
                    )
                db.add(test)
                await db.commit()
                await emit(
                    EventType.TEST_RESULT,
                    {"command": test.command, "output": test.output, "exit_code": test.exit_code},
                )
                if test.exit_code != 0:
                    raise RuntimeError(f"Tests failed with exit code {test.exit_code}")

                if self.real_agents_enabled:
                    await change_state(RunState.WAITING_APPROVAL)
                    if not await request_approval(
                        "review_with_claude",
                        "仅发送 Git Diff 与测试摘要给 Claude 进行最终审查",
                    ):
                        await change_state(RunState.CANCELLED)
                        await message("system", "Review network access was rejected.")
                        return
                    await change_state(RunState.REVIEWING)
                    review_prompt = (
                        "Review this implementation from the supplied diff and test output only. "
                        "The remote directory is intentionally empty; do not inspect it and do not "
                        "request repository access. Return concise findings.\n\nDIFF:\n"
                        + diff[:200_000]
                        + "\n\nTESTS:\n"
                        + test.output[:50_000]
                    )
                    review = await self._claude.send(
                        AgentRequest(thread_id, review_prompt, context)
                    )
                    await message("claude", review.content)
                else:
                    await change_state(RunState.REVIEWING)
                    await message("claude", "审查通过：实现符合计划，测试结果有效。")
                await change_state(RunState.COMPLETED)
                await emit(EventType.RUN_COMPLETED, {"status": "completed"})
            except asyncio.CancelledError:
                if thread.state not in {RunState.COMPLETED, RunState.CANCELLED}:
                    thread.state = RunState.CANCELLED
                    run.state = RunState.CANCELLED
                    await db.commit()
                    await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CANCELLED.value})
            except Exception as exc:
                thread.state = RunState.FAILED
                run.state = RunState.FAILED
                run.output = str(exc)
                await db.commit()
                await emit(EventType.ERROR, {"message": str(exc)})

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
                has_remote_repo = agent == "claude" and bool(remote_settings.vps_repo_path)
                action = "edit_files" if agent == "codex" else "remote_edit_files" if has_remote_repo else "network_access"
                reason = "允许 Codex 在当前本地仓库中处理本轮请求" if agent == "codex" else "允许 VPS Claude 在已配置远端仓库中处理本轮请求" if has_remote_repo else "允许向 VPS Claude 发送本轮对话"
                if not await approve(action, reason):
                    thread.state = RunState.CREATED
                    run.state = RunState.CANCELLED
                    await db.commit()
                    await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})
                    return
                recent = (await db.scalars(
                    select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at.desc()).limit(20)
                )).all()
                transcript = "\n".join(f"{item.role}: {item.content}" for item in reversed(recent))
                request_prompt = (
                    "Continue this development conversation. Respond only as the selected agent. "
                    "Do not hand off to another agent or automatically advance a workflow.\n\n"
                    f"RECENT CONVERSATION:\n{transcript}\n\nCURRENT REQUEST:\n{prompt}"
                )
                context = {"workspace_path": workspace.path}
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
                try:
                    response = await self._stream_agent(adapter, AgentRequest(thread_id, request_prompt, context, attachments), agent, emit)
                except Exception:
                    if "session_id" not in context:
                        raise
                    context.pop("session_id")
                    response = await self._stream_agent(adapter, AgentRequest(thread_id, request_prompt, context, attachments), agent, emit)
                    db.add(AuditLog(workspace_id=workspace.id, thread_id=thread_id, event="agent.session.fallback", detail=f"agent={agent};previous={previous_session.external_session_id if previous_session else ''}"))
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
                    item = event.get("item")
                    if isinstance(item, dict) and event.get("type") == "item.completed" and item.get("type") == "agent_message":
                        text = str(item.get("text") or "")
                    elif isinstance(item, dict) and item.get("type") in {"command_execution", "file_change", "mcp_tool_call"}:
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
