import json

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from .models import Approval, AuditLog, ExecutionJob


ACTIVE_JOB_STATES = ("READY", "RUNNING")
RETRYABLE_JOB_STATES = ("FAILED", "INTERRUPTED")


def decode_json_object(value: str | None) -> dict[str, object]:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


async def migrate_execution_jobs(engine: AsyncEngine) -> None:
    """Apply small, repeatable SQLite migrations for pre-Alembic installs."""
    async with engine.begin() as connection:
        # create_all is still authoritative for new installs. This hook exists
        # so future additive columns can be introduced without replacing DBs.
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_execution_jobs_status ON execution_jobs (status)"))
        columns = (await connection.execute(text("PRAGMA table_info(execution_jobs)"))).mappings().all()
        if "evidence" not in {str(column["name"]) for column in columns}:
            await connection.execute(text("ALTER TABLE execution_jobs ADD COLUMN evidence TEXT NOT NULL DEFAULT '{}'"))


async def record_job_evidence(db: AsyncSession, approval_id: str, phase: str,
                              value: dict[str, object]) -> None:
    """Durably append evidence before a side-effect status transition."""
    job = await db.scalar(select(ExecutionJob).where(ExecutionJob.approval_id == approval_id))
    if not job:
        return
    evidence = decode_json_object(job.evidence)
    evidence[phase] = value
    job.evidence = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))


async def create_job(
    db: AsyncSession, *, approval: Approval, workspace_id: str, kind: str,
    payload: dict[str, object], idempotency_key: str | None = None,
    initial_status: str = "WAITING_APPROVAL",
) -> ExecutionJob:
    if initial_status not in {"WAITING_APPROVAL", "READY"}:
        raise ValueError(f"Unsupported initial execution job status: {initial_status}")
    if initial_status == "READY" and approval.status != "APPROVED":
        raise ValueError("A READY execution job requires an approved authorization")
    job = ExecutionJob(
        approval_id=approval.id,
        workspace_id=workspace_id,
        thread_id=approval.thread_id,
        kind=kind,
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        idempotency_key=idempotency_key or f"approval:{approval.id}",
        status=initial_status,
    )
    db.add(job)
    return job


async def decide_job(db: AsyncSession, approval_id: str, approved: bool) -> ExecutionJob | None:
    job = await db.scalar(select(ExecutionJob).where(ExecutionJob.approval_id == approval_id))
    if job and job.status == "WAITING_APPROVAL":
        job.status = "READY" if approved else "CANCELLED"
    return job


async def mark_job(db: AsyncSession, approval_id: str, status: str, error: str = "") -> None:
    job = await db.scalar(select(ExecutionJob).where(ExecutionJob.approval_id == approval_id))
    if not job or job.status in ("SUCCEEDED", "CANCELLED"):
        return
    if status == "RUNNING":
        job.attempts += 1
    job.status = status
    job.last_error = error


async def request_retry(db: AsyncSession, job: ExecutionJob) -> bool:
    """Move a failed approved job back to READY.

    Returning False for an already READY/RUNNING job makes repeated retry
    requests idempotent. Approval is deliberately re-checked from storage;
    retry never creates or bypasses an approval decision.
    """
    approval = await db.get(Approval, job.approval_id)
    if not approval or approval.status != "APPROVED":
        raise ValueError("Execution job does not have an approved authorization")
    if job.status == "READY":
        return True
    if job.status == "RUNNING":
        return False
    if job.status not in RETRYABLE_JOB_STATES:
        raise ValueError(f"Execution job in state {job.status} cannot be retried")
    result = await db.execute(
        update(ExecutionJob)
        .where(ExecutionJob.id == job.id, ExecutionJob.status.in_(RETRYABLE_JOB_STATES))
        .values(status="READY", last_error="")
    )
    if result.rowcount != 1:
        # A concurrent retry request already advanced this job. It is the same
        # accepted intent, so report an idempotent no-op rather than a conflict.
        await db.refresh(job)
        if job.status in ACTIVE_JOB_STATES:
            return False
        raise ValueError(f"Execution job in state {job.status} cannot be retried")
    db.add(AuditLog(workspace_id=job.workspace_id, thread_id=job.thread_id,
                    event="execution.retry.requested", detail=f"job={job.id};kind={job.kind};attempt={job.attempts + 1}"))
    await db.refresh(job)
    return True


async def claim_job(db: AsyncSession, job_id: str) -> ExecutionJob | None:
    """Atomically claim one READY job so duplicate schedulers cannot replay it."""
    result = await db.execute(
        update(ExecutionJob)
        .where(ExecutionJob.id == job_id, ExecutionJob.status == "READY")
        .values(status="RUNNING", attempts=ExecutionJob.attempts + 1)
    )
    if result.rowcount != 1:
        return None
    await db.commit()
    return await db.get(ExecutionJob, job_id)


async def recover_execution_jobs(db: AsyncSession) -> dict[str, int]:
    """Reconcile jobs after an unclean shutdown without replaying side effects.

    READY remains retryable. RUNNING becomes INTERRUPTED: automatically
    replaying commit/push/test may duplicate an already completed side effect.
    The state is explicit and auditable instead of silently losing the task.
    """
    waiting = (await db.scalars(select(ExecutionJob).where(ExecutionJob.status == "WAITING_APPROVAL"))).all()
    interrupted = (await db.scalars(select(ExecutionJob).where(ExecutionJob.status == "RUNNING"))).all()
    ready = (await db.scalars(select(ExecutionJob).where(ExecutionJob.status == "READY"))).all()
    for job in waiting:
        approval = await db.get(Approval, job.approval_id)
        if approval and approval.action == "remote_git_provision" and approval.status == "PENDING":
            # The explicit clone button is now the authorization. Cancel a
            # redundant approval left by an older build so it cannot block UI.
            approval.status = "REJECTED"
            job.status = "CANCELLED"
            db.add(AuditLog(
                workspace_id=job.workspace_id,
                thread_id=job.thread_id,
                event="approval.superseded",
                detail=f"approval={approval.id};action=remote_git_provision;reason=interaction_migration",
            ))
        elif approval and approval.status == "APPROVED":
            job.status = "READY"
            ready.append(job)
        elif approval and approval.status == "REJECTED":
            job.status = "CANCELLED"
    reconciled = 0
    unknown = 0
    for job in interrupted:
        evidence = decode_json_object(job.evidence)
        after = evidence.get("after")
        # `verified` is only persisted after the command returned successfully
        # and observable repository state was captured. Absence is UNKNOWN,
        # never proof that the side effect did not occur.
        if isinstance(after, dict) and after.get("verified") is True:
            job.status = "SUCCEEDED"
            job.last_error = ""
            event = "execution.reconciled"
            reconciled += 1
        else:
            job.status = "INTERRUPTED"
            job.last_error = "Operation outcome is unknown after backend interruption; inspect evidence before retry"
            event = "execution.interrupted"
            unknown += 1
        db.add(AuditLog(workspace_id=job.workspace_id, thread_id=job.thread_id,
                        event=event, detail=f"job={job.id};kind={job.kind}"))
    await db.commit()
    return {"waiting": len(waiting), "ready": len({item.id for item in ready}),
            "interrupted": unknown, "reconciled": reconciled}
