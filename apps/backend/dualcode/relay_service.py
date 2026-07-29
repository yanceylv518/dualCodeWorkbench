"""Local shadow snapshots for isolated cross-agent review."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .collaboration_protocol import StrictModel
from .git_service import GitError, GitService
from .security import validate_project_file


class ShadowSnapshot(StrictModel):
    base_sha: str
    snapshot_sha: str
    excluded_paths: list[str]


async def create_shadow_snapshot(repository: Path) -> ShadowSnapshot:
    """Capture the current workspace in an unreachable commit without moving refs."""

    repository = repository.resolve(strict=True)
    git = GitService(repository.parent)
    await git.ensure_repository(repository)
    head = await git.run(repository, "rev-parse", "--verify", "HEAD", check=False)
    base_sha = head.stdout.strip()
    if head.returncode != 0 or len(base_sha) != 40:
        raise GitError("无法创建影子快照：当前仓库还没有 HEAD 提交")

    descriptor, index_name = tempfile.mkstemp(prefix="dualcode-shadow-", suffix=".index")
    os.close(descriptor)
    index_path = Path(index_name)
    index_path.unlink()
    shadow_env = {"GIT_INDEX_FILE": str(index_path)}
    excluded_paths: list[str] = []
    try:
        await git.run(repository, "read-tree", base_sha, env=shadow_env)
        await git.run(repository, "add", "-A", env=shadow_env)
        indexed = await git.run(
            repository, "ls-files", "-z", "--cached", env=shadow_env
        )
        for relative_path in filter(None, indexed.stdout.split("\0")):
            try:
                validate_project_file(Path(relative_path))
            except PermissionError:
                excluded_paths.append(relative_path)
                await git.run(
                    repository,
                    "rm",
                    "--cached",
                    "--ignore-unmatch",
                    "--",
                    relative_path,
                    env=shadow_env,
                )
        tree_sha = (
            await git.run(repository, "write-tree", env=shadow_env)
        ).stdout.strip()
        identity_env = {
            **shadow_env,
            "GIT_AUTHOR_NAME": "DualCode Relay",
            "GIT_AUTHOR_EMAIL": "relay@dualcode.invalid",
            "GIT_COMMITTER_NAME": "DualCode Relay",
            "GIT_COMMITTER_EMAIL": "relay@dualcode.invalid",
        }
        snapshot_sha = (
            await git.run(
                repository,
                "commit-tree",
                tree_sha,
                "-p",
                base_sha,
                "-m",
                "DualCode shadow snapshot",
                env=identity_env,
            )
        ).stdout.strip()
    finally:
        index_path.unlink(missing_ok=True)
        index_path.with_name(f"{index_path.name}.lock").unlink(missing_ok=True)

    if len(snapshot_sha) != 40:
        raise GitError("无法创建影子快照：Git 未返回完整快照 SHA")
    return ShadowSnapshot(
        base_sha=base_sha,
        snapshot_sha=snapshot_sha,
        excluded_paths=sorted(excluded_paths),
    )
