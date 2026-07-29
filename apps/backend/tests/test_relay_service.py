from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dualcode.git_service import GitError
from dualcode.relay_service import (
    RelayRemoteSpec,
    ShadowSnapshot,
    build_relay_sync_audit,
    cleanup_shadow_ref,
    create_shadow_snapshot,
    push_shadow_ref,
    shadow_ref,
)


def _git(repository: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _repository(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-b", "main")
    (path / "staged.txt").write_text("base staged\n", encoding="utf-8")
    (path / "unstaged.txt").write_text("base unstaged\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(
        path,
        "-c",
        "user.name=DualCode Test",
        "-c",
        "user.email=dualcode@example.invalid",
        "commit",
        "-m",
        "initial",
    )


@pytest.mark.asyncio
async def test_shadow_snapshot_captures_dirty_tree_without_mutating_user_state(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    (repository / "staged.txt").write_text("staged change\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "unstaged.txt").write_text("unstaged change\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (repository / ".env.local").write_text("TOKEN=secret\n", encoding="utf-8")

    status_before = _git(repository, "status", "--porcelain=v1", "-z")
    head_before = _git(repository, "rev-parse", "HEAD").strip()
    index_before = _git(repository, "write-tree").strip()

    snapshot = await create_shadow_snapshot(repository)

    assert _git(repository, "status", "--porcelain=v1", "-z") == status_before
    assert _git(repository, "rev-parse", "HEAD").strip() == head_before
    assert _git(repository, "write-tree").strip() == index_before
    assert snapshot.base_sha == head_before
    assert len(snapshot.base_sha) == 40
    assert len(snapshot.snapshot_sha) == 40
    assert snapshot.excluded_paths == [".env.local"]
    tree_paths = _git(
        repository, "ls-tree", "-r", "--name-only", snapshot.snapshot_sha
    ).splitlines()
    assert tree_paths == ["staged.txt", "unstaged.txt", "untracked.txt"]
    assert (
        _git(repository, "show", f"{snapshot.snapshot_sha}:staged.txt")
        == "staged change\n"
    )
    assert (
        _git(repository, "show", f"{snapshot.snapshot_sha}:unstaged.txt")
        == "unstaged change\n"
    )
    assert (
        _git(repository, "show", f"{snapshot.snapshot_sha}:untracked.txt")
        == "untracked\n"
    )


@pytest.mark.asyncio
async def test_shadow_snapshot_rejects_repository_without_head(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "empty"
    repository.mkdir()
    _git(repository, "init", "-b", "main")

    with pytest.raises(GitError, match="还没有 HEAD 提交"):
        await create_shadow_snapshot(repository)


@pytest.mark.asyncio
async def test_shadow_ref_push_overwrites_only_fixed_ref_and_cleans_up(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    remote_spec = RelayRemoteSpec(local_remote=str(bare))
    ref = shadow_ref("workspace-1", "thread-1")

    (repository / "first.txt").write_text("first\n", encoding="utf-8")
    first = await create_shadow_snapshot(repository)
    await push_shadow_ref(
        repository,
        first.snapshot_sha,
        workspace_id="workspace-1",
        thread_id="thread-1",
        remote_spec=remote_spec,
    )
    assert _git(bare, "rev-parse", ref).strip() == first.snapshot_sha

    (repository / "second.txt").write_text("second\n", encoding="utf-8")
    second = await create_shadow_snapshot(repository)
    await push_shadow_ref(
        repository,
        second.snapshot_sha,
        workspace_id="workspace-1",
        thread_id="thread-1",
        remote_spec=remote_spec,
    )
    assert _git(bare, "rev-parse", ref).strip() == second.snapshot_sha
    assert _git(bare, "show-ref").split()[1:] == [ref]

    assert (
        await cleanup_shadow_ref(
            repository,
            workspace_id="workspace-1",
            thread_id="thread-1",
            remote_spec=remote_spec,
        )
        == []
    )
    assert _git(bare, "show-ref", check=False) == ""


@pytest.mark.asyncio
async def test_shadow_push_failure_is_chinese_and_never_falls_back(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    snapshot = await create_shadow_snapshot(repository)
    missing_remote = tmp_path / "missing.git"
    remote_spec = RelayRemoteSpec(local_remote=str(missing_remote))

    with pytest.raises(GitError, match="影子快照推送失败"):
        await push_shadow_ref(
            repository,
            snapshot.snapshot_sha,
            workspace_id="workspace-1",
            thread_id="thread-1",
            remote_spec=remote_spec,
        )
    assert _git(repository, "remote") == ""


def test_shadow_ref_rejects_untrusted_components() -> None:
    with pytest.raises(ValueError, match="影子 ref 标识"):
        shadow_ref("../workspace", "thread")


def test_ssh_remote_requires_known_hosts_and_builds_strict_transport(
    tmp_path: Path,
) -> None:
    known_hosts = tmp_path / "known hosts"
    known_hosts.write_text("example.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    client_key = tmp_path / "client key"
    client_key.write_text("test-only", encoding="utf-8")
    spec = RelayRemoteSpec(
        host="example.invalid",
        username="reviewer",
        port=2222,
        repository_path="/srv/git/project.git",
        known_hosts=str(known_hosts),
        client_key=str(client_key),
    )

    assert spec.remote_url() == (
        "ssh://reviewer@example.invalid:2222/srv/git/project.git"
    )
    command = spec.git_environment()["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=yes" in command
    assert "UserKnownHostsFile=" in command
    assert "-p 2222" in command
    assert "-i " in command


@pytest.mark.asyncio
async def test_cleanup_failure_returns_warning_instead_of_raising(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    warnings = await cleanup_shadow_ref(
        repository,
        workspace_id="workspace-1",
        thread_id="thread-1",
        remote_spec=RelayRemoteSpec(local_remote=str(tmp_path / "missing.git")),
    )

    assert len(warnings) == 1
    assert warnings[0].startswith("远端影子 ref 清理失败")


def test_relay_sync_audit_contains_only_snapshot_metadata() -> None:
    snapshot = ShadowSnapshot(
        base_sha="a" * 40,
        snapshot_sha="b" * 40,
        excluded_paths=[".env", "secret.key"],
    )

    row = build_relay_sync_audit(
        workspace_id="workspace-1",
        thread_id="thread-1",
        snapshot=snapshot,
        succeeded=True,
    )

    assert row.event == "relay.shadow_sync.succeeded"
    assert row.detail == (
        f"base={'a' * 40};snapshot={'b' * 40};excluded=2"
    )
    assert ".env" not in row.detail
    assert "secret.key" not in row.detail
