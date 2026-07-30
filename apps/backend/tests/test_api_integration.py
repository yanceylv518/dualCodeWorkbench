import os
import subprocess
import tempfile
import base64
import io
import zipfile
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
from dualcode.models import AuditLog, Base, ExecutionJob, FileChange, Message, TestRun as PersistedTestRun
from sqlalchemy import select


def _docx_bytes(text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                f"{text}</w:t></w:r></w:p></w:body></w:document>"
            ),
        )
    return output.getvalue()


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
async def test_editing_a_user_message_resends_and_persists_the_new_content(
    api_client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace, thread = await _workspace(api_client, tmp_path)
    sessions = api_client._dualcode_test_sessions  # type: ignore[attr-defined]
    message = Message(thread_id=thread["id"], role="user", content="original request")
    async with sessions() as db:
        db.add(message)
        await db.commit()
        await db.refresh(message)
        message_id = message.id

    started: list[tuple[str, str, str, list[str]]] = []

    async def fake_start(thread_id: str, prompt: str, mode: str, attachment_ids: list[str]):
        started.append((thread_id, prompt, mode, attachment_ids))
        return "edited-run"

    from dualcode import api_workspaces

    monkeypatch.setattr(api_workspaces.scheduler, "start", fake_start)
    response = await api_client.post(
        f"/api/workspaces/{workspace['id']}/threads/{thread['id']}/messages/{message_id}/retry",
        json={"content": "edited request"},
    )

    assert response.status_code == 202
    assert response.json() == {"run_id": "edited-run"}
    assert started == [(thread["id"], "edited request", "codex", [])]
    async with sessions() as db:
        persisted = await db.get(Message, message_id)
        event = await db.scalar(
            select(AuditLog.event).where(
                AuditLog.thread_id == thread["id"],
                AuditLog.event == "message.edited_and_retried",
            )
        )
    assert persisted is not None
    assert persisted.content == "edited request"
    assert event == "message.edited_and_retried"


@pytest.mark.asyncio
async def test_smart_message_is_accepted_when_feature_flag_is_enabled(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace, thread = await _workspace(api_client, tmp_path)
    started: list[str] = []

    async def fake_start(thread_id, prompt, mode, attachment_ids):
        started.append(mode)
        return "smart-run"

    from dualcode import api_workspaces
    monkeypatch.setattr(api_workspaces.scheduler, "start", fake_start)
    assert (await _set_smart_collaboration(api_client, True)).status_code == 200
    assert (await api_client.get("/api/capabilities")).json() == {
        "smart_collaboration_enabled": True
    }
    response = await api_client.post(
        f"/api/workspaces/{workspace['id']}/threads/{thread['id']}/messages",
        json={"content": "增加收藏功能", "mode": "smart"},
    )

    assert response.status_code == 202
    assert response.json()["run_id"] == "smart-run"
    assert started == ["smart"]

    assert (await _set_smart_collaboration(api_client, False)).status_code == 200
    assert (await api_client.get("/api/capabilities")).json() == {
        "smart_collaboration_enabled": False
    }
    rejected = await api_client.post(
        f"/api/workspaces/{workspace['id']}/threads/{thread['id']}/messages",
        json={"content": "再次增加收藏功能", "mode": "smart"},
    )
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_thread_can_be_renamed_and_deleted_with_audit(
    api_client: httpx.AsyncClient, tmp_path: Path
):
    repository = tmp_path / "thread-management"
    repository.mkdir()
    (repository / ".git").mkdir()
    workspace = (
        await api_client.post("/api/workspaces", json={"path": str(repository)})
    ).json()
    thread_id = workspace["threads"][0]["id"]

    renamed = await api_client.patch(
        f"/api/workspaces/{workspace['id']}/threads/{thread_id}",
        json={"title": "正式任务名称"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "正式任务名称"

    removed = await api_client.delete(
        f"/api/workspaces/{workspace['id']}/threads/{thread_id}"
    )
    assert removed.status_code == 204
    listed = (await api_client.get("/api/workspaces")).json()
    assert listed[0]["threads"] == []

    sessions = api_client._dualcode_test_sessions  # type: ignore[attr-defined]
    async with sessions() as db:
        events = list(
            await db.scalars(
                select(AuditLog.event).where(AuditLog.thread_id == thread_id)
            )
        )
    assert "thread.renamed" in events
    assert "thread.deleted" in events


@pytest.mark.asyncio
async def test_workspace_creation_rejects_non_repository(api_client: httpx.AsyncClient, tmp_path: Path):
    directory = tmp_path / "not-a-repository"
    directory.mkdir()

    response = await api_client.post("/api/workspaces", json={"path": str(directory)})

    assert response.status_code == 400
    assert response.json()["detail"] == "项目必须是 Git 仓库"


@pytest.mark.asyncio
async def test_user_facing_http_errors_are_localized(
    api_client: httpx.AsyncClient, tmp_path: Path
):
    workspace, thread = await _workspace(api_client, tmp_path)
    prefix = f"/api/workspaces/{workspace['id']}/threads/{thread['id']}"

    blank_title = await api_client.patch(
        prefix,
        json={"title": "   "},
    )
    assert blank_title.status_code == 422
    assert blank_title.json()["detail"] == "任务标题不能为空"

    missing_approval = await api_client.post(
        f"{prefix}/approvals/missing",
        json={"approved": True, "note": ""},
    )
    assert missing_approval.status_code == 404
    assert missing_approval.json()["detail"] == "未找到待处理审批"

    unsupported = await api_client.post(
        f"{prefix}/attachments",
        files={"file": ("archive.zip", b"not-a-zip", "application/zip")},
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["detail"] == "不支持此附件类型"

    legacy_word = await api_client.post(
        f"{prefix}/attachments",
        files={"file": ("legacy.doc", b"legacy", "application/msword")},
    )
    assert legacy_word.status_code == 415
    assert legacy_word.json()["detail"] == "暂不支持旧版 .doc，请另存为 .docx 后上传"


@pytest.mark.asyncio
async def test_docx_attachment_is_validated_stored_and_downloadable(
    api_client: httpx.AsyncClient, tmp_path: Path
):
    workspace, thread = await _workspace(api_client, tmp_path)
    prefix = f"/api/workspaces/{workspace['id']}/threads/{thread['id']}"
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    document = _docx_bytes("产品需求说明")

    uploaded = await api_client.post(
        f"{prefix}/attachments",
        files={"file": ("requirements.docx", document, media_type)},
    )

    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["name"] == "requirements.docx"
    assert uploaded.json()["media_type"] == media_type
    content = await api_client.get(
        f"{prefix}/attachments/{uploaded.json()['id']}/content"
    )
    assert content.status_code == 200
    assert content.content == document

    invalid = await api_client.post(
        f"{prefix}/attachments",
        files={"file": ("broken.docx", b"not-a-document", media_type)},
    )
    assert invalid.status_code == 400
    assert "Word 文档" in invalid.json()["detail"]



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
    (repository / "README.md").write_text("integration repository\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=DualCode Test",
            "-c",
            "user.email=dualcode@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    workspace = (await api_client.post("/api/workspaces", json={"path": str(repository)})).json()
    return workspace, workspace["threads"][0]


async def _set_smart_collaboration(
    api_client: httpx.AsyncClient, enabled: bool
) -> httpx.Response:
    current = (await api_client.get("/api/settings/agents")).json()
    current["smart_collaboration_enabled"] = enabled
    return await api_client.put("/api/settings/agents", json=current)


def _collaboration_headers(workspace_id: str, thread_id: str) -> dict[str, str]:
    return {
        "X-DualCode-Workspace-Id": workspace_id,
        "X-DualCode-Thread-Id": thread_id,
    }


@pytest.mark.asyncio
async def test_collaboration_run_api_obeys_flag_and_returns_current(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
):
    workspace, thread = await _workspace(api_client, tmp_path)
    path = (
        f"/api/workspaces/{workspace['id']}/threads/{thread['id']}"
        "/collaboration-runs"
    )
    assert (await _set_smart_collaboration(api_client, False)).status_code == 200
    disabled = await api_client.post(path, json={"goal": "实现正式功能"})
    assert disabled.status_code == 422
    assert disabled.json()["detail"] == "智能协作功能尚未启用"

    assert (await _set_smart_collaboration(api_client, True)).status_code == 200
    created = await api_client.post(path, json={"goal": "实现正式功能"})
    assert created.status_code == 201
    assert created.json()["state"] == "WAITING_USER"
    current = await api_client.get(f"{path}/current")
    assert current.status_code == 200
    assert current.json()["id"] == created.json()["id"]
    assert (await _set_smart_collaboration(api_client, False)).status_code == 200


@pytest.mark.asyncio
async def test_collaboration_run_controls_enforce_ownership_and_resume(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from dualcode.api_collaboration import scheduler
    from dualcode.models import CollaborationRun

    started: list[tuple[str, str, str]] = []

    async def start(thread_id: str, run_id: str, prompt: str) -> str:
        started.append((thread_id, run_id, prompt))
        return run_id

    async def cancel(_thread_id: str) -> None:
        return None

    monkeypatch.setattr(scheduler, "start_collaboration_run", start)
    monkeypatch.setattr(scheduler, "cancel", cancel)
    workspace, thread = await _workspace(api_client, tmp_path)
    sessions = api_client._dualcode_test_sessions  # type: ignore[attr-defined]
    async with sessions() as db:
        run = CollaborationRun(
            workspace_id=workspace["id"],
            thread_id=thread["id"],
            mode="smart",
            state="IMPLEMENTING",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id

    wrong = await api_client.post(
        f"/api/collaboration-runs/{run_id}/pause",
        headers=_collaboration_headers(workspace["id"], "another-thread"),
    )
    assert wrong.status_code == 404
    paused = await api_client.post(
        f"/api/collaboration-runs/{run_id}/pause",
        headers=_collaboration_headers(workspace["id"], thread["id"]),
    )
    assert paused.status_code == 200
    assert paused.json()["state"] == "BLOCKED"
    resumed = await api_client.post(
        f"/api/collaboration-runs/{run_id}/resume",
        headers=_collaboration_headers(workspace["id"], thread["id"]),
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "IMPLEMENTING"
    assert started == [(thread["id"], run_id, "")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected"),
    [("reenter", "READY"), ("fix", "FIXING"), ("cancel", "CANCELLED")],
)
async def test_collaboration_decisions_cover_waiting_user_exits(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    action: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
):
    from dualcode.api_collaboration import scheduler
    from dualcode.models import CollaborationRun

    started: list[str] = []

    async def start(_thread_id: str, run_id: str, _prompt: str) -> str:
        started.append(run_id)
        return run_id

    async def cancel(_thread_id: str) -> None:
        return None

    monkeypatch.setattr(scheduler, "start_collaboration_run", start)
    monkeypatch.setattr(scheduler, "cancel", cancel)
    workspace, thread = await _workspace(api_client, tmp_path)
    sessions = api_client._dualcode_test_sessions  # type: ignore[attr-defined]
    async with sessions() as db:
        run = CollaborationRun(
            workspace_id=workspace["id"],
            thread_id=thread["id"],
            mode="smart",
            state="WAITING_USER",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id
    response = await api_client.post(
        f"/api/collaboration-runs/{run_id}/decisions",
        params={"workspace_id": workspace["id"], "thread_id": thread["id"]},
        json={"action": action, "note": "用户选择"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == expected
    assert started == ([] if action == "cancel" else [run_id])


@pytest.mark.asyncio
async def test_collaboration_findings_are_scoped_and_ordered(
    api_client: httpx.AsyncClient, tmp_path: Path
):
    from dualcode.models import CollaborationRun, HandoffPackage, ReviewFinding

    workspace, thread = await _workspace(api_client, tmp_path)
    sessions = api_client._dualcode_test_sessions  # type: ignore[attr-defined]
    async with sessions() as db:
        run = CollaborationRun(
            workspace_id=workspace["id"],
            thread_id=thread["id"],
            mode="smart",
            state="WAITING_USER",
        )
        handoff = HandoffPackage(
            workspace_id=workspace["id"],
            thread_id=thread["id"],
            recipient="claude",
            purpose="review",
        )
        db.add_all([run, handoff])
        await db.flush()
        db.add_all(
            [
                ReviewFinding(
                    collaboration_run_id=run.id,
                    round=2,
                    type="risk",
                    severity="advisory",
                    status="open",
                    description="次要问题",
                    acceptance="记录风险",
                    source_handoff_id=handoff.id,
                ),
                ReviewFinding(
                    collaboration_run_id=run.id,
                    round=1,
                    type="missing",
                    severity="blocking",
                    status="open",
                    description="缺少实现",
                    acceptance="补齐实现",
                    source_handoff_id=handoff.id,
                ),
            ]
        )
        await db.commit()
        run_id = run.id
    response = await api_client.get(
        f"/api/collaboration-runs/{run_id}/findings",
        headers=_collaboration_headers(workspace["id"], thread["id"]),
    )
    assert response.status_code == 200
    assert [item["round"] for item in response.json()] == [1, 2]


@pytest.mark.asyncio
async def test_approval_job_failure_and_explicit_retry_are_auditable(
    api_client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exercise the durable API lifecycle without executing a real Git side effect."""
    from dualcode import api_jobs as api

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
    smart_disabled = await api_client.post(
        f"{prefix}/messages",
        json={"content": "智能处理这个需求", "mode": "smart"},
    )
    assert smart_disabled.status_code == 422
    assert smart_disabled.json()["detail"] == "智能协作尚未启用"

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
    from dualcode import api_agents as api

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
async def test_capabilities_exposes_smart_collaboration_flag(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from dualcode import api_agents as api

    monkeypatch.setattr(api, "is_smart_collaboration_enabled", lambda: True)
    response = await api_client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json() == {"smart_collaboration_enabled": True}


@pytest.mark.asyncio
async def test_project_governance_and_task_contract_gate(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
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

    assert (await _set_smart_collaboration(api_client, True)).status_code == 200
    v2_prepared = await api_client.post(
        f"{prefix}/handoffs",
        json={"recipient": "claude", "purpose": "review"},
    )
    assert v2_prepared.status_code == 201
    v2_payload = v2_prepared.json()["payload"]
    assert v2_payload["schema"] == "handoff.v2"
    assert v2_payload["task"]["goal"] == "实现项目规则中心"
    assert v2_payload["sender"] == "codex"
    assert v2_payload["recipient"] == "claude"
    assert "contract" not in v2_payload

    handoffs = (await api_client.get(f"{prefix}/handoffs")).json()
    assert handoffs[0]["recipient"] == "claude"
    assert handoffs[0]["status"] == "PREPARED"
    assert (await _set_smart_collaboration(api_client, False)).status_code == 200
