from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dualcode.git_service import GitError
from dualcode.relay_service import create_shadow_snapshot


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
