import os
import subprocess
import tempfile
import base64
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Importing the application constructs its scheduler. Isolate that construction from any real
# per-user agent configuration (especially SSH paths) before importing application modules.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dualcode-api-integration-")

from dualcode.database import get_session
from dualcode.config import sidecar_token
from dualcode.main import app
from dualcode.models import AuditLog, Base, ExecutionJob, FileChange, TestRun as PersistedTestRun
from sqlalchemy import select


@pytest.fixture
async def api_client(tmp_path: Path):
    """Run the real FastAPI routes against an isolated SQLite database."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'integration.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def isolated_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = isolated_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-DualCode-Token": sidecar_token},
    ) as client:
        client._dualcode_test_sessions = sessions  # type: ignore[attr-defined]
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_health_and_empty_install_are_ready(api_client: httpx.AsyncClient):
    health = await api_client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    workspaces = await api_client.get("/api/workspaces")
    assert workspaces.status_code == 200
    assert workspaces.json() == []

    preflight = await api_client.options("/api/workspaces/example", headers={
        "Origin": "tauri://localhost",
        "Access-Control-Request-Method": "DELETE",
    })
    assert preflight.status_code == 200
    assert "DELETE" in preflight.headers["access-control-allow-methods"]


@pytest.mark.asyncio
async def test_workspace_and_thread_lifecycle_persists(api_client: httpx.AsyncClient, tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()

    created = await api_client.post(
        "/api/workspaces", json={"path": str(repository), "name": "Integration repository"}
    )
    assert created.status_code == 201
    workspace = created.json()
    assert workspace["name"] == "Integration repository"
    assert Path(workspace["path"]) == repository.resolve()
    assert len(workspace["threads"]) == 1

    # Creating the same workspace is idempotent and must not duplicate persisted state.
    repeated = await api_client.post(
        "/api/workspaces", json={"path": str(repository), "name": "Ignored rename"}
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == workspace["id"]

    # An unused initial thread is intentionally reused instead of creating empty duplicates.
    thread = await api_client.post(
        f"/api/workspaces/{workspace['id']}/threads", json={"title": "Delivery verification"}
    )
    assert thread.status_code == 201
    assert thread.json()["id"] == workspace["threads"][0]["id"]

    listed = (await api_client.get("/api/workspaces")).json()
    assert len(listed) == 1
    assert len(listed[0]["threads"]) == 1


@pytest.mark.asyncio
async def test_workspace_creation_rejects_non_repository(api_client: httpx.AsyncClient, tmp_path: Path):
    directory = tmp_path / "not-a-repository"
    directory.mkdir()

    response = await api_client.post("/api/workspaces", json={"path": str(directory)})

    assert response.status_code == 400
    assert response.json()["detail"] == "Workspace must be a Git repository"


@pytest.mark.asyncio
async def test_workspace_can_be_initialized_linked_and_removed_without_deleting_files(
    api_client: httpx.AsyncClient, tmp_path: Path
):
    repository = tmp_path / "new-product"
    created = await api_client.post("/api/workspaces/provision", json={
        "path": str(repository), "mode": "init", "remote_url": "https://example.invalid/team/product.git"
    })
    assert created.status_code == 201, created.text
    workspace = created.json()
    assert (repository / ".git").is_dir()
    assert (repository / "README.md").is_file()

    remote = await api_client.get(f"/api/workspaces/{workspace['id']}/remote")
    assert remote.status_code == 200
    assert remote.json()["local"]["remote"] == "https://example.invalid/team/product.git"
    assert remote.json()["local"]["head"]
    assert remote.json()["local"]["commits"][0]["subject"] == "chore: initialize project"

    repeated = await api_client.post("/api/workspaces/provision", json={
        "path": str(repository), "mode": "init", "remote_url": "https://example.invalid/team/product.git"
    })
    assert repeated.status_code == 201
    assert repeated.json()["id"] == workspace["id"]

    removed = await api_client.delete(f"/api/workspaces/{workspace['id']}")
    assert removed.status_code == 204, removed.text
    assert repository.is_dir()
    assert (repository / ".git").is_dir()
    assert (await api_client.get("/api/workspaces")).json() == []


async def _workspace(api_client: httpx.AsyncClient, tmp_path: Path) -> tuple[dict, dict]:
    repository = tmp_path / "acceptance-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, capture_output=True)
    workspace = (await api_client.post("/api/workspaces", json={"path": str(repository)})).json()
    return workspace, workspace["threads"][0]


@pytest.mark.asyncio
async def test_approval_job_failure_and_explicit_retry_are_auditable(
    api_client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exercise the durable API lifecycle without executing a real Git side effect."""
    from dualcode import api

    workspace, thread = await _workspace(api_client, tmp_path)
    prefix = f"/api/workspaces/{workspace['id']}/threads/{thread['id']}"
    requested = await api_client.post(f"{prefix}/git/actions", json={"action": "push", "message": ""})
    assert requested.status_code == 202
    approval_id = requested.json()["approval_id"]

    pending = await api_client.get(f"{prefix}/approvals")
    assert [item["id"] for item in pending.json()] == [approval_id]
    decided = await api_client.post(
        f"{prefix}/approvals/{approval_id}", json={"approved": True, "note": "acceptance"}
    )
    assert decided.status_code == 200

    jobs = (await api_client.get(f"{prefix}/jobs")).json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "READY"
    job_id = jobs[0]["id"]

    sessions = api_client._dualcode_test_sessions  # type: ignore[attr-defined]
    async with sessions() as db:
        job = await db.get(ExecutionJob, job_id)
        job.status = "INTERRUPTED"
        job.last_error = "simulated process interruption; outcome unknown"
        job.attempts = 1
        await db.commit()

    scheduled: list[str] = []
    monkeypatch.setattr(api, "_schedule_retry", scheduled.append)
    retried = await api_client.post(f"{prefix}/jobs/{job_id}/retry")
    assert retried.status_code == 202
    assert retried.json() == {"job_id": job_id, "status": "READY", "scheduled": True}
    assert scheduled == [job_id]

    duplicate = await api_client.post(f"{prefix}/jobs/{job_id}/retry")
    assert duplicate.status_code == 202
    assert duplicate.json()["scheduled"] is True
    assert scheduled == [job_id, job_id]

    async with sessions() as db:
        events = list(await db.scalars(select(AuditLog.event).order_by(AuditLog.created_at)))
    assert "git.action.requested" in events
    assert "approval.decided" in events


@pytest.mark.asyncio
async def test_attachment_diff_test_result_and_audit_chain(
    api_client: httpx.AsyncClient, tmp_path: Path
):
    workspace, thread = await _workspace(api_client, tmp_path)
    prefix = f"/api/workspaces/{workspace['id']}/threads/{thread['id']}"
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    uploaded = await api_client.post(
        f"{prefix}/attachments", files={"file": ("pixel.png", png, "image/png")}
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["name"] == "pixel.png"
    attachment_id = uploaded.json()["id"]

    sent = await api_client.post(
        f"{prefix}/messages",
        json={"content": "What is shown?", "mode": "codex", "attachment_ids": [attachment_id]},
    )
    assert sent.status_code == 202
    workspaces = (await api_client.get("/api/workspaces")).json()
    user_message = workspaces[0]["threads"][0]["messages"][-1]
    assert user_message["attachments"][0]["id"] == attachment_id
    content = await api_client.get(f"{prefix}/attachments/{attachment_id}/content")
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("image/png")

    image_only = await api_client.post(
        f"{prefix}/messages",
        json={"content": "", "mode": "codex", "attachment_ids": [attachment_id]},
    )
    assert image_only.status_code == 202

    collaboration = await api_client.post(
        f"{prefix}/messages",
        json={"content": "run the old pipeline", "mode": "collaboration"},
    )
    assert collaboration.status_code == 422

    sessions = api_client._dualcode_test_sessions  # type: ignore[attr-defined]
    async with sessions() as db:
        from dualcode.models import Attachment
        attachment = await db.get(Attachment, attachment_id)
        assert attachment is not None
        assert attachment.storage_key.endswith(".png")
        db.add(FileChange(thread_id=thread["id"], path="src/example.py", diff="+professional\n"))
        db.add(PersistedTestRun(thread_id=thread["id"], command="pytest -q", output="1 passed", exit_code=0))
        await db.commit()

    details = await api_client.get(f"{prefix}/details")
    assert details.status_code == 200
    assert details.json()["files"] == [{"path": "src/example.py"}]
    assert details.json()["diff"] == "+professional\n"
    assert details.json()["tests"][0]["exit_code"] == 0

    async with sessions() as db:
        audit = await db.scalar(
            select(AuditLog).where(AuditLog.workspace_id == workspace["id"], AuditLog.event == "attachment.created")
        )
    assert audit is not None
    assert "pixel.png" in audit.detail


@pytest.mark.asyncio
async def test_agent_diagnostics_report_independent_health(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from dualcode import api

    async def healthy() -> bool:
        return True

    async def unhealthy() -> bool:
        return False

    monkeypatch.setattr(api.CodexCliAdapter, "health_check", lambda self: healthy())
    monkeypatch.setattr(api.ClaudeCliAdapter, "health_check", lambda self: unhealthy())
    response = await api_client.get("/api/agents/health")
    assert response.status_code == 200
    assert response.json()["codex"]["healthy"] is True
    assert response.json()["claude"]["healthy"] is False


@pytest.mark.asyncio
async def test_project_governance_and_task_contract_gate(api_client: httpx.AsyncClient, tmp_path: Path):
    workspace, thread = await _workspace(api_client, tmp_path)
    prefix = f"/api/workspaces/{workspace['id']}/threads/{thread['id']}"
    initial = (await api_client.get(f"{prefix}/contract")).json()
    assert initial["gate"]["ready_for_implementation"] is False
    assert any("正式产品" in rule for rule in initial["governance"]["rules"])
    assert any("不得提前锁定技术框架" in rule for rule in initial["governance"]["rules"])
    assert any("fast-forward" in rule for rule in initial["governance"]["rules"])
    assert any("潜在问题" in rule for rule in initial["governance"]["rules"])
    assert len(initial["governance"]["rules"]) == 12
    assert len(initial["governance"]["deliverables"]) == 7
    assert (await api_client.put(f"/api/workspaces/{workspace['id']}/governance", json={
        "product_goal": "交付本地双 Agent 工程工作台", "product_boundary": "不是完整 IDE",
        "rules": ["所有状态必须持久化"], "deliverables": ["测试报告", "发布产物"],
    })).status_code == 200
    assert (await api_client.put(f"{prefix}/contract", json={
        "goal": "实现项目规则中心", "non_goals": ["本轮不自动提交"],
        "acceptance": ["重启后规则仍存在"], "constraints": ["不得使用演示数据"],
        "risks": ["旧数据库兼容"], "status": "READY",
    })).status_code == 200
    saved = (await api_client.get(f"{prefix}/contract")).json()
    assert saved["gate"] == {"ready_for_implementation": True, "missing": []}
    assert saved["task"]["acceptance"] == ["重启后规则仍存在"]
    assert saved["governance"]["rules"][-1] == "所有状态必须持久化"
    assert saved["governance"]["deliverables"] == ["测试报告", "发布产物"]

    prepared = await api_client.post(f"{prefix}/handoffs", json={"recipient": "claude", "purpose": "review"})
    assert prepared.status_code == 201
    package = prepared.json()
    assert package["payload"]["contract"]["task_goal"] == "实现项目规则中心"
    assert "messages" not in package["payload"]
    handoffs = (await api_client.get(f"{prefix}/handoffs")).json()
    assert handoffs[0]["recipient"] == "claude"
    assert handoffs[0]["status"] == "PREPARED"
