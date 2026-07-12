import asyncio
import subprocess
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path


OutputCallback = Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True)
class TestCommand:
    __test__ = False
    executable: Path
    arguments: tuple[str, ...]
    cwd: Path
    timeout_seconds: float = 300

    def validate(self, allowed_root: Path) -> None:
        executable = self.executable.resolve(strict=True)
        cwd = self.cwd.resolve(strict=True)
        root = allowed_root.resolve(strict=True)
        if not executable.is_file():
            raise ValueError("test executable must be a file")
        if cwd != root and root not in cwd.parents:
            raise PermissionError("test cwd escaped the allowed worktree")
        if not cwd.is_dir():
            raise ValueError("test cwd must be a directory")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("test timeout must be between 1 and 3600 seconds")
        if any("\x00" in argument for argument in self.arguments):
            raise ValueError("test arguments contain a null byte")


@dataclass(frozen=True)
class TestExecutionResult:
    run_id: str
    command: tuple[str, ...]
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    timed_out: bool = False


class TestExecutor:
    __test__ = False

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @staticmethod
    def safe_environment() -> dict[str, str]:
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

    async def execute(
        self,
        command: TestCommand,
        allowed_root: Path,
        on_output: OutputCallback | None = None,
    ) -> TestExecutionResult:
        command.validate(allowed_root)
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            str(command.executable.resolve()),
            *command.arguments,
            cwd=command.cwd.resolve(),
            env=self.safe_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._processes[run_id] = process
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        async def consume(stream: asyncio.StreamReader, channel: str, parts: list[str]) -> None:
            while chunk := await stream.readline():
                text = chunk.decode("utf-8", errors="replace")
                parts.append(text)
                if on_output:
                    await on_output(channel, text)

        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(consume(process.stdout, "stdout", stdout_parts))
        stderr_task = asyncio.create_task(consume(process.stderr, "stderr", stderr_parts))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=command.timeout_seconds)
        except TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        finally:
            await asyncio.gather(stdout_task, stderr_task)
            self._processes.pop(run_id, None)
        return TestExecutionResult(
            run_id=run_id,
            command=(str(command.executable.resolve()), *command.arguments),
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=process.returncode if process.returncode is not None else -1,
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
        )

    async def cancel(self, run_id: str) -> bool:
        process = self._processes.get(run_id)
        if not process or process.returncode is not None:
            return False
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()
        return True
