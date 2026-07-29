from pathlib import Path, PurePosixPath

import pytest

from dualcode.adapters import AgentRequest
from dualcode.ssh_adapter import (
    ClaudeSshAdapter,
    ClaudeSshConfig,
    RemoteRepositoryUnavailable,
)


@pytest.mark.asyncio
async def test_claude_ssh_stream_events_filter_protocol_envelopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(ClaudeSshAdapter)

    async def fake_stream(_request):
        yield '{"type":"system","subtype":"init","session_id":"session-1"}'
        yield (
            '{"type":"assistant","session_id":"session-1",'
            '"message":{"content":[{"type":"thinking","thinking":"inspect"},'
            '{"type":"text","text":"answer"}]}}'
        )
        yield '{"type":"result","session_id":"session-1","result":"answer"}'

    monkeypatch.setattr(adapter, "stream", fake_stream)
    events = [event async for event in adapter.stream_events(object())]

    assert [event.type.value for event in events] == ["tool_event", "delta", "final"]
    assert events[0].item["type"] == "reasoning"
    assert events[0].item["text"] == "inspect"
    assert events[1].text == "answer"


def config(tmp_path: Path, **changes):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest\n")
    values = {
        "host": "claude.example",
        "username": "dualcode",
        "known_hosts": known_hosts,
    }
    values.update(changes)
    return ClaudeSshConfig(**values)


def test_requires_known_hosts(tmp_path: Path):
    with pytest.raises(ValueError, match="known_hosts"):
        ClaudeSshAdapter(config(tmp_path, known_hosts=tmp_path / "missing"))


def test_rejects_relative_remote_root(tmp_path: Path):
    with pytest.raises(ValueError, match="remote_root"):
        ClaudeSshAdapter(config(tmp_path, remote_root=PurePosixPath("relative")))


def test_remote_thread_directory_uses_validated_uuid(tmp_path: Path):
    adapter = ClaudeSshAdapter(config(tmp_path))
    with pytest.raises(ValueError, match="UUID"):
        adapter._remote_dir(AgentRequest("../escape", "prompt", {}), "run")


@pytest.mark.asyncio
async def test_review_worktree_uses_detached_snapshot_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    commands: list[str] = []

    class Result:
        exit_status = 0
        stderr = ""

    class Connection:
        async def run(self, command, **kwargs):
            commands.append(command)
            return Result()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    adapter = ClaudeSshAdapter(
        config(
            tmp_path,
            remote_root=PurePosixPath("/home/dualcode/runtime"),
        )
    )

    async def connect():
        return Connection()

    monkeypatch.setattr(adapter, "_connect", connect)
    thread_id = "11111111-1111-4111-8111-111111111111"
    run_id = "22222222-2222-4222-8222-222222222222"
    snapshot_sha = "a" * 40
    worktree = await adapter.create_review_worktree(
        PurePosixPath("/home/dualcode/work/project"),
        thread_id=thread_id,
        run_id=run_id,
        snapshot_sha=snapshot_sha,
    )
    warnings = await adapter.remove_review_worktree(worktree)

    assert warnings == []
    assert commands == [
        "mkdir -p -- /home/dualcode/runtime/review-worktrees/"
        "11111111-1111-4111-8111-111111111111",
        "git -C /home/dualcode/work/project worktree add --detach "
        "/home/dualcode/runtime/review-worktrees/"
        "11111111-1111-4111-8111-111111111111/"
        "22222222-2222-4222-8222-222222222222 "
        + snapshot_sha,
        "git -C /home/dualcode/work/project worktree remove --force "
        "/home/dualcode/runtime/review-worktrees/"
        "11111111-1111-4111-8111-111111111111/"
        "22222222-2222-4222-8222-222222222222",
        "git -C /home/dualcode/work/project worktree prune",
    ]


@pytest.mark.asyncio
async def test_review_worktree_add_failure_prunes_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    commands: list[str] = []

    class Result:
        exit_status = 0
        stderr = ""

    class Connection:
        async def run(self, command, **kwargs):
            commands.append(command)
            if "worktree add" in command:
                raise RuntimeError("add failed")
            return Result()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    adapter = ClaudeSshAdapter(config(tmp_path))

    async def connect():
        return Connection()

    monkeypatch.setattr(adapter, "_connect", connect)
    with pytest.raises(RuntimeError, match="add failed"):
        await adapter.create_review_worktree(
            PurePosixPath("/home/dualcode/work/project"),
            thread_id="11111111-1111-4111-8111-111111111111",
            run_id="22222222-2222-4222-8222-222222222222",
            snapshot_sha="b" * 40,
        )

    assert any("worktree remove --force" in command for command in commands)
    assert commands[-1].endswith("worktree prune")


@pytest.mark.asyncio
async def test_repository_status_distinguishes_not_cloned_from_command_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Result:
        stdout = "__DUALCODE_REPOSITORY_NOT_READY__"

    class Connection:
        async def run(self, command, **kwargs):
            assert "rev-parse --is-inside-work-tree" in command
            return Result()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    adapter = ClaudeSshAdapter(config(tmp_path))

    async def connect():
        return Connection()

    monkeypatch.setattr(adapter, "_connect", connect)
    with pytest.raises(RemoteRepositoryUnavailable, match="not been cloned"):
        await adapter.repository_status(PurePosixPath("/home/dualcode/work/product"))


@pytest.mark.asyncio
async def test_repository_status_accepts_cloned_empty_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Result:
        stdout = "true\nmain\n\ngit@github.com:owner/product.git\n"

    class Connection:
        async def run(self, command, **kwargs):
            assert "rev-parse --verify HEAD" in command
            return Result()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    adapter = ClaudeSshAdapter(config(tmp_path))

    async def connect():
        return Connection()

    monkeypatch.setattr(adapter, "_connect", connect)
    status = await adapter.repository_status(PurePosixPath("/home/dualcode/work/product"))

    assert status == {
        "branch": "main",
        "head": "",
        "remote": "git@github.com:owner/product.git",
    }


@pytest.mark.asyncio
async def test_repository_discovery_only_lists_immediate_git_children(
    monkeypatch: pytest.MonkeyPatch,
):
    class Result:
        def __init__(self, stdout: str):
            self.stdout = stdout

    class Connection:
        async def run(self, command, **kwargs):
            if "-mindepth 1 -maxdepth 1" in command:
                return Result(
                    "/home/yancey/work/DualCodeWorkBench\0"
                    "/home/yancey/work/Orbit\0"
                    "/home/yancey/work/not-a-repository\0"
                )
            if "DualCodeWorkBench" in command:
                return Result("git@github.com:yanceylv518/DualCodeWorkBench.git\n")
            if "Orbit" in command:
                return Result("https://github.com/acme/Orbit.git\n")
            return Result("")

        def close(self):
            return None

        async def wait_closed(self):
            return None

    adapter = object.__new__(ClaudeSshAdapter)

    async def connect():
        return Connection()

    monkeypatch.setattr(adapter, "_connect", connect)

    assert await adapter.discover_repositories(PurePosixPath("/home/yancey/work")) == [
        {
            "path": "/home/yancey/work/DualCodeWorkBench",
            "remote": "git@github.com:yanceylv518/DualCodeWorkBench.git",
        },
        {
            "path": "/home/yancey/work/Orbit",
            "remote": "https://github.com/acme/Orbit.git",
        },
    ]


@pytest.mark.asyncio
async def test_repair_provision_refuses_valid_repo_then_replaces_only_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    commands: list[str] = []

    class Result:
        stdout = "cloned"
        stderr = ""

    class Connection:
        async def run(self, command, **kwargs):
            commands.append(command)
            return Result()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    adapter = ClaudeSshAdapter(config(tmp_path))

    async def connect():
        return Connection()

    monkeypatch.setattr(adapter, "_connect", connect)
    output = await adapter.repository_update(
        PurePosixPath("/home/dualcode/work/product"),
        "repair_provision",
        "git@github.com:owner/product.git",
    )

    assert output == "cloned"
    assert len(commands) == 1
    command = commands[0]
    assert "Refusing to replace an existing valid Git repository" in command
    assert "rm -rf -- /home/dualcode/work/product" in command
    assert "git clone -- git@github.com:owner/product.git /home/dualcode/work/product" in command
