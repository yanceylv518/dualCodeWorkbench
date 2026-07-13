from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.middleware.cors import CORSMiddleware
from .api import router
from .database import SessionLocal, engine, get_session
from .diagnostics import build_diagnostic_snapshot
from .execution_jobs import migrate_execution_jobs, recover_execution_jobs
from .models import AgentRun, Base, RunState, Thread
from .seed import repair_legacy_labels


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        columns = (await conn.execute(text("PRAGMA table_info(attachments)"))).mappings().all()
        if "message_id" not in {str(column["name"]) for column in columns}:
            await conn.execute(text("ALTER TABLE attachments ADD COLUMN message_id VARCHAR REFERENCES messages(id) ON DELETE SET NULL"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_attachments_message_id ON attachments (message_id)"))
        run_columns = (await conn.execute(text("PRAGMA table_info(agent_runs)"))).mappings().all()
        run_column_names = {str(column["name"]) for column in run_columns}
        if "before_diff" not in run_column_names:
            await conn.execute(text("ALTER TABLE agent_runs ADD COLUMN before_diff TEXT NOT NULL DEFAULT ''"))
        if "after_diff" not in run_column_names:
            await conn.execute(text("ALTER TABLE agent_runs ADD COLUMN after_diff TEXT NOT NULL DEFAULT ''"))
    await migrate_execution_jobs(engine)
    async with SessionLocal() as session:
        app.state.recovered_jobs = await recover_execution_jobs(session)
        active_states = [RunState.PLANNING, RunState.WAITING_APPROVAL, RunState.IMPLEMENTING, RunState.TESTING, RunState.REVIEWING, RunState.FALLBACK_TO_CODEX]
        await session.execute(update(Thread).where(Thread.state.in_(active_states)).values(state=RunState.CREATED))
        await session.execute(update(AgentRun).where(AgentRun.state.in_(active_states)).values(
            state=RunState.CANCELLED, output="Sidecar restarted before this Agent turn completed"
        ))
        await session.commit()
    await repair_legacy_labels()
    yield


app = FastAPI(title="DualCode Workbench API", version="0.1.5", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/diagnostics")
async def diagnostics(
    request: Request, db: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    """Return a support snapshot containing no configured values or file contents."""
    recovered_jobs = getattr(request.app.state, "recovered_jobs", [])
    recovered_count = len(recovered_jobs) if isinstance(recovered_jobs, (list, tuple, set)) else 0
    return await build_diagnostic_snapshot(
        db, version=request.app.version, recovered_jobs=recovered_count
    )
