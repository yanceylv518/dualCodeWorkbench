from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="dualcode-c5-e2e-"))

from dualcode.api_collaboration import scheduler
from dualcode.collaboration_orchestrator import (
    ReviewTurnResult,
    SnapshotEvidence,
    StageCallbacks,
    advance,
    execute_pipeline,
    recover_interrupted_runs,
)
from dualcode.collaboration_protocol import CollaborationState
from dualcode.config import sidecar_token
from dualcode.database import get_session
from dualcode.main import app
from dualcode.models import (
    Approval,
    Base,
    CollaborationRun,
    HandoffPackage,
    TaskContract,
)


@dataclass(frozen=True)
class Turn:
    status: str = "completed"
    error: str = ""


@pytest.fixture
async def c5_api(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'c5-e2e.db'}")
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
        current = (await client.get("/api/settings/agents")).json()
        current["smart_collaboration_enabled"] = True
        assert (
            await client.put("/api/settings/agents", json=current)
        ).status_code == 200
        yield client, sessions, tmp_path
        current["smart_collaboration_enabled"] = False
        assert (
            await client.put("/api/settings/agents", json=current)
        ).status_code == 200
    app.dependency_overrides.clear()
    await engine.dispose()


async def _workspace(client: httpx.AsyncClient, root: Path):
    repository = root / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repository)], check=True)
    (repository / "README.md").write_text("C5 E2E\n", encoding="utf-8")
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
    bare = root / "vps.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    workspace = (
        await client.post("/api/workspaces", json={"path": str(repository)})
    ).json()
    thread = workspace["threads"][0]
    return workspace, thread, repository, bare


async def _ready_contract(sessions, thread_id: str) -> None:
    async with sessions() as db:
        db.add(
            TaskContract(
                thread_id=thread_id,
                goal="实现并审查功能",
                acceptance=json.dumps(["审查通过"], ensure_ascii=False),
                status="READY",
            )
        )
        await db.commit()


def _headers(workspace_id: str, thread_id: str) -> dict[str, str]:
    return {
        "X-DualCode-Workspace-Id": workspace_id,
        "X-DualCode-Thread-Id": thread_id,
    }


def _review(verdict: str, findings: list[dict] | None = None) -> str:
    return (
        "review\n```json\n"
        + json.dumps(
            {
                "schema": "review.v1",
                "verdict": verdict,
                "summary": f"{verdict} summary",
                "findings": findings or [],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )


def _blocking() -> dict[str, object]:
    return {
        "id": "F-1",
        "type": "regression",
        "severity": "blocking",
        "file": "src/app.py",
        "line": None,
        "description": "功能仍未满足验收",
        "acceptance": "补齐实现并复验",
    }


async def _run(sessions, run_id: str) -> CollaborationRun:
    async with sessions() as db:
        run = await db.get(CollaborationRun, run_id)
        assert run is not None
        return run


def _pipeline_driver(
    monkeypatch: pytest.MonkeyPatch,
    sessions,
    reviews: list[str],
    *,
    codex_stages: list[CollaborationState] | None = None,
) -> list[asyncio.Task[None]]:
    tasks: list[asyncio.Task[None]] = []

    async def drive(thread_id: str, run_id: str, prompt: str) -> None:
        async with sessions() as db:
            run = await db.get(CollaborationRun, run_id)
            assert run is not None
            package = HandoffPackage(
                workspace_id=run.workspace_id,
                thread_id=thread_id,
                recipient="claude",
                purpose="review",
            )
            db.add(package)
            await db.flush()
            scripted = iter(reviews)

            async def codex(_prompt: str, stage: CollaborationState) -> Turn:
                if codex_stages is not None:
                    codex_stages.append(stage)
                return Turn()

            callbacks = StageCallbacks(
                run_codex=codex,
                run_tests=lambda: _value(True),
                sync_snapshot=lambda: _value(
                    SnapshotEvidence("a" * 40, "b" * 40)
                ),
                run_review=lambda _suffix, _snapshot: _value(
                    ReviewTurnResult(next(scripted), package.id)
                ),
                record_system=lambda _text: _value(None),
            )
            await execute_pipeline(db, run, initial_prompt=prompt, callbacks=callbacks)
            await db.commit()

    async def start(thread_id: str, run_id: str, prompt: str) -> str:
        task = asyncio.create_task(drive(thread_id, run_id, prompt))
        tasks.append(task)
        return run_id

    monkeypatch.setattr(scheduler, "start_collaboration_run", start)
    return tasks


async def _start(client, workspace, thread):
    response = await client.post(
        f"/api/workspaces/{workspace['id']}/threads/{thread['id']}/collaboration-runs",
        json={"goal": "实现并审查功能"},
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_c5_e2e_pass_completes(c5_api, monkeypatch: pytest.MonkeyPatch):
    client, sessions, root = c5_api
    workspace, thread, _repository, _bare = await _workspace(client, root)
    await _ready_contract(sessions, thread["id"])
    tasks = _pipeline_driver(monkeypatch, sessions, [_review("pass")])

    run_id = await _start(client, workspace, thread)
    await tasks[0]

    assert (await _run(sessions, run_id)).state == "COMPLETED"


@pytest.mark.asyncio
async def test_c5_e2e_one_fix_then_pass(c5_api, monkeypatch: pytest.MonkeyPatch):
    client, sessions, root = c5_api
    workspace, thread, _repository, _bare = await _workspace(client, root)
    await _ready_contract(sessions, thread["id"])
    stages: list[CollaborationState] = []
    tasks = _pipeline_driver(
        monkeypatch,
        sessions,
        [_review("blocking", [_blocking()]), _review("pass")],
        codex_stages=stages,
    )

    run_id = await _start(client, workspace, thread)
    await tasks[0]
    run = await _run(sessions, run_id)

    assert run.state == "COMPLETED"
    assert run.round == 2
    assert stages == [CollaborationState.IMPLEMENTING, CollaborationState.FIXING]


@pytest.mark.asyncio
async def test_c5_e2e_blocking_limit_waits_with_findings(
    c5_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, root = c5_api
    workspace, thread, _repository, _bare = await _workspace(client, root)
    await _ready_contract(sessions, thread["id"])
    tasks = _pipeline_driver(
        monkeypatch,
        sessions,
        [_review("blocking", [_blocking()])] * 3,
    )

    run_id = await _start(client, workspace, thread)
    await tasks[0]
    findings = await client.get(
        f"/api/collaboration-runs/{run_id}/findings",
        headers=_headers(workspace["id"], thread["id"]),
    )

    assert (await _run(sessions, run_id)).state == "WAITING_USER"
    assert findings.status_code == 200
    assert any(item["status"] == "open" for item in findings.json())


@pytest.mark.asyncio
async def test_c5_e2e_approval_then_resume_completes(
    c5_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, root = c5_api
    workspace, thread, _repository, _bare = await _workspace(client, root)
    await _ready_contract(sessions, thread["id"])
    tasks = _pipeline_driver(monkeypatch, sessions, [_review("pass")])
    async with sessions() as db:
        run = CollaborationRun(
            workspace_id=workspace["id"],
            thread_id=thread["id"],
            mode="smart",
            state="IMPLEMENTING",
        )
        db.add(run)
        await db.flush()
        await advance(
            db, run, CollaborationState.WAITING_APPROVAL, reason="等待文件编辑审批"
        )
        approval = Approval(
            thread_id=thread["id"],
            action="edit_files",
            reason="完成实现",
        )
        db.add(approval)
        await db.commit()
        run_id, approval_id = run.id, approval.id

    decided = await client.post(
        f"/api/workspaces/{workspace['id']}/threads/{thread['id']}/approvals/{approval_id}",
        json={"approved": True, "scope": "once", "note": "批准"},
    )
    resumed = await client.post(
        f"/api/collaboration-runs/{run_id}/resume",
        headers=_headers(workspace["id"], thread["id"]),
    )
    await tasks[0]

    assert decided.status_code == 200
    assert resumed.status_code == 200
    assert (await _run(sessions, run_id)).state == "COMPLETED"


@pytest.mark.asyncio
async def test_c5_e2e_cancel_review_cleans_shadow_ref(
    c5_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, root = c5_api
    workspace, thread, repository, bare = await _workspace(client, root)
    await _ready_contract(sessions, thread["id"])
    ref = f"refs/dualcode/relay/{workspace['id']}/{thread['id']}"
    subprocess.run(
        ["git", "-C", str(repository), "push", str(bare), f"HEAD:{ref}"],
        check=True,
        capture_output=True,
    )
    cancelled: list[str] = []

    async def cancel(thread_id: str) -> None:
        cancelled.append(thread_id)
        subprocess.run(
            ["git", "--git-dir", str(bare), "update-ref", "-d", ref],
            check=True,
        )

    monkeypatch.setattr(scheduler, "cancel", cancel)
    async with sessions() as db:
        run = CollaborationRun(
            workspace_id=workspace["id"],
            thread_id=thread["id"],
            mode="smart",
            state="REVIEWING",
        )
        db.add(run)
        await db.commit()
        run_id = run.id

    response = await client.post(
        f"/api/collaboration-runs/{run_id}/cancel",
        headers=_headers(workspace["id"], thread["id"]),
    )
    refs = subprocess.run(
        ["git", "--git-dir", str(bare), "show-ref", ref],
        capture_output=True,
        text=True,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "CANCELLED"
    assert cancelled == [thread["id"]]
    assert refs.returncode == 1


@pytest.mark.asyncio
async def test_c5_e2e_restart_blocks_and_resumes_without_replay(
    c5_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, root = c5_api
    workspace, thread, _repository, _bare = await _workspace(client, root)
    await _ready_contract(sessions, thread["id"])
    starts: list[str] = []

    async def start(_thread_id: str, run_id: str, _prompt: str) -> str:
        starts.append(run_id)
        return run_id

    monkeypatch.setattr(scheduler, "start_collaboration_run", start)
    async with sessions() as db:
        run = CollaborationRun(
            workspace_id=workspace["id"],
            thread_id=thread["id"],
            mode="smart",
            state="IMPLEMENTING",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        assert await recover_interrupted_runs(db) == [run_id]
        await db.commit()

    blocked = await _run(sessions, run_id)
    resumed = await client.post(
        f"/api/collaboration-runs/{run_id}/resume",
        headers=_headers(workspace["id"], thread["id"]),
    )

    assert blocked.state == "BLOCKED"
    assert blocked.error == "应用重启中断"
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "IMPLEMENTING"
    assert starts == [run_id]


async def _value(value):
    return value
