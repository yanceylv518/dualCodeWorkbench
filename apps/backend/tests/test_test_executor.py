import sys
from pathlib import Path

import pytest

from dualcode.test_executor import TestCommand, TestExecutor


@pytest.mark.asyncio
async def test_executes_parameterized_command_and_streams_output(tmp_path: Path):
    chunks: list[tuple[str, str]] = []
    command = TestCommand(
        executable=Path(sys.executable),
        arguments=("-c", "print('passed')"),
        cwd=tmp_path,
        timeout_seconds=10,
    )
    result = await TestExecutor().execute(
        command, tmp_path, lambda channel, text: _capture(chunks, channel, text)
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "passed"
    assert chunks == [("stdout", "passed\r\n" if sys.platform == "win32" else "passed\n")]


async def _capture(chunks: list[tuple[str, str]], channel: str, text: str) -> None:
    chunks.append((channel, text))


def test_rejects_cwd_outside_worktree(tmp_path: Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    with pytest.raises(PermissionError):
        TestCommand(Path(sys.executable), (), outside).validate(root)
