import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dualcode.collaboration_audit import EVENT_REVIEW_VERDICT, ReviewVerdictDetail
from dualcode.collaboration_protocol import ReviewV1
from dualcode.models import (
    AuditLog,
    Base,
    HandoffPackage,
    ReviewFinding as FindingRecord,
    Thread,
    Workspace,
)
from dualcode.review_findings import persist_review_findings


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'findings.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_review_findings_maps_fields_and_audits_counts(session) -> None:
    workspace = Workspace(name="Project", path="/project")
    session.add(workspace)
    await session.flush()
    thread = Thread(workspace_id=workspace.id, title="Task")
    session.add(thread)
    await session.flush()
    handoff = HandoffPackage(
        workspace_id=workspace.id,
        thread_id=thread.id,
        recipient="claude",
        purpose="review",
    )
    session.add(handoff)
    await session.flush()
    secret_description = "Blocking description that must not enter audit"
    review = ReviewV1.model_validate(
        {
            "schema": "review.v1",
            "verdict": "blocking",
            "summary": "Changes required",
            "findings": [
                {
                    "id": "finding-1",
                    "type": "regression",
                    "severity": "blocking",
                    "file": "app.py",
                    "line": "12-14",
                    "description": secret_description,
                    "acceptance": "Regression test passes",
                },
                {
                    "id": "finding-2",
                    "type": "risk",
                    "severity": "advisory",
                    "file": None,
                    "line": None,
                    "description": "Consider a timeout",
                    "acceptance": "Risk documented",
                },
            ],
        }
    )

    records = await persist_review_findings(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        source_handoff_id=handoff.id,
        review=review,
        round=2,
    )
    await session.flush()

    assert len(records) == 2
    first = records[0]
    assert first.id != "finding-1"
    assert {
        "collaboration_run_id": first.collaboration_run_id,
        "round": first.round,
        "type": first.type,
        "severity": first.severity,
        "status": first.status,
        "file": first.file,
        "line": first.line,
        "description": first.description,
        "acceptance": first.acceptance,
        "source_handoff_id": first.source_handoff_id,
        "resolved_by_snapshot_sha": first.resolved_by_snapshot_sha,
    } == {
        "collaboration_run_id": None,
        "round": 2,
        "type": "regression",
        "severity": "blocking",
        "status": "open",
        "file": "app.py",
        "line": "12-14",
        "description": secret_description,
        "acceptance": "Regression test passes",
        "source_handoff_id": handoff.id,
        "resolved_by_snapshot_sha": None,
    }
    audit = await session.scalar(
        select(AuditLog).where(AuditLog.event == EVENT_REVIEW_VERDICT)
    )
    assert audit is not None
    detail = ReviewVerdictDetail.model_validate_json(audit.detail)
    assert detail == ReviewVerdictDetail(
        handoff_id=handoff.id,
        verdict="blocking",
        blocking_count=1,
        advisory_count=1,
    )
    assert secret_description not in audit.detail
    assert "Consider a timeout" not in audit.detail
    assert set(json.loads(audit.detail)) == {
        "handoff_id",
        "verdict",
        "blocking_count",
        "advisory_count",
    }


@pytest.mark.asyncio
async def test_reused_reviewer_finding_id_does_not_collide_across_handoffs(
    session,
) -> None:
    workspace = Workspace(name="Project", path="/project")
    session.add(workspace)
    await session.flush()
    thread = Thread(workspace_id=workspace.id, title="Task")
    session.add(thread)
    await session.flush()
    handoffs = [
        HandoffPackage(
            workspace_id=workspace.id,
            thread_id=thread.id,
            recipient="claude",
            purpose="review",
        )
        for _ in range(2)
    ]
    session.add_all(handoffs)
    await session.flush()

    def review(description: str) -> ReviewV1:
        return ReviewV1.model_validate(
            {
                "schema": "review.v1",
                "verdict": "blocking",
                "summary": description,
                "findings": [
                    {
                        "id": "F-1",
                        "type": "regression",
                        "severity": "blocking",
                        "file": "app.py",
                        "line": "10",
                        "description": description,
                        "acceptance": f"Resolve {description}",
                    }
                ],
            }
        )

    first = await persist_review_findings(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        source_handoff_id=handoffs[0].id,
        review=review("first round"),
        round=1,
    )
    second = await persist_review_findings(
        session,
        workspace_id=workspace.id,
        thread_id=thread.id,
        source_handoff_id=handoffs[1].id,
        review=review("second round"),
        round=2,
    )
    await session.flush()

    records = (
        await session.scalars(select(FindingRecord).order_by(FindingRecord.round))
    ).all()
    assert len(records) == 2
    assert first[0].id != second[0].id
    assert {record.id for record in records} == {first[0].id, second[0].id}
    assert [record.description for record in records] == [
        "first round",
        "second round",
    ]
    assert [record.source_handoff_id for record in records] == [
        handoffs[0].id,
        handoffs[1].id,
    ]
