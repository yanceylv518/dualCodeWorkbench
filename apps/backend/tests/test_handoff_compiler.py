import json
import subprocess

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode.handoff_compiler import compile_handoff_v2
from dualcode.models import (
    Base,
    FileChange,
    HandoffPackage,
    ReviewFinding,
    TaskContract,
    TestRun as RunRecord,
    Thread,
    Workspace,
)


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'handoff.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _repository(path) -> str:
    path.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    (path / "README.md").write_text("project", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
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
    return (
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
    )


@pytest.mark.asyncio
async def test_compile_handoff_v2_projects_real_sources_without_large_fields(
    session, tmp_path
) -> None:
    repository = tmp_path / "repository"
    head = _repository(repository)
    workspace = Workspace(name="Project", path=str(repository))
    session.add(workspace)
    await session.flush()
    thread = Thread(workspace_id=workspace.id, title="Task")
    session.add(thread)
    await session.flush()
    session.add(
        TaskContract(
            thread_id=thread.id,
            goal="Implement structured handoff",
            non_goals=json.dumps(["No orchestration"]),
            acceptance=json.dumps(["Payload validates"]),
            constraints=json.dumps(["No demo shortcuts"]),
            risks=json.dumps(["Schema drift"]),
        )
    )
    session.add_all(
        [
            FileChange(
                thread_id=thread.id,
                path="apps/backend/a.py",
                diff="SECRET DIFF " + "x" * 5000,
            ),
            FileChange(
                thread_id=thread.id,
                path="apps/backend/b.py",
                diff="SECOND SECRET DIFF",
            ),
            RunRecord(
                thread_id=thread.id,
                command="pytest -q",
                output="SECRET OUTPUT " + "x" * 5000,
                exit_code=0,
            ),
        ]
    )
    await session.flush()
    previous_handoff = HandoffPackage(
        workspace_id=workspace.id,
        thread_id=thread.id,
        recipient="claude",
        purpose="review",
    )
    session.add(previous_handoff)
    await session.flush()
    session.add(
        ReviewFinding(
            id="open-finding",
            collaboration_run_id=None,
            round=1,
            type="risk",
            severity="blocking",
            status="open",
            file=None,
            line=None,
            description="Existing unresolved risk",
            acceptance="Risk resolved",
            source_handoff_id=previous_handoff.id,
            resolved_by_snapshot_sha=None,
        )
    )
    await session.flush()

    handoff = await compile_handoff_v2(
        session,
        workspace,
        thread,
        purpose="review",
        sender="codex",
        recipient="claude",
    )

    assert handoff.schema_version == "handoff.v2"
    assert handoff.task.model_dump() == {
        "goal": "Implement structured handoff",
        "non_goals": ["No orchestration"],
        "acceptance": ["Payload validates"],
        "constraints": ["No demo shortcuts"],
    }
    assert handoff.repository.base_sha == head
    assert handoff.repository.snapshot_sha == head
    assert len(handoff.repository.base_sha) == 40
    assert handoff.repository.branch == "main"
    assert handoff.repository.changed_files == [
        "apps/backend/a.py",
        "apps/backend/b.py",
    ]
    assert handoff.repository.diff_stats == {"files": 2}
    assert handoff.evidence[0].model_dump() == {
        "type": "test",
        "command": "pytest -q",
        "exit_code": 0,
        "summary": "pytest -q → exit 0",
    }
    assert handoff.claims == []
    assert handoff.open_findings == ["Existing unresolved risk"]
    assert handoff.risks == ["Schema drift"]
    serialized = handoff.model_dump_json()
    assert "SECRET DIFF" not in serialized
    assert "SECRET OUTPUT" not in serialized


@pytest.mark.asyncio
async def test_compile_handoff_v2_propagates_model_validation_failure(
    session, tmp_path
) -> None:
    repository = tmp_path / "invalid-repository"
    _repository(repository)
    workspace = Workspace(name="Project", path=str(repository))
    session.add(workspace)
    await session.flush()
    thread = Thread(workspace_id=workspace.id, title="Task")
    session.add(thread)
    await session.flush()

    with pytest.raises(ValidationError):
        await compile_handoff_v2(
            session,
            workspace,
            thread,
            purpose="unsupported",
            sender="codex",
            recipient="claude",
        )
