from sqlalchemy import select

from .database import SessionLocal
from .models import Thread, Workspace


async def repair_legacy_labels() -> None:
    """Repair labels from early builds without creating synthetic user data."""
    async with SessionLocal() as db:
        # Repair labels written by early Windows builds which passed Chinese text
        # through a non-UTF-8 PowerShell code page. User-authored messages are
        # deliberately left untouched.
        workspaces = list((await db.scalars(select(Workspace))).all())
        for item in workspaces:
            if item.name in {"?? Agent ??", "???? Agent ????"} or (
                "?" in item.name and item.path.endswith("dualcode-fixture")
            ):
                item.name = "Agent 联调夹具"
        threads = list((await db.scalars(select(Thread))).all())
        for item in threads:
            if item.title.startswith("æ°å¼å") or item.title == "??????":
                item.title = "新开发任务"
        if workspaces or threads:
            await db.commit()
