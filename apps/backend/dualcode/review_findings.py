"""Persist validated review findings and their compact verdict audit."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .collaboration_audit import ReviewVerdictDetail, build_review_verdict_audit
from .collaboration_protocol import ReviewV1
from .models import ReviewFinding as ReviewFindingRecord


async def persist_review_findings(
    db: AsyncSession,
    *,
    workspace_id: str,
    thread_id: str,
    source_handoff_id: str,
    review: ReviewV1,
    collaboration_run_id: str | None = None,
    round: int = 1,
) -> list[ReviewFindingRecord]:
    records = [
        ReviewFindingRecord(
            collaboration_run_id=collaboration_run_id,
            round=round,
            type=finding.type,
            severity=finding.severity,
            status="open",
            file=finding.file,
            line=finding.line,
            description=finding.description,
            acceptance=finding.acceptance,
            source_handoff_id=source_handoff_id,
        )
        for finding in review.findings
    ]
    db.add_all(records)
    db.add(
        build_review_verdict_audit(
            workspace_id,
            thread_id,
            ReviewVerdictDetail(
                handoff_id=source_handoff_id,
                verdict=review.verdict,
                blocking_count=sum(
                    finding.severity == "blocking" for finding in review.findings
                ),
                advisory_count=sum(
                    finding.severity == "advisory" for finding in review.findings
                ),
            ),
        )
    )
    await db.flush()
    return records
