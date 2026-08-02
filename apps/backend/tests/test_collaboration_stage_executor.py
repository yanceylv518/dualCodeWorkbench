from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode.collaboration_orchestrator import (
    ReviewTurnResult,
    SnapshotEvidence,
    StageCallbacks,
    execute_pipeline,
)
from dualcode.collaboration_protocol import CollaborationState
from dualcode.models import (
    Base,
    CollaborationRun,
    HandoffPackage,
    ReviewFinding,
    Thread,
    Workspace,
)


@dataclass(frozen=True)
class Turn:
    status: str = "completed"
    content: str = ""
    error: str = ""


@pytest.fixture
async def stage_context(tmp_path, monkeypatch: pytest.MonkeyPatch):
    async def publish(_event):
        return None

    monkeypatch.setattr(
        "dualcode.collaboration_orchestrator.manager.publish", publish
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'stages.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        workspace = Workspace(name="Project", path=str(tmp_path))
        db.add(workspace)
        await db.flush()
        thread = Thread(workspace_id=workspace.id, title="Task")
        db.add(thread)
        await db.flush()
        run = CollaborationRun(
            workspace_id=workspace.id,
            thread_id=thread.id,
            mode="smart",
            state=CollaborationState.READY.value,
            current_agent="codex",
            round=1,
            max_rounds=3,
        )
        db.add(run)
        await db.flush()
        yield db, workspace, thread, run
    await engine.dispose()


def _review(verdict: str, findings: list[dict] | None = None) -> str:
    return (
        "审查结论。\n```json\n"
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


@pytest.mark.asyncio
async def test_pass_completes_all_stages(stage_context):
    db, _workspace, _thread, run = stage_context
    systems: list[str] = []
    visible_reviews: list[str] = []
    callbacks = StageCallbacks(
        run_codex=lambda _prompt, _stage: _async(Turn()),
        run_tests=lambda: _async(True),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=lambda suffix, _snapshot: _async(
            ReviewTurnResult(_review("pass"), "unused")
        ),
        record_system=lambda text: _append(systems, text),
        record_review=lambda text: _append(visible_reviews, text),
    )

    result = await execute_pipeline(
        db, run, initial_prompt="implement", callbacks=callbacks
    )

    assert result.state == CollaborationState.COMPLETED.value
    assert result.snapshot_sha == "b" * 40
    assert systems == ["智能协作已完成，共经过 1 轮审查。"]
    assert visible_reviews == ["审查通过：pass summary"]


@pytest.mark.asyncio
async def test_blocking_compiles_fix_prompt_and_resolves_after_pass(stage_context):
    db, workspace, thread, run = stage_context
    package = HandoffPackage(
        workspace_id=workspace.id,
        thread_id=thread.id,
        recipient="claude",
        purpose="review",
    )
    db.add(package)
    await db.flush()
    prompts: list[tuple[str, CollaborationState]] = []
    reviews = iter(
        [
            _review(
                "blocking",
                [
                    {
                        "id": "F-1",
                        "type": "architecture",
                        "severity": "blocking",
                        "file": "src/app.py",
                        "line": "12",
                        "description": "使用了临时存储",
                        "acceptance": "改为正式持久化",
                    }
                ],
            ),
            _review("pass"),
        ]
    )

    async def codex(prompt, stage):
        prompts.append((prompt, stage))
        return Turn()

    callbacks = StageCallbacks(
        run_codex=codex,
        run_tests=lambda: _async(True),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=lambda _suffix, _snapshot: _async(
            ReviewTurnResult(next(reviews), package.id)
        ),
        record_system=lambda _text: _async(None),
    )
    result = await execute_pipeline(
        db, run, initial_prompt="implement", callbacks=callbacks
    )
    finding = await db.scalar(
        select(ReviewFinding).where(
            ReviewFinding.collaboration_run_id == run.id
        )
    )

    assert result.state == CollaborationState.COMPLETED.value
    assert result.round == 2
    assert prompts[1][1] is CollaborationState.FIXING
    assert "src/app.py:12" in prompts[1][0]
    assert "使用了临时存储" in prompts[1][0]
    assert "改为正式持久化" in prompts[1][0]
    assert finding is not None
    assert finding.status == "resolved"
    assert finding.resolved_by_snapshot_sha == "b" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["没有 JSON", "```json\n{x}\n```"])
async def test_parse_failure_retries_then_blocks_without_user_prompt(stage_context, raw):
    db, _workspace, _thread, run = stage_context
    systems: list[str] = []
    review_prompts: list[str] = []

    async def review(prompt, _snapshot):
        review_prompts.append(prompt)
        return ReviewTurnResult(raw, "unused")

    callbacks = StageCallbacks(
        run_codex=lambda _prompt, _stage: _async(Turn()),
        run_tests=lambda: _async(None),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=review,
        record_system=lambda text: _append(systems, text),
    )
    result = await execute_pipeline(
        db, run, initial_prompt="implement", callbacks=callbacks
    )

    assert result.state == CollaborationState.BLOCKED.value
    expected = "no_json" if raw == "没有 JSON" else "invalid_json"
    assert result.error == f"审查结果协议校验失败：{expected}"
    assert len(review_prompts) == 2
    assert "不是用户需求不明确" in review_prompts[1]
    assert systems == ["尚未配置测试命令，本轮未生成测试证据。"]


@pytest.mark.asyncio
async def test_parse_failure_recovers_after_protocol_retry(stage_context):
    db, _workspace, _thread, run = stage_context
    reviews = iter(["no protocol", _review("pass")])
    prompts: list[str] = []

    async def review(prompt, _snapshot):
        prompts.append(prompt)
        return ReviewTurnResult(next(reviews), "unused")

    callbacks = StageCallbacks(
        run_codex=lambda _prompt, _stage: _async(Turn()),
        run_tests=lambda: _async(True),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=review,
        record_system=lambda _text: _async(None),
    )

    result = await execute_pipeline(
        db, run, initial_prompt="implement", callbacks=callbacks
    )

    assert result.state == CollaborationState.COMPLETED.value
    assert len(prompts) == 2
    assert "不是用户需求不明确" in prompts[1]


@pytest.mark.asyncio
async def test_needs_user_waits_and_preserves_raw(stage_context):
    db, _workspace, _thread, run = stage_context
    raw = _review("needs_user")
    systems: list[str] = []
    callbacks = StageCallbacks(
        run_codex=lambda _prompt, _stage: _async(Turn()),
        run_tests=lambda: _async(True),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=lambda _suffix, _snapshot: _async(
            ReviewTurnResult(raw, "unused")
        ),
        record_system=lambda text: _append(systems, text),
    )
    result = await execute_pipeline(
        db, run, initial_prompt="implement", callbacks=callbacks
    )

    assert result.state == CollaborationState.WAITING_USER.value
    assert systems == [raw]


@pytest.mark.asyncio
async def test_blocking_stops_after_two_fix_rounds(stage_context):
    db, workspace, thread, run = stage_context
    package = HandoffPackage(
        workspace_id=workspace.id,
        thread_id=thread.id,
        recipient="claude",
        purpose="review",
    )
    db.add(package)
    await db.flush()
    finding = {
        "id": "F-limit",
        "type": "regression",
        "severity": "blocking",
        "file": "src/app.py",
        "line": None,
        "description": "still broken",
        "acceptance": "prove fixed",
    }
    fixes: list[CollaborationState] = []

    async def codex(_prompt, stage):
        fixes.append(stage)
        return Turn()

    callbacks = StageCallbacks(
        run_codex=codex,
        run_tests=lambda: _async(True),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=lambda _suffix, _snapshot: _async(
            ReviewTurnResult(_review("blocking", [finding]), package.id)
        ),
        record_system=lambda _text: _async(None),
    )

    result = await execute_pipeline(
        db, run, initial_prompt="implement", callbacks=callbacks
    )

    assert result.state == CollaborationState.WAITING_USER.value
    assert result.round == 3
    assert fixes.count(CollaborationState.FIXING) == 2
    assert json.loads(result.budget_json)["_fix_count"] == 2


@pytest.mark.asyncio
async def test_review_agent_failure_blocks_once_and_emits_failed(stage_context, monkeypatch):
    db, _workspace, _thread, run = stage_context
    events = []

    async def publish(event):
        events.append(event)

    monkeypatch.setattr(
        "dualcode.collaboration_orchestrator.manager.publish", publish
    )
    attempts = 0

    async def review(_suffix, _snapshot):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("offline")

    callbacks = StageCallbacks(
        run_codex=lambda _prompt, _stage: _async(Turn()),
        run_tests=lambda: _async(True),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=review,
        record_system=lambda _text: _async(None),
    )

    result = await execute_pipeline(
        db, run, initial_prompt="implement", callbacks=callbacks
    )

    assert result.state == CollaborationState.BLOCKED.value
    assert attempts == 1
    assert events[-1].type.value == "collaboration.failed"
    assert events[-1].payload["recoverable"] is True


@pytest.mark.asyncio
async def test_pass_emits_complete_compact_event_sequence(stage_context, monkeypatch):
    db, workspace, thread, run = stage_context
    package = HandoffPackage(
        workspace_id=workspace.id,
        thread_id=thread.id,
        recipient="claude",
        purpose="review",
    )
    db.add(package)
    await db.flush()
    events = []

    async def publish(event):
        events.append(event)

    monkeypatch.setattr(
        "dualcode.collaboration_orchestrator.manager.publish", publish
    )
    callbacks = StageCallbacks(
        run_codex=lambda _prompt, _stage: _async(Turn()),
        run_tests=lambda: _async(True),
        sync_snapshot=lambda: _async(SnapshotEvidence("a" * 40, "b" * 40)),
        run_review=lambda _suffix, _snapshot: _async(
            ReviewTurnResult(_review("pass"), package.id)
        ),
        record_system=lambda _text: _async(None),
    )

    await execute_pipeline(db, run, initial_prompt="secret prompt", callbacks=callbacks)
    types = [event.type.value for event in events]

    assert "collaboration.agent_changed" in types
    assert "collaboration.handoff_prepared" in types
    assert "collaboration.review_completed" in types
    assert "collaboration.findings_updated" in types
    assert types[-1] == "collaboration.completed"
    for event in events:
        serialized = json.dumps(event.payload)
        assert "secret prompt" not in serialized
        assert "raw_text" not in event.payload
        assert "diff" not in event.payload


async def _async(value):
    return value


async def _append(items: list[str], value: str):
    items.append(value)
