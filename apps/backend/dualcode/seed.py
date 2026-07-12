from sqlalchemy import func, select

from .database import SessionLocal
from .models import Message, RunState, Thread, Workspace


async def seed_demo() -> None:
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
        if await db.scalar(select(func.count()).select_from(Workspace)):
            return
        workspace = Workspace(name="DualCode Workbench 示例", path="D:/Projects/dualcode")
        thread = Thread(title="实现协作执行状态机", state=RunState.COMPLETED)
        thread.messages = [
            Message(role="user", content="请实现协作执行状态机，并补充测试。"),
            Message(role="claude", content="计划分为状态定义、合法迁移校验和事件审计。"),
            Message(role="codex", content="已实现状态机与 WebSocket 事件。"),
        ]
        workspace.threads = [thread, Thread(title="附件隔离与校验", state=RunState.CREATED)]
        db.add(workspace)
        await db.commit()
