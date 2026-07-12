import asyncio
from pathlib import Path

from dualcode.git_service import GitService


async def main() -> None:
    root = Path(__file__).parents[1]
    service = GitService(root / ".integration-worktrees")
    target, branch = await service.create_worktree(
        root / "fixtures" / "dualcode-fixture",
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    )
    print(f"WORKTREE={target}")
    print(f"BRANCH={branch}")


if __name__ == "__main__":
    asyncio.run(main())
