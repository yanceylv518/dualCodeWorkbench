import asyncio
import subprocess
import json
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from .adapters import AgentAdapter, AgentCapabilities, AgentRequest, AgentResponse
from .security import validate_project_file


class CliUnavailableError(RuntimeError):
    pass


class BaseCliAdapter(AgentAdapter):
    """Safe async subprocess adapter with no shell interpolation."""

    version_args = ("--version",)

    def __init__(self, executable: str, timeout_seconds: float = 900) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def resolve_executable(self) -> str | None:
        candidate = Path(self.executable).expanduser()
        if candidate.is_absolute() and candidate.is_file():
            return str(candidate)
        return shutil.which(self.executable)

    def safe_environment(self) -> dict[str, str]:
        allowed = {
            "PATH",
            "HOME",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
            "TEMP",
            "TMP",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
        }
        return {key: value for key, value in os.environ.items() if key.upper() in allowed}

    def workspace(self, request: AgentRequest) -> Path:
        raw = request.context.get("workspace_path")
        if not isinstance(raw, str):
            raise ValueError("workspace_path is required")
        workspace = Path(raw).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("workspace_path must be a directory")
        return workspace

    def validate_attachments(self, request: AgentRequest, workspace: Path) -> None:
        if len(request.attachments) > self.capabilities.max_images_per_request:
            raise ValueError("too many attachments")
        for attachment in request.attachments:
            path = attachment.local_path.resolve(strict=True)
            validate_project_file(path)
            if attachment.media_type not in self.capabilities.supported_image_types:
                raise ValueError(f"unsupported attachment type: {attachment.media_type}")
            if attachment.size > self.capabilities.max_image_bytes:
                raise ValueError("attachment too large")
            if workspace not in path.parents:
                root_value = request.context.get("attachment_root")
                if not isinstance(root_value, str):
                    raise PermissionError("attachment is outside the allowed workspace")
                attachment_root = Path(root_value).resolve(strict=True)
                if attachment_root not in path.parents:
                    raise PermissionError("attachment is outside the allowed attachment store")

    def command_args(self, request: AgentRequest) -> list[str]:
        raise NotImplementedError

    async def _spawn(self, run_id: str, request: AgentRequest) -> asyncio.subprocess.Process:
        executable = self.resolve_executable()
        if not executable:
            raise CliUnavailableError(f"CLI executable not found: {self.executable}")
        workspace = self.workspace(request)
        self.validate_attachments(request, workspace)
        process = await asyncio.create_subprocess_exec(
            executable,
            *self.command_args(request),
            cwd=workspace,
            env=self.safe_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._processes[run_id] = process
        assert process.stdin is not None
        process.stdin.write(request.prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        return process

    async def stream(self, request: AgentRequest) -> AsyncIterator[str]:
        run_id = str(uuid.uuid4())
        process = await self._spawn(run_id, request)
        assert process.stdout is not None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while line := await process.stdout.readline():
                    yield line.decode("utf-8", errors="replace").rstrip()
                exit_code = await process.wait()
            if exit_code != 0:
                assert process.stderr is not None
                error = (await process.stderr.read()).decode("utf-8", errors="replace")
                raise RuntimeError(error.strip() or f"CLI exited with {exit_code}")
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            self._processes.pop(run_id, None)

    async def send(self, request: AgentRequest) -> AgentResponse:
        chunks = [chunk async for chunk in self.stream(request)]
        return AgentResponse(str(uuid.uuid4()), "\n".join(chunks))

    async def cancel(self, run_id: str) -> None:
        process = self._processes.get(run_id)
        if not process or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def resume(self, run_id: str) -> AgentResponse:
        raise NotImplementedError("resume requires an adapter-specific stored session")

    async def health_check(self) -> bool:
        executable = self.resolve_executable()
        if not executable:
            return False
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *self.version_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self.safe_environment(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return await asyncio.wait_for(process.wait(), timeout=5) == 0
        except (OSError, TimeoutError):
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            return False


class CodexCliAdapter(BaseCliAdapter):
    capabilities = AgentCapabilities(
        vision=True,
        supported_image_types=frozenset({"image/png", "image/jpeg", "image/webp"}),
        max_image_bytes=10 * 1024 * 1024,
        max_images_per_request=8,
        native_file_input=True,
    )

    def __init__(self, executable: str = "codex", timeout_seconds: float = 900, model: str = "", reasoning_effort: str = "medium") -> None:
        super().__init__(executable, timeout_seconds)
        self.model = model
        self.reasoning_effort = reasoning_effort

    def command_args(self, request: AgentRequest) -> list[str]:
        session_id = request.context.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise ValueError("session_id must be a string")
        args = ["exec"]
        # DualCode gates the run with its own approval model. A nested Codex
        # Windows sandbox cannot initialise reliably under a hidden sidecar.
        if session_id:
            args.extend(["resume", "--json", "--skip-git-repo-check", "-c", 'sandbox_mode="danger-full-access"'])
        else:
            args.extend(["--json", "--sandbox", "danger-full-access", "--skip-git-repo-check"])
        if self.model:
            args.extend(["--model", self.model])
        args.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
        for attachment in request.attachments:
            args.extend(["--image", str(attachment.local_path.resolve())])
        if session_id:
            args.append(session_id)
        args.append("-")
        return args

    async def send(self, request: AgentRequest) -> AgentResponse:
        chunks = [chunk async for chunk in self.stream(request)]
        thread_id: str | None = None
        messages: list[str] = []
        for chunk in chunks:
            try:
                event = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
            item = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                messages.append(item["text"])
        return AgentResponse(thread_id or str(uuid.uuid4()), "\n".join(messages))


class ClaudeCliAdapter(BaseCliAdapter):
    capabilities = AgentCapabilities(native_file_input=False)

    def __init__(self, executable: str = "claude", timeout_seconds: float = 900, model: str = "", reasoning_effort: str = "medium") -> None:
        super().__init__(executable, timeout_seconds)
        self.model = model
        self.reasoning_effort = reasoning_effort

    def command_args(self, request: AgentRequest) -> list[str]:
        args = [
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "plan",
            "--tools",
            "",
        ]
        if self.model:
            args.extend(["--model", self.model])
        args.extend(["--effort", self.reasoning_effort])
        session_id = request.context.get("session_id")
        if session_id:
            if not isinstance(session_id, str):
                raise ValueError("session_id must be a string")
            args.extend(["--resume", session_id])
        return args

    async def send(self, request: AgentRequest) -> AgentResponse:
        chunks = [chunk async for chunk in self.stream(request)]
        text_parts: list[str] = []
        session_id: str | None = None
        for chunk in chunks:
            try:
                event = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            session_id = event.get("session_id", session_id)
            if event.get("type") == "result" and isinstance(event.get("result"), str):
                text_parts.append(event["result"])
        return AgentResponse(session_id or str(uuid.uuid4()), "\n".join(text_parts))
