import asyncio
import json
import re
import subprocess
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from .adapters import (
    AgentCapabilities,
    AgentRequest,
    AgentResponse,
    AgentStreamEvent,
    AgentStreamEventType,
)
from .cli_adapters import BaseCliAdapter, CliUnavailableError


class AppServerProtocolError(RuntimeError):
    pass


class CodexAppServerAdapter(BaseCliAdapter):
    """Persistent Codex app-server JSON-RPC client.

    The adapter emits a small normalized JSONL event contract so the scheduler
    is independent from Codex protocol revisions.
    """

    capabilities = AgentCapabilities(
        vision=True,
        supported_image_types=frozenset({"image/png", "image/jpeg", "image/webp"}),
        max_image_bytes=10 * 1024 * 1024,
        max_images_per_request=8,
        native_file_input=True,
    )
    _APPROVAL_METHODS = {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
    }

    def __init__(self, executable: str = "codex", timeout_seconds: float = 900,
                 model: str = "", reasoning_effort: str = "medium", permission_mode: str = "safe") -> None:
        super().__init__(executable, timeout_seconds)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.permission_mode = permission_mode
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._turn_queues: dict[str, asyncio.Queue[dict]] = {}
        self._thread_queues: dict[str, asyncio.Queue[dict]] = {}
        self._request_id = 0
        self._start_lock = asyncio.Lock()
        self._stderr_lines: deque[str] = deque(maxlen=20)

    def command_args(self, request: AgentRequest) -> list[str]:
        return ["app-server"]

    @staticmethod
    def _safe_stderr(text: str) -> str:
        text = re.sub(r"(?i)(authorization|api[_-]?key|token|secret)(\s*[:=]\s*)\S+", r"\1\2[redacted]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[redacted]", text)
        return text[:1000]

    async def _ensure_started(self, workspace: Path) -> None:
        async with self._start_lock:
            if self._process is not None and self._process.returncode is None:
                return
            executable = self.resolve_executable()
            if not executable:
                raise CliUnavailableError(f"未找到 Codex CLI 可执行文件：{self.executable}")
            self._process = await asyncio.create_subprocess_exec(
                executable, "app-server", cwd=workspace, env=self.safe_environment(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._stderr_lines.clear()
            self._reader_task = asyncio.create_task(self._read_messages())
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            await self._request("initialize", {
                "clientInfo": {"name": "dualcode-workbench", "title": "DualCode Workbench", "version": "0.1.5"},
                "capabilities": {"experimentalApi": True},
            })
            await self._notify("initialized", {})

    async def _write(self, value: dict) -> None:
        if not self._process or not self._process.stdin:
            raise AppServerProtocolError("Codex app-server is not running")
        self._process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
        await self._process.stdin.drain()

    async def _request(self, method: str, params: dict) -> dict:
        self._request_id += 1
        request_id = self._request_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"id": request_id, "method": method, "params": params})
        try:
            reply = await asyncio.wait_for(future, timeout=15)
        finally:
            self._pending.pop(request_id, None)
        if "error" in reply:
            raise AppServerProtocolError(str(reply["error"]))
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    async def _notify(self, method: str, params: dict) -> None:
        await self._write({"method": method, "params": params})

    async def _read_messages(self) -> None:
        assert self._process and self._process.stdout
        try:
            while line := await self._process.stdout.readline():
                try:
                    event = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if "id" in event and event.get("method") in self._APPROVAL_METHODS:
                    params = event.get("params") if isinstance(event.get("params"), dict) else {}
                    turn_id = str(params.get("turnId") or "")
                    thread_id = str(params.get("threadId") or "")
                    queue = self._turn_queues.get(turn_id) or self._thread_queues.get(thread_id)
                    if queue:
                        await queue.put(event)
                    else:
                        await self._write({"id": event["id"], "result": {"decision": "cancel"}})
                    continue
                if "id" in event:
                    if future := self._pending.get(event["id"]):
                        if not future.done():
                            future.set_result(event)
                    elif event.get("method"):
                        await self._write({
                            "id": event["id"],
                            "error": {"code": -32601, "message": "DualCode does not support this app-server request"},
                        })
                    continue
                params = event.get("params") if isinstance(event.get("params"), dict) else {}
                turn_id = str(params.get("turnId") or params.get("turn", {}).get("id") or "")
                if turn_id and (queue := self._turn_queues.get(turn_id)):
                    await queue.put(event)
                else:
                    thread_id = str(params.get("threadId") or params.get("thread", {}).get("id") or "")
                    if thread_id and (queue := self._thread_queues.get(thread_id)):
                        await queue.put(event)
        finally:
            process = self._process
            if process is not None and process.returncode is None:
                with suppress(Exception):
                    await process.wait()
            detail = " | ".join(self._stderr_lines)
            message = "Codex app-server 意外退出"
            if detail:
                message += f": {detail[-1200:]}"
            error = AppServerProtocolError(message)
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            for queue in self._turn_queues.values():
                await queue.put({"method": "transport/error", "params": {"message": str(error)}})
            self._process = None

    async def _drain_stderr(self) -> None:
        assert self._process and self._process.stderr
        while line := await self._process.stderr.readline():
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self._stderr_lines.append(self._safe_stderr(text))

    @staticmethod
    def _thread_id(result: dict) -> str:
        thread = result.get("thread")
        return str(thread.get("id")) if isinstance(thread, dict) else str(result.get("threadId") or result.get("id") or "")

    @staticmethod
    def _turn_id(result: dict) -> str:
        turn = result.get("turn")
        return str(turn.get("id")) if isinstance(turn, dict) else str(result.get("turnId") or result.get("id") or "")

    def _normalize(self, event: dict, thread_id: str) -> dict | None:
        method = event.get("method")
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        if method == "item/agentMessage/delta":
            return {"type": "agent_message.delta", "thread_id": thread_id, "text": str(params.get("delta") or "")}
        if method in {"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"}:
            return {"type": "activity.delta", "thread_id": thread_id, "item": {"id": params.get("itemId", "reasoning"), "type": "reasoning", "text": params.get("delta", "")}}
        if method in {"item/started", "item/completed"}:
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") in {"userMessage", "agentMessage", "user_message", "agent_message"}:
                return None
            return {"type": "activity.event", "thread_id": thread_id, "event": method, "item": item}
        if method == "item/commandExecution/outputDelta":
            return {"type": "terminal.delta", "thread_id": thread_id, "text": str(params.get("delta") or "")}
        if method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            if status not in {"completed", "success"}:
                failure = turn.get("error")
                if isinstance(failure, dict):
                    failure = failure.get("message")
                raise AppServerProtocolError(str(failure or f"Codex turn ended with status {status}"))
            return {"type": "turn.completed", "thread_id": thread_id, "turn": turn}
        if method == "transport/error":
            raise AppServerProtocolError(str(params.get("message")))
        return None

    async def stream(self, request: AgentRequest) -> AsyncIterator[str]:
        workspace = self.workspace(request)
        self.validate_attachments(request, workspace)
        await self._ensure_started(workspace)
        thread_id = request.context.get("session_id")
        if thread_id is not None and not isinstance(thread_id, str):
            raise ValueError("session_id must be a string")
        if not thread_id:
            approval_policy = "on-request" if self.permission_mode == "safe" else "never"
            legacy_sandbox = "danger-full-access" if self.permission_mode == "full_access" else "workspace-write"
            result = await self._request("thread/start", {
                "cwd": str(workspace), "approvalPolicy": approval_policy, "sandbox": legacy_sandbox,
                "model": self.model or None,
            })
            thread_id = self._thread_id(result)
            if not thread_id:
                raise AppServerProtocolError("thread/start returned no thread id")
        yield json.dumps({"type": "thread.started", "thread_id": thread_id})
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._thread_queues[thread_id] = queue
        inputs = [{"type": "text", "text": request.prompt}]
        inputs.extend({"type": "localImage", "path": str(item.local_path.resolve())} for item in request.attachments)
        approval_policy = "on-request" if self.permission_mode == "safe" else "never"
        sandbox_policy = ({"type": "dangerFullAccess"} if self.permission_mode == "full_access" else {
            "type": "workspaceWrite", "writableRoots": [str(workspace)], "networkAccess": False,
        })
        result = await self._request("turn/start", {
            "threadId": thread_id, "input": inputs, "cwd": str(workspace),
            "approvalPolicy": approval_policy, "sandboxPolicy": sandbox_policy,
            "model": self.model or None, "effort": self.reasoning_effort,
        })
        turn_id = self._turn_id(result)
        if not turn_id:
            raise AppServerProtocolError("turn/start returned no turn id")
        self._turn_queues[turn_id] = queue
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while True:
                    event = await queue.get()
                    if event.get("id") is not None and event.get("method") in self._APPROVAL_METHODS:
                        handler = request.context.get("approval_callback")
                        params = event.get("params") if isinstance(event.get("params"), dict) else {}
                        approved = bool(await handler(str(event["method"]), params)) if callable(handler) else False
                        if event["method"] == "item/permissions/requestApproval":
                            result = {"permissions": params.get("permissions", {}) if approved else {}, "scope": "turn"}
                        else:
                            result = {"decision": "accept" if approved else "decline"}
                        await self._write({"id": event["id"], "result": result})
                        continue
                    normalized = self._normalize(event, thread_id)
                    if normalized:
                        yield json.dumps(normalized, ensure_ascii=False)
                        if normalized["type"] == "turn.completed":
                            break
        except asyncio.CancelledError:
            with suppress(Exception):
                await self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
            raise
        finally:
            self._turn_queues.pop(turn_id, None)
            self._thread_queues.pop(thread_id, None)

    async def send(self, request: AgentRequest) -> AgentResponse:
        session_id = ""
        content: list[str] = []
        async for chunk in self.stream(request):
            event = json.loads(chunk)
            session_id = str(event.get("thread_id") or session_id)
            if event.get("type") == "agent_message.delta":
                content.append(str(event.get("text") or ""))
        return AgentResponse(session_id or str(uuid.uuid4()), "".join(content))

    async def stream_events(self, request: AgentRequest) -> AsyncIterator[AgentStreamEvent]:
        session_id = ""
        async for chunk in self.stream(request):
            event = json.loads(chunk)
            session_id = str(event.get("thread_id") or session_id)
            event_type = event.get("type")
            if event_type == "agent_message.delta":
                yield AgentStreamEvent(
                    AgentStreamEventType.DELTA,
                    session_id=session_id,
                    text=str(event.get("text") or ""),
                )
            elif event_type in {"activity.event", "activity.delta"}:
                item = event.get("item")
                if isinstance(item, dict):
                    yield AgentStreamEvent(
                        AgentStreamEventType.TOOL_EVENT,
                        session_id=session_id,
                        event=str(event.get("event") or ("delta" if event_type == "activity.delta" else "")),
                        item=item,
                    )
            elif event_type == "terminal.delta":
                yield AgentStreamEvent(
                    AgentStreamEventType.TERMINAL,
                    session_id=session_id,
                    text=str(event.get("text") or ""),
                )
            elif event_type == "turn.completed":
                yield AgentStreamEvent(AgentStreamEventType.FINAL, session_id=session_id)

    async def cancel(self, run_id: str) -> None:
        # Active streams issue turn/interrupt from their cancellation handler.
        return None

    async def resume(self, run_id: str) -> AgentResponse:
        raise NotImplementedError("resume requires a prompt and is performed by stream")

    async def close(self) -> None:
        process = self._process
        if process and process.returncode is None:
            process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), 3)
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
        self._process = None
