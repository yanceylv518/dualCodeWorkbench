import uuid
from pathlib import Path

import pytest

from dualcode.git_service import GitService


def test_managed_worktree_path_cannot_escape(tmp_path: Path):
    service = GitService(tmp_path / "managed")
    workspace_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    target = service.worktree_path(workspace_id, thread_id)
    assert service.managed_root in target.parents


def test_branch_name_rejects_untrusted_input(tmp_path: Path):
    service = GitService(tmp_path / "managed")
    with pytest.raises(ValueError):
        service.branch_name("../../main")


@pytest.mark.asyncio
async def test_diff_and_changed_files_include_untracked_and_staged_files(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    service = GitService(tmp_path / "managed")
    await service.run(repository, "init", "-b", "main")
    (repository / "tracked.txt").write_text("before\n", encoding="utf-8")
    await service.run(repository, "add", "tracked.txt")
    await service.run(
        repository, "-c", "user.name=DualCode", "-c", "user.email=dualcode@example.invalid",
        "commit", "-m", "initial",
    )
    (repository / "tracked.txt").write_text("after\n", encoding="utf-8")
    await service.run(repository, "add", "tracked.txt")
    (repository / "new.txt").write_text("new content\n", encoding="utf-8")

    changed = await service.changed_files(repository)
    diff = await service.diff(repository)

    assert changed == ["new.txt", "tracked.txt"] or changed == ["tracked.txt", "new.txt"]
    assert "+after" in diff
    assert "diff --git a/new.txt b/new.txt" in diff
    assert "+new content" in diff
