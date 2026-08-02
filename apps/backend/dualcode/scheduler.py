import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from .adapters import (
    AgentAttachment,
    AgentRequest,
    AgentResponse,
    AgentStreamEventType,
    MockClaudeAdapter,
    MockCodexAdapter,
)
from .approvals import approval_gate
from .cli_adapters import ClaudeCliAdapter
from .codex_app_server import CodexAppServerAdapter
from .connections import manager
from .collaboration_audit import RoutingDecisionDetail, build_routing_decision_audit
from .collaboration_orchestrator import (
    ReviewTurnResult,
    SnapshotEvidence,
    StageCallbacks,
    advance as advance_collaboration,
    ensure_contract_draft,
    execute_pipeline,
    resume as resume_collaboration,
    start_run as start_collaboration_run,
)
from .collaboration_protocol import CollaborationState
from .context_budget import build_memory_section, build_recent_transcript, truncate_contract
from .database import SessionLocal
from .document_text import DOCX_MEDIA_TYPE, extract_docx_text
from .events import AgentEvent, EventType
from .git_service import GitService
from .models import (
    Approval,
    AgentRun,
    AgentSession,
    Attachment,
    AuditLog,
    CollaborationRun,
    FileChange,
    HandoffPackage,
    Message,
    MemoryFact,
    ProjectGovernance,
    RunState,
    Thread,
    TaskContract,
    TestRun,
    Workspace,
)
from .config import settings
from .handoff_compiler import compile_handoff_v2
from .handoff_prompt import handoff_prompt
from .memory_service import snapshot_thread_facts
from .relay_service import (
    RelayRemoteSpec,
    build_relay_sync_audit,
    cleanup_shadow_ref,
    create_shadow_snapshot,
    push_shadow_ref,
)
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig
from .runtime_settings import AgentSettings, agent_settings_store
from .workspace_remote import derived_repository_path, workspace_remote_store
from .test_executor import TestCommand, TestExecutor
from .task_classifier import RoutingDecision, classify


@dataclass(frozen=True)
class AgentTurnResult:
    agent_run_id: str
    status: str
    content: str = ""
    error: str = ""


ApprovalLifecycle = Callable[[str, str, bool | None], Awaitable[None]]


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

    def codex_protocol_diagnostics(self) -> dict[str, int]:
        """Method names the live app-server adapter received but did not map."""
        return dict(getattr(self._codex, "unhandled_methods", {}) or {})

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

    async def ensure_relay_sync_approval(
        self,
        db,
        thread_id: str,
        emit,
        approval_lifecycle: ApprovalLifecycle | None = None,
    ) -> bool:
        """Require the first relay sync approval and reuse durable thread grants."""

        action = "relay_shadow_sync"
        if await self._has_thread_grant(db, thread_id, action):
            return True
        item = Approval(
            thread_id=thread_id,
            action=action,
            reason="允许本任务自动同步影子快照到 VPS？",
        )
        db.add(item)
        await db.flush()
        approval_gate.prepare(item.id)
        await db.commit()
        await emit(
            EventType.APPROVAL_REQUIRED,
            {"id": item.id, "action": action, "reason": item.reason},
        )
        if approval_lifecycle:
            await approval_lifecycle(action, item.reason, None)
        approved = await approval_gate.wait(item.id)
        if approval_lifecycle:
            await approval_lifecycle(action, item.reason, approved)
        return approved

    async def _shared_memory_prompt(self, db, workspace: Workspace, thread: Thread) -> str:
        if not agent_settings_store.load().smart_collaboration_enabled:
            return ""
        await snapshot_thread_facts(db, workspace, thread)
        facts = (
            await db.scalars(
                select(MemoryFact).where(
                    MemoryFact.workspace_id == workspace.id,
                    (
                        (MemoryFact.thread_id == thread.id)
                        | MemoryFact.thread_id.is_(None)
                    ),
                    MemoryFact.invalidated_at.is_(None),
                )
            )
        ).all()
        rendered = build_memory_section(list(facts))
        return f"SHARED MEMORY:\n{rendered}\n\n" if rendered else ""

    async def start(self, thread_id: str, prompt: str, mode: str, attachment_ids: list[str] | None = None) -> str:
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            raise RuntimeError("当前任务已有 Agent 正在运行")
        run_id = str(uuid.uuid4())
        self._tasks[thread_id] = asyncio.create_task(self._execute(thread_id, run_id, prompt, mode, attachment_ids or []))
        return run_id

    async def start_handoff_review(
        self, thread_id: str, handoff_id: str
    ) -> str:
        if thread_id in self._tasks and not self._tasks[thread_id].done():
            raise RuntimeError("当前任务已有 Agent 正在运行")
        run_id = str(uuid.uuid4())
        self._tasks[thread_id] = asyncio.create_task(
            self._execute_handoff_review(thread_id, run_id, handoff_id)
        )
        return run_id

    async def start_collaboration_run(
        self,
        thread_id: str,
        collaboration_run_id: str,
        prompt: str,
    ) -> str:
        """Continue an API-created collaboration run in the background."""

        if thread_id in self._tasks and not self._tasks[thread_id].done():
            raise RuntimeError("当前任务已有 Agent 正在运行")
        self._tasks[thread_id] = asyncio.create_task(
            self._execute_smart_collaboration(
                thread_id,
                prompt,
                [],
                None,
                collaboration_run_id=collaboration_run_id,
            )
        )
        return collaboration_run_id

    def _relay_remote_spec(self, repository_path: str) -> RelayRemoteSpec:
        return RelayRemoteSpec(
            host=self.runtime.claude_ssh_host,
            username=self.runtime.claude_ssh_username,
            port=self.runtime.claude_ssh_port,
            repository_path=repository_path,
            known_hosts=self.runtime.claude_ssh_known_hosts,
            client_key=self.runtime.claude_ssh_client_key,
        )

    async def _execute_handoff_review(
        self, thread_id: str, run_id: str, handoff_id: str
    ) -> None:
        """Synchronize and review an immutable snapshot without touching VPS HEAD."""

        worktree = None
        repository: Path | None = None
        remote_spec: RelayRemoteSpec | None = None
        workspace_id = ""

        async def emit(kind: EventType, payload: dict[str, object]) -> None:
            await manager.publish(
                AgentEvent(
                    type=kind,
                    thread_id=thread_id,
                    run_id=run_id,
                    payload=payload,
                )
            )

        try:
            async with SessionLocal() as db:
                thread = await db.get(Thread, thread_id)
                item = await db.get(HandoffPackage, handoff_id)
                workspace = (
                    await db.get(Workspace, thread.workspace_id) if thread else None
                )
                if (
                    not thread
                    or not workspace
                    or not item
                    or item.thread_id != thread_id
                    or item.recipient != "claude"
                    or item.purpose != "review"
                ):
                    raise ValueError("未找到可发送给 Claude 的审查交接包")
                if not isinstance(self._claude, ClaudeSshAdapter):
                    raise ValueError("智能审查要求已配置可用的 VPS Claude SSH")
                remote_settings = workspace_remote_store.get(workspace.id)
                remote_path = remote_settings.vps_repo_path
                if not remote_path:
                    remote_path = derived_repository_path(
                        self.runtime.claude_ssh_projects_root,
                        remote_settings.remote_url,
                        workspace.name,
                    )
                if not remote_path:
                    raise ValueError("尚未配置或发现 VPS 仓库路径")
                workspace_id = workspace.id
                if not await self.ensure_relay_sync_approval(
                    db, thread_id, emit
                ):
                    raise PermissionError("用户取消了影子快照同步")

                repository = Path(workspace.path).resolve(strict=True)
                snapshot = await create_shadow_snapshot(repository)
                remote_spec = self._relay_remote_spec(remote_path)
                try:
                    await push_shadow_ref(
                        repository,
                        snapshot.snapshot_sha,
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        remote_spec=remote_spec,
                    )
                except Exception as exc:
                    db.add(
                        build_relay_sync_audit(
                            workspace_id=workspace.id,
                            thread_id=thread_id,
                            snapshot=snapshot,
                            succeeded=False,
                            error=str(exc),
                        )
                    )
                    await db.commit()
                    raise
                db.add(
                    build_relay_sync_audit(
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        snapshot=snapshot,
                        succeeded=True,
                    )
                )
                payload = json.loads(item.payload)
                payload["repository"]["base_sha"] = snapshot.base_sha
                payload["repository"]["snapshot_sha"] = snapshot.snapshot_sha
                item.payload = json.dumps(payload, ensure_ascii=False)
                await db.commit()
                worktree = await self._claude.create_review_worktree(
                    PurePosixPath(remote_path),
                    thread_id=thread_id,
                    run_id=run_id,
                    snapshot_sha=snapshot.snapshot_sha,
                )
                item.status = "SENT"
                db.add(
                    AuditLog(
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        event="handoff.sent",
                        detail=(
                            f"handoff={item.id};recipient=claude;run={run_id}"
                        ),
                    )
                )
                await db.commit()
                prompt = handoff_prompt(item)

            await self._execute_chat(
                thread_id,
                run_id,
                prompt,
                "claude",
                [],
                remote_workspace_path=str(worktree.path),
                allow_remote_write=False,
                skip_remote_approval=True,
            )
        except Exception as exc:
            message = f"无法启动隔离审查：{str(exc)[:300]}"
            await emit(EventType.ERROR, {"message": message})
            async with SessionLocal() as db:
                thread = await db.get(Thread, thread_id)
                if thread:
                    await self._record_system_message(db, thread_id, message)
                    await db.commit()
        finally:
            if worktree and isinstance(self._claude, ClaudeSshAdapter):
                try:
                    warnings = await self._claude.remove_review_worktree(worktree)
                    for warning in warnings:
                        await emit(EventType.ERROR, {"message": warning})
                except Exception as exc:
                    await emit(
                        EventType.ERROR,
                        {"message": f"VPS 审查 worktree 清理失败：{str(exc)[:200]}"},
                    )
            if repository and remote_spec:
                warnings = await cleanup_shadow_ref(
                    repository,
                    workspace_id=workspace_id,
                    thread_id=thread_id,
                    remote_spec=remote_spec,
                )
                for warning in warnings:
                    await emit(EventType.ERROR, {"message": warning})

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
        if mode != "smart":
            await self._execute_chat(thread_id, run_id, prompt, mode, attachment_ids)
            return

        decision = classify(prompt)
        agent = self._agent_for_decision(decision)
        await self._record_smart_route(thread_id, decision)
        if decision.dual_agent:
            await self._execute_smart_collaboration(
                thread_id, prompt, attachment_ids, decision
            )
            return
        await self._execute_chat(thread_id, run_id, prompt, agent, attachment_ids)
        await self._upgrade_after_diff(thread_id, run_id, agent, decision)

    async def _execute_smart_collaboration(
        self,
        thread_id: str,
        prompt: str,
        attachment_ids: list[str],
        decision: RoutingDecision | None,
        *,
        collaboration_run_id: str | None = None,
    ) -> None:
        """Run the C5 implementation/review loop with existing side effects."""

        async with SessionLocal() as db:
            thread = await db.get(Thread, thread_id)
            workspace = (
                await db.get(Workspace, thread.workspace_id) if thread else None
            )
            if not thread or not workspace:
                return
            if collaboration_run_id:
                collaboration = await db.get(
                    CollaborationRun, collaboration_run_id
                )
                if (
                    not collaboration
                    or collaboration.thread_id != thread_id
                    or collaboration.workspace_id != workspace.id
                ):
                    return
            else:
                if decision is None:
                    return
                contract = await db.scalar(
                    select(TaskContract).where(TaskContract.thread_id == thread_id)
                )
                if contract is None:
                    contract = TaskContract(thread_id=thread_id)
                    db.add(contract)
                if not (contract.goal or "").strip():
                    contract.goal = prompt.strip()
                ensure_contract_draft(contract, decision)
                await db.flush()
                collaboration = await start_collaboration_run(
                    db, workspace, thread, decision=decision
                )
                await db.commit()
            async def emit(kind: EventType, payload: dict[str, object]) -> None:
                await manager.publish(
                    AgentEvent(
                        type=kind,
                        thread_id=thread_id,
                        run_id=collaboration.id,
                        payload=payload,
                    )
                )

            async def approval_lifecycle(
                action: str, reason: str, approved: bool | None
            ) -> None:
                del action
                if approved is None:
                    await advance_collaboration(
                        db,
                        collaboration,
                        CollaborationState.WAITING_APPROVAL,
                        reason=reason,
                    )
                elif approved:
                    await resume_collaboration(
                        db, collaboration, reason="审批通过，继续当前阶段"
                    )
                else:
                    await resume_collaboration(
                        db, collaboration, reason="审批未通过，结束当前操作"
                    )
                await db.commit()

            async def run_codex(
                current_prompt: str, stage: CollaborationState
            ) -> AgentTurnResult:
                collaboration.current_agent = "codex"
                await db.commit()
                return await self._execute_chat(
                    thread_id,
                    str(uuid.uuid4()),
                    current_prompt,
                    "codex",
                    attachment_ids if stage is CollaborationState.IMPLEMENTING else [],
                    approval_lifecycle=approval_lifecycle,
                )

            async def run_tests() -> bool | None:
                runtime = agent_settings_store.load()
                if not runtime.test_executable:
                    return None

                async def on_output(channel: str, text: str) -> None:
                    await emit(
                        EventType.TERMINAL_OUTPUT,
                        {"channel": channel, "text": text},
                    )

                result = await self._tests.execute(
                    command=TestCommand(
                        executable=Path(runtime.test_executable),
                        arguments=tuple(runtime.test_arguments),
                        cwd=Path(workspace.path),
                    ),
                    allowed_root=Path(workspace.path),
                    on_output=on_output,
                )
                db.add(
                    TestRun(
                        thread_id=thread_id,
                        command=" ".join(result.command)[:500],
                        output=(result.stdout + result.stderr),
                        exit_code=result.exit_code,
                    )
                )
                await db.commit()
                return result.exit_code == 0 and not result.timed_out

            async def sync_snapshot() -> SnapshotEvidence:
                remote_settings = workspace_remote_store.get(workspace.id)
                remote_path = remote_settings.vps_repo_path or derived_repository_path(
                    self.runtime.claude_ssh_projects_root,
                    remote_settings.remote_url,
                    workspace.name,
                )
                if not remote_path:
                    raise ValueError("尚未配置或发现 VPS 仓库路径")
                if not await self.ensure_relay_sync_approval(
                    db,
                    thread_id,
                    emit,
                    approval_lifecycle=approval_lifecycle,
                ):
                    raise PermissionError("用户未批准影子快照同步")
                repository = Path(workspace.path).resolve(strict=True)
                snapshot = await create_shadow_snapshot(repository)
                try:
                    await push_shadow_ref(
                        repository,
                        snapshot.snapshot_sha,
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        remote_spec=self._relay_remote_spec(remote_path),
                    )
                except Exception as exc:
                    db.add(
                        build_relay_sync_audit(
                            workspace_id=workspace.id,
                            thread_id=thread_id,
                            snapshot=snapshot,
                            succeeded=False,
                            error=str(exc),
                        )
                    )
                    await db.commit()
                    raise
                db.add(
                    build_relay_sync_audit(
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        snapshot=snapshot,
                        succeeded=True,
                    )
                )
                await db.commit()
                return SnapshotEvidence(snapshot.base_sha, snapshot.snapshot_sha)

            async def run_review(
                prompt_suffix: str, snapshot: SnapshotEvidence
            ) -> ReviewTurnResult:
                if not isinstance(self._claude, ClaudeSshAdapter):
                    raise ValueError("智能审查要求已配置可用的 VPS Claude SSH")
                remote_settings = workspace_remote_store.get(workspace.id)
                remote_path = remote_settings.vps_repo_path or derived_repository_path(
                    self.runtime.claude_ssh_projects_root,
                    remote_settings.remote_url,
                    workspace.name,
                )
                if not remote_path:
                    raise ValueError("尚未配置或发现 VPS 仓库路径")
                payload = await compile_handoff_v2(
                    db,
                    workspace,
                    thread,
                    purpose="review",
                    sender="codex",
                    recipient="claude",
                )
                payload.repository.base_sha = snapshot.base_sha
                payload.repository.snapshot_sha = snapshot.snapshot_sha
                package = HandoffPackage(
                    workspace_id=workspace.id,
                    thread_id=thread_id,
                    recipient="claude",
                    purpose="review",
                    payload=payload.model_dump_json(by_alias=True),
                    status="PREPARED",
                )
                db.add(package)
                await db.flush()
                await db.commit()
                worktree = None
                repository = Path(workspace.path).resolve(strict=True)
                remote_spec = self._relay_remote_spec(remote_path)
                try:
                    worktree = await self._claude.create_review_worktree(
                        PurePosixPath(remote_path),
                        thread_id=thread_id,
                        run_id=collaboration.id,
                        snapshot_sha=snapshot.snapshot_sha,
                    )
                    collaboration.current_agent = "claude"
                    package.status = "SENT"
                    await db.commit()
                    result = await self._execute_chat(
                        thread_id,
                        str(uuid.uuid4()),
                        f"{handoff_prompt(package)}\n\n{prompt_suffix}",
                        "claude",
                        [],
                        remote_workspace_path=str(worktree.path),
                        allow_remote_write=False,
                        skip_remote_approval=True,
                        expose_response=False,
                    )
                    if result.status != "completed":
                        raise RuntimeError(result.error or "Claude 审查轮次未完成")
                    return ReviewTurnResult(result.content, package.id)
                finally:
                    if worktree:
                        await self._claude.remove_review_worktree(worktree)
                    await cleanup_shadow_ref(
                        repository,
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        remote_spec=remote_spec,
                    )

            async def record_system(text: str) -> None:
                await self._record_system_message(db, thread_id, text)
                await db.commit()

            async def record_review(text: str) -> None:
                db.add(Message(thread_id=thread_id, role="claude", content=text))
                await db.commit()
                await manager.publish(
                    AgentEvent(
                        type=EventType.MESSAGE_CREATED,
                        thread_id=thread_id,
                        run_id=collaboration.id,
                        payload={"role": "claude", "content": text},
                    )
                )

            callbacks = StageCallbacks(
                run_codex=run_codex,
                run_tests=run_tests,
                sync_snapshot=sync_snapshot,
                run_review=run_review,
                record_system=record_system,
                record_review=record_review,
            )
            try:
                await execute_pipeline(
                    db,
                    collaboration,
                    initial_prompt=prompt,
                    callbacks=callbacks,
                )
                await db.commit()
            except Exception as exc:
                if collaboration.state not in {
                    CollaborationState.BLOCKED.value,
                    CollaborationState.WAITING_USER.value,
                    CollaborationState.CANCELLED.value,
                    CollaborationState.COMPLETED.value,
                }:
                    collaboration.error = str(exc)[:500]
                    await advance_collaboration(
                        db,
                        collaboration,
                        CollaborationState.BLOCKED,
                        reason=f"协作阶段失败：{str(exc)[:200]}",
                    )
                    await db.commit()
                await emit(EventType.ERROR, {"message": str(exc)[:300]})

    @staticmethod
    def _agent_for_decision(decision: RoutingDecision) -> str:
        return "claude" if decision.primary_agent.startswith("Claude") else "codex"

    async def _record_system_message(self, db, thread_id: str, content: str) -> None:
        message = Message(thread_id=thread_id, role="system", content=content)
        db.add(message)
        await db.flush()
        await manager.publish(
            AgentEvent(
                type=EventType.MESSAGE_CREATED,
                thread_id=thread_id,
                payload={"id": message.id, "role": "system", "content": content},
            )
        )

    async def _record_smart_route(
        self, thread_id: str, decision: RoutingDecision
    ) -> None:
        async with SessionLocal() as db:
            thread = await db.get(Thread, thread_id)
            if not thread:
                return
            db.add(
                build_routing_decision_audit(
                    thread.workspace_id,
                    thread_id,
                    RoutingDecisionDetail(
                        category=decision.category,
                        primary_agent=decision.primary_agent,
                        collaborator=decision.collaborator,
                        reason="；".join(decision.reasons),
                    ),
                )
            )
            reason = "；".join(decision.reasons)
            await self._record_system_message(
                db,
                thread_id,
                f"智能路由：{decision.label} → {decision.primary_agent}（{reason}）",
            )
            await db.commit()

    async def _prepare_review_handoff(
        self,
        thread_id: str,
        run_id: str,
        agent: str,
        decision: RoutingDecision,
    ) -> None:
        async with SessionLocal() as db:
            thread = await db.get(Thread, thread_id)
            run = await db.get(AgentRun, run_id)
            workspace = await db.get(Workspace, thread.workspace_id) if thread else None
            if not thread or not workspace or not run or run.state != RunState.COMPLETED:
                return
            recipient = "claude" if agent == "codex" else "codex"
            try:
                payload = await compile_handoff_v2(
                    db,
                    workspace,
                    thread,
                    purpose="review",
                    sender=agent,
                    recipient=recipient,
                )
                db.add(
                    HandoffPackage(
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        recipient=recipient,
                        purpose="review",
                        payload=payload.model_dump_json(by_alias=True),
                        status="PREPARED",
                    )
                )
                db.add(
                    AuditLog(
                        workspace_id=workspace.id,
                        thread_id=thread_id,
                        event="handoff.prepared",
                        detail=(
                            f"recipient={recipient};purpose=review;"
                            f"category={decision.category}"
                        ),
                    )
                )
                message = f"已准备审查交接：{agent} → {recipient}"
            except Exception as exc:
                message = f"未能准备审查交接：{str(exc)[:160]}"
            await self._record_system_message(db, thread_id, message)
            await db.commit()

    async def _upgrade_after_diff(
        self,
        thread_id: str,
        run_id: str,
        agent: str,
        decision: RoutingDecision,
    ) -> bool:
        async with SessionLocal() as db:
            thread = await db.get(Thread, thread_id)
            run = await db.get(AgentRun, run_id)
            if not thread or not run or run.state != RunState.COMPLETED:
                return False
            changed_files = list(
                await db.scalars(
                    select(FileChange).where(FileChange.thread_id == thread_id)
                )
            )
            changed_count = len(changed_files)
            if changed_count <= 5:
                return False
            reason = f"事后 Diff 升级：{changed_count} 个文件"
            db.add(
                build_routing_decision_audit(
                    thread.workspace_id,
                    thread_id,
                    RoutingDecisionDetail(
                        category=decision.category,
                        primary_agent=decision.primary_agent,
                        collaborator=decision.collaborator,
                        reason=reason,
                    ),
                )
            )
            await self._record_system_message(
                db, thread_id, f"{reason}，已升级为双 Agent 审查"
            )
            await db.commit()
        await self._prepare_review_handoff(
            thread_id, run_id, agent, decision
        )
        return True

    async def _execute_chat(
        self,
        thread_id: str,
        run_id: str,
        prompt: str,
        agent: str,
        attachment_ids: list[str],
        *,
        remote_workspace_path: str | None = None,
        allow_remote_write: bool | None = None,
        skip_remote_approval: bool = False,
        approval_lifecycle: ApprovalLifecycle | None = None,
        expose_response: bool = True,
    ) -> AgentTurnResult:
        """Run one turn for the selected agent without advancing an orchestration pipeline."""
        async with SessionLocal() as db:
            thread = await db.scalar(select(Thread).where(Thread.id == thread_id))
            if not thread:
                return AgentTurnResult(run_id, "failed", error="未找到任务会话")
            workspace = await db.get(Workspace, thread.workspace_id)
            if not workspace:
                return AgentTurnResult(run_id, "failed", error="未找到项目")
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
                if not expose_response and kind in {
                    EventType.AGENT_DELTA,
                    EventType.MESSAGE_CREATED,
                }:
                    return
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
                if approval_lifecycle:
                    await approval_lifecycle(action, reason, None)
                approved = await approval_gate.wait(item.id)
                if approval_lifecycle:
                    await approval_lifecycle(action, reason, approved)
                return approved

            await emit(EventType.RUN_STATE_CHANGED, {"state": run_state.value, "agent": agent})
            try:
                remote_settings = workspace_remote_store.get(workspace.id)
                if not remote_settings.vps_repo_path:
                    runtime = agent_settings_store.load()
                    remote_settings = remote_settings.model_copy(update={"vps_repo_path": derived_repository_path(runtime.claude_ssh_projects_root, remote_settings.remote_url, workspace.name)})
                has_remote_repo = agent == "claude" and bool(
                    remote_workspace_path or remote_settings.vps_repo_path
                )
                action = "remote_edit_files" if has_remote_repo else "network_access"
                reason = "允许 VPS Claude 在已配置远端仓库中处理本轮请求" if has_remote_repo else "允许向 VPS Claude 发送本轮对话"
                if (
                    agent != "codex"
                    and not skip_remote_approval
                    and not await approve(action, reason)
                ):
                    thread.state = RunState.CREATED
                    run.state = RunState.CANCELLED
                    await db.commit()
                    await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})
                    return AgentTurnResult(run_id, "cancelled")
                recent = await db.stream_scalars(
                    select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at.desc())
                )
                transcript = await build_recent_transcript(recent)
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
                contract_text = truncate_contract(json.dumps(governance_context, ensure_ascii=False))
                memory_text = await self._shared_memory_prompt(db, workspace, thread)
                request_prompt = (
                    "Continue this development conversation. Respond only as the selected agent. "
                    "Do not hand off to another agent or automatically advance a workflow. "
                    "Image generation is not available in DualCode; do not invoke imageGeneration or claim that an image was generated.\n\n"
                    "This is production product development, not a demo. Do not use temporary, simulated, hard-coded, bypass, or unsustainable architecture merely to complete the current feature. "
                    "Identify requirements not covered by the implementation, potential problems, regression risks, and missing evidence. "
                    "If the existing architecture is insufficient, propose a formal architectural change instead of disguising a temporary patch as complete.\n\n"
                    f"PROJECT AND TASK CONTRACT:\n{contract_text}\n\n"
                    f"{memory_text}"
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
                            content = local_path.read_text(encoding="utf-8", errors="replace")
                            text_attachments.append(f"ATTACHMENT {item.name}:\n{content[:200_000]}")
                            continue
                        if item.media_type == DOCX_MEDIA_TYPE:
                            content = extract_docx_text(local_path)
                            text_attachments.append(f"ATTACHMENT {item.name}:\n{content[:200_000]}")
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
                    context["remote_workspace_path"] = (
                        remote_workspace_path or remote_settings.vps_repo_path
                    )
                    context["allow_remote_write"] = (
                        True if allow_remote_write is None else allow_remote_write
                    )
                adapter = self._codex if agent == "codex" else self._claude
                if agent == "codex":
                    run.before_diff = await self._git.diff(Path(workspace.path))
                    await db.commit()
                try:
                    response = await self._stream_agent(adapter, AgentRequest(thread_id, request_prompt, context, attachments), agent, emit)
                except Exception as exc:
                    # A timeout may follow an in-flight command or file edit.
                    # Replaying the prompt could duplicate irreversible side
                    # effects, so only ordinary stale-session failures receive
                    # the legacy fresh-thread fallback.
                    if "session_id" not in context or isinstance(
                        getattr(exc, "context", None), dict
                    ):
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
                if expose_response:
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
                return AgentTurnResult(run_id, "completed", content=response.content)
            except asyncio.CancelledError:
                thread.state = RunState.CREATED
                run.state = RunState.CANCELLED
                await db.commit()
                await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})
                return AgentTurnResult(run_id, "cancelled")
            except Exception as exc:
                thread.state = RunState.CREATED
                run.state = RunState.FAILED
                error_message = f"Agent 运行失败：{exc}"
                run.output = error_message
                failure_context = getattr(exc, "context", None)
                if isinstance(failure_context, dict):
                    run.failure_kind = str(
                        failure_context.get("failure_kind") or "agent_failure"
                    )
                    run.failure_context = json.dumps(
                        failure_context, ensure_ascii=False
                    )
                    db.add(
                        AuditLog(
                            workspace_id=workspace.id,
                            thread_id=thread_id,
                            event="agent.turn.failed",
                            detail=run.failure_context,
                        )
                    )
                await db.commit()
                await emit(EventType.ERROR, {"message": error_message})
                await emit(EventType.RUN_STATE_CHANGED, {"state": RunState.CREATED.value})
                return AgentTurnResult(run_id, "failed", error=error_message)

    async def _stream_agent(self, adapter, request: AgentRequest, agent: str, emit) -> AgentResponse:
        session_id = ""
        content_parts: list[str] = []
        async for event in adapter.stream_events(request):
            session_id = event.session_id or session_id
            if event.type == AgentStreamEventType.DELTA and event.text:
                content_parts.append(event.text)
                await emit(EventType.AGENT_DELTA, {"agent": agent, "text": event.text})
            elif event.type == AgentStreamEventType.TOOL_EVENT:
                await emit(
                    EventType.TOOL_EVENT,
                    {"agent": agent, "event": event.event, "item": event.item},
                )
            elif event.type == AgentStreamEventType.TERMINAL and event.text:
                await emit(EventType.TERMINAL_OUTPUT, {"channel": agent, "text": event.text})
        return AgentResponse(session_id or str(uuid.uuid4()), "".join(content_parts))


scheduler = RunScheduler()
