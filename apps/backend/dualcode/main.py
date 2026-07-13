from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.middleware.cors import CORSMiddleware
from .api import router
from .database import SessionLocal, engine, get_session
from .diagnostics import build_diagnostic_snapshot
from .execution_jobs import recover_execution_jobs
from .models import AgentRun, RunState, Thread
from .database_migrations import upgrade_database
from .seed import repair_legacy_labels
from .auth import SidecarTokenMiddleware
from .config import sidecar_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    upgrade_database(str(engine.url))
    async with SessionLocal() as session:
        app.state.recovered_jobs = await recover_execution_jobs(session)
        active_states = [RunState.PLANNING, RunState.WAITING_APPROVAL, RunState.IMPLEMENTING, RunState.TESTING, RunState.REVIEWING, RunState.FALLBACK_TO_CODEX]
        await session.execute(update(Thread).where(Thread.state.in_(active_states)).values(state=RunState.CREATED))
        await session.execute(update(AgentRun).where(AgentRun.state.in_(active_states)).values(
            state=RunState.CANCELLED, output="本地后台服务已重启，本轮 Agent 任务未完成"
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
    allow_headers=["Content-Type", "X-DualCode-Token"],
)
app.add_middleware(SidecarTokenMiddleware, token=sidecar_token)
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
