import json
import os
import tempfile

# Isolate module-level settings before importing backend modules.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="dualcode-diagnostics-"))

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode import diagnostics
from dualcode.database import get_session
from dualcode.main import app
from dualcode.models import AuditLog, Base, ExecutionJob
from dualcode.runtime_settings import AgentSettings


@pytest.fixture
async def api_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def isolated_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = isolated_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_endpoint_is_safe_and_reports_runtime(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    secret = "do-not-return-this-value"
    runtime = AgentSettings(
        codex_executable=f"C:/private/{secret}/codex.exe",
        claude_executable=f"C:/private/{secret}/claude.exe",
        claude_ssh_host=f"{secret}.example",
        claude_ssh_username=secret,
        claude_ssh_client_key=f"C:/private/{secret}.key",
        test_executable=f"C:/private/{secret}/python.exe",
    )
    monkeypatch.setattr(diagnostics.agent_settings_store, "load", lambda: runtime)

    async def healthy(_self):
        return True

    monkeypatch.setattr(diagnostics.CodexCliAdapter, "health_check", healthy)
    monkeypatch.setattr(diagnostics.ClaudeCliAdapter, "health_check", healthy)

    response = await api_client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "0.1.5"
    assert payload["storage"]["database_reachable"] is True
    assert payload["storage"]["data_directory_writable"] is True
    assert payload["configuration"] == {
        "loaded": True,
        "real_agents_enabled": True,
        "ssh_enabled": False,
        "test_command_configured": True,
    }
    assert payload["agents"]["codex"] == {"configured": True, "healthy": True}
    assert payload["process"]["pid"] > 0
    assert payload["process"]["uptime_seconds"] >= 0
    assert secret not in json.dumps(payload)
    assert str(diagnostics.settings.data_dir) not in json.dumps(payload)


@pytest.mark.asyncio
async def test_snapshot_counts_jobs_and_audit_without_returning_records(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'diagnostics.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(diagnostics.agent_settings_store, "load", lambda: AgentSettings())

    async def unavailable(_self):
        return False

    monkeypatch.setattr(diagnostics.CodexCliAdapter, "health_check", unavailable)
    monkeypatch.setattr(diagnostics.ClaudeCliAdapter, "health_check", unavailable)
    async with sessions() as session:
        session.add_all(
            [
                ExecutionJob(
                    approval_id="approval-1",
                    workspace_id="workspace",
                    thread_id="thread",
                    kind="test_run",
                    idempotency_key="key-1",
                    status="FAILED",
                ),
                ExecutionJob(
                    approval_id="approval-2",
                    workspace_id="workspace",
                    thread_id="thread",
                    kind="test_run",
                    idempotency_key="key-2",
                    status="FAILED",
                ),
                AuditLog(
                    workspace_id="workspace",
                    thread_id="thread",
                    event="private.event",
                    detail="sensitive audit detail",
                ),
            ]
        )
        await session.commit()
        snapshot = await diagnostics.build_diagnostic_snapshot(
            session, version="test", recovered_jobs=1
        )

    assert snapshot["jobs"] == {"counts": {"FAILED": 2}, "recovered_on_startup": 1}
    assert snapshot["audit"] == {"record_count": 1}
    assert "sensitive audit detail" not in json.dumps(snapshot)
    await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_settings_degrade_safely(api_client: httpx.AsyncClient, monkeypatch):
    monkeypatch.setattr(
        diagnostics.agent_settings_store,
        "load",
        lambda: (_ for _ in ()).throw(ValueError("secret settings parse failure")),
    )

    response = await api_client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configuration"]["loaded"] is False
    assert payload["agents"]["codex"]["healthy"] is False
    assert "secret settings parse failure" not in json.dumps(payload)
