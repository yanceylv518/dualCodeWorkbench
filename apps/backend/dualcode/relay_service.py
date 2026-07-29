"""Local shadow snapshots for isolated cross-agent review."""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from .collaboration_protocol import StrictModel
from .git_service import GitError, GitService
from .models import AuditLog
from .security import validate_project_file


class ShadowSnapshot(StrictModel):
    base_sha: str
    snapshot_sha: str
    excluded_paths: list[str]


class RelayRemoteSpec(StrictModel):
    host: str = ""
    username: str = ""
    port: int = 22
    repository_path: str = ""
    known_hosts: str = ""
    client_key: str = ""
    local_remote: str = ""

    def remote_url(self) -> str:
        if self.local_remote:
            path = Path(self.local_remote).resolve(strict=True)
            return str(path)
        if (
            not self.host
            or any(character.isspace() for character in self.host)
            or not self.username
            or any(character.isspace() for character in self.username)
        ):
            raise ValueError("影子同步 SSH 主机或用户名无效")
        if not 1 <= self.port <= 65535:
            raise ValueError("影子同步 SSH 端口无效")
        repository = PurePosixPath(self.repository_path)
        if not self.repository_path.startswith("/") or ".." in repository.parts:
            raise ValueError("影子同步 VPS 仓库路径无效")
        return (
            f"ssh://{quote(self.username, safe='')}@{self.host}:{self.port}"
            f"{quote(self.repository_path, safe='/')}"
        )

    def git_environment(self) -> dict[str, str]:
        if self.local_remote:
            return {}
        known_hosts = Path(self.known_hosts).resolve(strict=True)
        if not known_hosts.is_file():
            raise ValueError("影子同步要求有效的 known_hosts 文件")
        command = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-p",
            str(self.port),
        ]
        if self.client_key:
            client_key = Path(self.client_key).resolve(strict=True)
            if not client_key.is_file():
                raise ValueError("影子同步 SSH 私钥路径无效")
            command.extend(["-i", str(client_key)])
        return {"GIT_SSH_COMMAND": shlex.join(command)}


def shadow_ref(workspace_id: str, thread_id: str) -> str:
    for value in (workspace_id, thread_id):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError("影子 ref 标识只允许字母、数字、点、下划线和连字符")
    return f"refs/dualcode/relay/{workspace_id}/{thread_id}"


def build_relay_sync_audit(
    *,
    workspace_id: str,
    thread_id: str,
    snapshot: ShadowSnapshot,
    succeeded: bool,
    error: str = "",
) -> AuditLog:
    outcome = "succeeded" if succeeded else "failed"
    detail = (
        f"base={snapshot.base_sha};snapshot={snapshot.snapshot_sha};"
        f"excluded={len(snapshot.excluded_paths)}"
    )
    if error:
        detail += f";error={error.replace(chr(10), ' ')[:200]}"
    return AuditLog(
        workspace_id=workspace_id,
        thread_id=thread_id,
        event=f"relay.shadow_sync.{outcome}",
        detail=detail,
    )


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


async def push_shadow_ref(
    repository: Path,
    snapshot_sha: str,
    *,
    workspace_id: str,
    thread_id: str,
    remote_spec: RelayRemoteSpec,
) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", snapshot_sha):
        raise ValueError("影子快照 SHA 无效")
    ref = shadow_ref(workspace_id, thread_id)
    git = GitService(repository.parent)
    try:
        await git.run(
            repository,
            "push",
            "--force",
            remote_spec.remote_url(),
            f"{snapshot_sha}:{ref}",
            env=remote_spec.git_environment(),
        )
    except (GitError, OSError, ValueError) as exc:
        raise GitError(f"影子快照推送失败：{str(exc)[:300]}") from exc


async def cleanup_shadow_ref(
    repository: Path,
    *,
    workspace_id: str,
    thread_id: str,
    remote_spec: RelayRemoteSpec,
) -> list[str]:
    ref = shadow_ref(workspace_id, thread_id)
    git = GitService(repository.parent)
    warnings: list[str] = []
    try:
        await git.run(repository, "update-ref", "-d", ref)
    except (GitError, OSError) as exc:
        warnings.append(f"本地影子 ref 清理失败：{str(exc)[:200]}")
    try:
        await git.run(
            repository,
            "push",
            remote_spec.remote_url(),
            f":{ref}",
            env=remote_spec.git_environment(),
        )
    except (GitError, OSError, ValueError) as exc:
        warnings.append(f"远端影子 ref 清理失败：{str(exc)[:200]}")
    return warnings
