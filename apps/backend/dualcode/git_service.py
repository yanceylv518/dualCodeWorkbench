import asyncio
import subprocess
import re
from dataclasses import dataclass
from pathlib import Path

from .security import validate_project_file


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitResult:
    stdout: str
    stderr: str
    returncode: int


class GitService:
    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.resolve()
        self.managed_root.mkdir(parents=True, exist_ok=True)

    async def run(self, repository: Path, *args: str, check: bool = True) -> GitResult:
        repository = repository.resolve(strict=True)
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout, stderr = await process.communicate()
        result = GitResult(
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            process.returncode or 0,
        )
        if check and result.returncode != 0:
            raise GitError(result.stderr.strip() or result.stdout.strip())
        return result

    async def ensure_repository(self, repository: Path) -> Path:
        result = await self.run(repository, "rev-parse", "--show-toplevel")
        root = Path(result.stdout.strip()).resolve(strict=True)
        if root != repository.resolve(strict=True):
            raise GitError("Workspace must be the Git repository root")
        return root

    async def status(self, repository: Path) -> list[str]:
        result = await self.run(repository, "status", "--porcelain=v1", "-z")
        return [entry for entry in result.stdout.split("\0") if entry]

    async def current_branch(self, repository: Path) -> str:
        result = await self.run(repository, "branch", "--show-current")
        return result.stdout.strip()

    def branch_name(self, thread_id: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", thread_id):
            raise ValueError("thread_id must be a UUID")
        return f"dualcode/{thread_id.lower()}"

    def worktree_path(self, workspace_id: str, thread_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", workspace_id):
            raise ValueError("workspace_id must be a UUID")
        branch = self.branch_name(thread_id)
        target = (self.managed_root / workspace_id / branch.removeprefix("dualcode/")).resolve()
        if self.managed_root not in target.parents:
            raise PermissionError("worktree path escaped the managed root")
        return target

    async def create_worktree(
        self, repository: Path, workspace_id: str, thread_id: str
    ) -> tuple[Path, str]:
        await self.ensure_repository(repository)
        target = self.worktree_path(workspace_id, thread_id)
        branch = self.branch_name(thread_id)
        if target.exists():
            raise GitError("Managed worktree already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.run(repository, "worktree", "add", "-b", branch, str(target), "HEAD")
        return target, branch

    async def diff(self, worktree: Path) -> str:
        head = await self.run(worktree, "rev-parse", "--verify", "HEAD", check=False)
        args = ["diff", "--no-ext-diff", "--no-color", "--src-prefix=a/", "--dst-prefix=b/"]
        if head.returncode == 0:
            args.append("HEAD")
        else:
            args.append("--cached")
        result = await self.run(
            worktree,
            *args,
        )
        sections = [result.stdout]
        for relative in await self._untracked_files(worktree):
            path = (worktree / relative).resolve()
            try:
                validate_project_file(path)
                if path.is_file() and path.stat().st_size <= 512_000:
                    content = path.read_text(encoding="utf-8")
                    lines = content.splitlines()
                    sections.append(
                        f"diff --git a/{relative} b/{relative}\n"
                        f"new file mode 100644\n--- /dev/null\n+++ b/{relative}\n"
                        f"@@ -0,0 +1,{len(lines)} @@\n"
                        + "\n".join(f"+{line}" for line in lines)
                        + "\n"
                    )
            except (OSError, UnicodeError, PermissionError):
                continue
        return "".join(sections)

    async def changed_files(self, worktree: Path) -> list[str]:
        entries = await self.status(worktree)
        paths: list[str] = []
        for entry in entries:
            path = entry[3:] if len(entry) > 3 else ""
            if path and path not in paths:
                paths.append(path)
        return paths

    async def apply_diff(self, repository: Path, patch: str, reverse: bool = False) -> None:
        repository = repository.resolve(strict=True)
        args = ["git", "-C", str(repository), "apply", "--whitespace=nowarn"]
        if reverse:
            args.append("--reverse")
        process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout, stderr = await process.communicate(patch.encode("utf-8"))
        if process.returncode != 0:
            raise GitError((stderr or stdout).decode("utf-8", errors="replace").strip() or "Unable to apply checkpoint")

    async def _untracked_files(self, worktree: Path) -> list[str]:
        return [entry[3:] for entry in await self.status(worktree) if entry.startswith("?? ")]

    async def repository_status(self, repository: Path) -> dict[str, object]:
        await self.ensure_repository(repository)
        branch = await self.current_branch(repository)
        head_result = await self.run(repository, "rev-parse", "--short=10", "HEAD", check=False)
        head = head_result.stdout.strip() if head_result.returncode == 0 else ""
        remote_result = await self.run(repository, "remote", "get-url", "origin", check=False)
        remote = remote_result.stdout.strip() if remote_result.returncode == 0 else ""
        upstream_result = await self.run(
            repository, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False
        )
        upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
        ahead = behind = 0
        if upstream:
            counts = await self.run(repository, "rev-list", "--left-right", "--count", f"{upstream}...HEAD", check=False)
            if counts.returncode == 0:
                parts = counts.stdout.strip().split()
                if len(parts) == 2:
                    behind, ahead = int(parts[0]), int(parts[1])
        log = await self.run(repository, "log", "-5", "--pretty=format:%h%x09%an%x09%s%x09%cI", check=False)
        commits = []
        for line in log.stdout.splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4:
                commits.append({"sha": parts[0], "author": parts[1], "subject": parts[2], "date": parts[3]})
        return {
            "branch": branch,
            "head": head,
            "remote": remote,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "changes": await self.status(repository),
            "commits": commits,
        }

    async def commit_all(self, repository: Path, message: str) -> str:
        if not message.strip():
            raise ValueError("Commit message is required")
        await self.ensure_repository(repository)
        await self.run(repository, "add", "--all")
        await self.run(repository, "commit", "-m", message.strip())
        return (await self.run(repository, "rev-parse", "--short=10", "HEAD")).stdout.strip()

    async def push(self, repository: Path) -> str:
        await self.ensure_repository(repository)
        upstream = await self.run(repository, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False)
        if upstream.returncode == 0:
            result = await self.run(repository, "push")
        else:
            branch = await self.current_branch(repository)
            if not branch:
                raise GitError("Cannot push a detached HEAD")
            result = await self.run(repository, "push", "--set-upstream", "origin", branch)
        return result.stdout.strip() or result.stderr.strip()

    async def pull_ff_only(self, repository: Path) -> str:
        await self.ensure_repository(repository)
        if await self.status(repository):
            raise GitError("Pull refused: local workspace has uncommitted changes")
        result = await self.run(repository, "pull", "--ff-only")
        return result.stdout.strip() or result.stderr.strip()
