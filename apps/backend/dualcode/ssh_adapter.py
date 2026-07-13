import asyncio
import json
import shlex
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import asyncssh

from .adapters import AgentAdapter, AgentCapabilities, AgentRequest, AgentResponse
from .security import validate_project_file


class RemoteRepositoryUnavailable(ValueError):
    """The configured VPS project path does not contain a Git repository yet."""


@dataclass(frozen=True)
class ClaudeSshConfig:
    host: str
    username: str
    known_hosts: Path
    client_keys: tuple[Path, ...] = ()
    port: int = 22
    remote_root: PurePosixPath = PurePosixPath("/tmp/dualcode-workbench")
    claude_executable: PurePosixPath = PurePosixPath("/usr/local/bin/claude")
    model: str = ""
    reasoning_effort: str = "medium"
    connect_timeout: float = 15
    run_timeout: float = 900

    def validate(self) -> None:
        if not self.host or any(char.isspace() for char in self.host):
            raise ValueError("invalid SSH host")
        if not self.username or any(char.isspace() for char in self.username):
            raise ValueError("invalid SSH username")
        if not 1 <= self.port <= 65535:
            raise ValueError("invalid SSH port")
        if not self.known_hosts.is_file():
            raise ValueError("known_hosts file is required")
        if not self.remote_root.is_absolute() or ".." in self.remote_root.parts:
            raise ValueError("remote_root must be an absolute normalized path")
        if not self.claude_executable.is_absolute() or ".." in self.claude_executable.parts:
            raise ValueError("claude_executable must be an absolute normalized path")
        for key in self.client_keys:
            if not key.is_file():
                raise ValueError(f"SSH client key not found: {key}")


@dataclass
class RemoteRun:
    connection: asyncssh.SSHClientConnection
    process: asyncssh.SSHClientProcess[str]
    remote_dir: PurePosixPath


class ClaudeSshAdapter(AgentAdapter):
    """Remote planning/review adapter with strict host validation and isolated uploads."""

    capabilities = AgentCapabilities(
        vision=True,
        supported_image_types=frozenset({"image/png", "image/jpeg", "image/webp"}),
        max_image_bytes=10 * 1024 * 1024,
        max_images_per_request=8,
        native_file_input=True,
    )

    def __init__(self, config: ClaudeSshConfig) -> None:
        config.validate()
        self.config = config
        self._runs: dict[str, RemoteRun] = {}

    async def _connect(self) -> asyncssh.SSHClientConnection:
        keys = [str(path) for path in self.config.client_keys] or None
        return await asyncio.wait_for(
            asyncssh.connect(
                self.config.host,
                port=self.config.port,
                username=self.config.username,
                known_hosts=str(self.config.known_hosts),
                client_keys=keys,
                password=None,
                agent_forwarding=False,
            ),
            timeout=self.config.connect_timeout,
        )

    def _remote_dir(self, request: AgentRequest, run_id: str) -> PurePosixPath:
        try:
            thread_id = str(uuid.UUID(request.thread_id))
        except ValueError as exc:
            raise ValueError("thread_id must be a UUID for remote execution") from exc
        return self.config.remote_root / thread_id / run_id

    async def _upload_explicit_attachments(
        self,
        connection: asyncssh.SSHClientConnection,
        request: AgentRequest,
        remote_dir: PurePosixPath,
    ) -> list[str]:
        if len(request.attachments) > self.capabilities.max_images_per_request:
            raise ValueError("too many remote attachments")
        remote_paths: list[str] = []
        async with connection.start_sftp_client() as sftp:
            for index, attachment in enumerate(request.attachments):
                local = attachment.local_path.resolve(strict=True)
                validate_project_file(local)
                if attachment.media_type not in self.capabilities.supported_image_types:
                    raise ValueError(f"unsupported remote attachment: {attachment.media_type}")
                if attachment.size > self.capabilities.max_image_bytes:
                    raise ValueError("remote attachment too large")
                suffix = local.suffix.lower()
                remote = remote_dir / f"attachment-{index}{suffix}"
                await sftp.put(str(local), str(remote))
                remote_paths.append(str(remote))
        return remote_paths

    async def stream(self, request: AgentRequest) -> AsyncIterator[str]:
        run_id = str(uuid.uuid4())
        upload_dir = self._remote_dir(request, run_id)
        configured_workspace = request.context.get("remote_workspace_path")
        work_dir = upload_dir
        if configured_workspace is not None:
            if not isinstance(configured_workspace, str):
                raise ValueError("remote_workspace_path must be a string")
            work_dir = PurePosixPath(configured_workspace)
            if not work_dir.is_absolute() or ".." in work_dir.parts:
                raise ValueError("remote workspace path must be absolute and normalized")
        connection = await self._connect()
        quoted_upload = shlex.quote(str(upload_dir))
        quoted_work = shlex.quote(str(work_dir))
        try:
            await connection.run(f"mkdir -p -- {quoted_upload}", check=True)
            if configured_workspace is not None:
                await connection.run(f"git -C {quoted_work} rev-parse --is-inside-work-tree", check=True)
            remote_paths = await self._upload_explicit_attachments(connection, request, upload_dir)
            allow_write = request.context.get("allow_remote_write") is True
            permission = "acceptEdits" if allow_write else "plan"
            tools = "Read,Edit,Write,Bash" if allow_write else "Read"
            command = (
                f"cd -- {quoted_work} && exec {shlex.quote(str(self.config.claude_executable))} "
                "--print --output-format stream-json --verbose "
                f"--permission-mode {permission} --tools {shlex.quote(tools)}"
            )
            session_id = request.context.get("session_id")
            if session_id:
                try:
                    session_id = str(uuid.UUID(str(session_id)))
                except ValueError as exc:
                    raise ValueError("invalid Claude session ID") from exc
                command += f" --resume {shlex.quote(session_id)}"
            if self.config.model:
                command += f" --model {shlex.quote(self.config.model)}"
            command += f" --effort {shlex.quote(self.config.reasoning_effort)}"
            process = await connection.create_process(command, encoding="utf-8")
            self._runs[run_id] = RemoteRun(connection, process, upload_dir)
            prompt = request.prompt
            if remote_paths:
                prompt += "\n\nExplicit task attachments:\n" + "\n".join(remote_paths)
            process.stdin.write(prompt)
            process.stdin.write_eof()
            async with asyncio.timeout(self.config.run_timeout):
                async for line in process.stdout:
                    yield line.rstrip()
                await process.wait(check=True)
        finally:
            self._runs.pop(run_id, None)
            try:
                await connection.run(f"rm -rf -- {quoted_upload}", check=False)
            finally:
                connection.close()
                await connection.wait_closed()

    async def send(self, request: AgentRequest) -> AgentResponse:
        chunks = [chunk async for chunk in self.stream(request)]
        result: list[str] = []
        session_id: str | None = None
        for chunk in chunks:
            try:
                event = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            session_id = event.get("session_id", session_id)
            if event.get("type") == "result" and isinstance(event.get("result"), str):
                result.append(event["result"])
        return AgentResponse(session_id or str(uuid.uuid4()), "\n".join(result))

    async def cancel(self, run_id: str) -> None:
        run = self._runs.pop(run_id, None)
        if not run:
            return
        run.process.terminate()
        try:
            await asyncio.wait_for(run.process.wait(), timeout=3)
        except TimeoutError:
            run.process.kill()
        run.connection.close()
        await run.connection.wait_closed()

    async def resume(self, run_id: str) -> AgentResponse:
        raise NotImplementedError(
            "remote resume requires a new prompt and stored Claude session ID"
        )

    async def health_check(self) -> bool:
        try:
            connection = await self._connect()
            executable = shlex.quote(str(self.config.claude_executable))
            result = await connection.run(f"{executable} --version", check=False, timeout=5)
            return result.exit_status == 0
        except (asyncssh.Error, OSError, TimeoutError):
            return False

    async def model_catalog(self) -> tuple[str, list[str]]:
        """Query Claude Code's account-aware /model catalog without project access."""
        connection = await self._connect()
        try:
            executable = shlex.quote(str(self.config.claude_executable))
            result = await connection.run(
                f"{executable} --print --output-format json '/model' < /dev/null",
                check=True,
                timeout=20,
            )
            payload = json.loads(result.stdout)
            message = payload.get("result", "")
            current_match = re.search(r"Current model:\s*([^\n]+)", message)
            available_match = re.search(r"Available:\s*(.+?)(?:,?\s+or a full model ID\.)", message)
            aliases = []
            if available_match:
                aliases = [part.strip() for part in available_match.group(1).split(",") if part.strip()]
            return (current_match.group(1).strip() if current_match else "", aliases)
        except (asyncssh.Error, OSError, TimeoutError, json.JSONDecodeError):
            return ("", [])
        finally:
            connection.close()
            await connection.wait_closed()

    async def repository_status(self, repository: PurePosixPath) -> dict[str, str]:
        if not repository.is_absolute() or ".." in repository.parts:
            raise ValueError("remote repository path must be absolute and normalized")
        connection = await self._connect()
        quoted = shlex.quote(str(repository))
        try:
            script = (
                f"if ! git -C {quoted} rev-parse --is-inside-work-tree >/dev/null 2>&1 "
                f"|| ! git -C {quoted} remote get-url origin >/dev/null 2>&1; then "
                "printf '__DUALCODE_REPOSITORY_NOT_READY__'; "
                "else "
                "printf 'true\\n'; "
                f"branch=$(git -C {quoted} symbolic-ref --short -q HEAD || true); printf '%s\\n' \"$branch\"; "
                f"head=$(git -C {quoted} rev-parse --verify HEAD 2>/dev/null || true); printf '%s\\n' \"$head\"; "
                f"git -C {quoted} remote get-url origin; "
                "fi"
            )
            result = await connection.run(script, check=True, timeout=15)
            if result.stdout.strip() == "__DUALCODE_REPOSITORY_NOT_READY__":
                raise RemoteRepositoryUnavailable("VPS repository has not been cloned yet")
            lines = result.stdout.splitlines()
            if len(lines) < 4 or lines[0].strip() != "true":
                raise ValueError("VPS path is not a Git repository")
            return {"branch": lines[1].strip(), "head": lines[2].strip(), "remote": lines[3].strip()}
        finally:
            connection.close()
            await connection.wait_closed()

    async def repository_update(self, repository: PurePosixPath, action: str, remote_url: str = "") -> str:
        if not repository.is_absolute() or ".." in repository.parts:
            raise ValueError("remote repository path must be absolute and normalized")
        if action not in {"provision", "repair_provision", "fetch", "pull"}:
            raise ValueError("unsupported remote Git action")
        connection = await self._connect()
        quoted = shlex.quote(str(repository))
        try:
            if action in {"provision", "repair_provision"}:
                if not remote_url or any(char in remote_url for char in "\r\n\0"):
                    raise ValueError("A valid remote URL is required to prepare the VPS repository")
                parent = shlex.quote(str(repository.parent))
                remote = shlex.quote(remote_url)
                if action == "repair_provision":
                    command = (
                        f"if git -C {quoted} rev-parse --is-inside-work-tree >/dev/null 2>&1 "
                        f"&& git -C {quoted} remote get-url origin >/dev/null 2>&1; then "
                        "printf 'Refusing to replace an existing valid Git repository' >&2; exit 65; "
                        f"fi; rm -rf -- {quoted} && mkdir -p -- {parent} && git clone -- {remote} {quoted}"
                    )
                else:
                    command = f"mkdir -p -- {parent} && git clone -- {remote} {quoted}"
            elif action == "fetch":
                command = f"git -C {quoted} fetch --prune"
            else:
                dirty = await connection.run(f"git -C {quoted} status --porcelain", check=True, timeout=15)
                if dirty.stdout.strip():
                    raise ValueError("Remote pull refused: VPS workspace has uncommitted changes")
                command = f"git -C {quoted} pull --ff-only"
            try:
                result = await connection.run(command, check=True, timeout=120)
            except asyncssh.ProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                raise ValueError(f"Remote Git {action} failed: {detail}") from exc
            return result.stdout.strip() or result.stderr.strip()
        finally:
            connection.close()
            await connection.wait_closed()
