from datetime import UTC, datetime

import pytest

from dualcode.config import Settings, settings
from dualcode.context_budget import MEMORY_TRUNCATION_MARKER, build_memory_section
from dualcode.models import MemoryFact, Thread, Workspace
from dualcode.scheduler import RunScheduler


def _fact(
    *,
    fact_id: str,
    kind: str,
    content: str,
    source: str,
    confidence: str,
) -> MemoryFact:
    return MemoryFact(
        id=fact_id,
        workspace_id="workspace-1",
        thread_id="thread-1",
        kind=kind,
        content_json=f'{{"content":"{content}"}}',
        source=source,
        confidence=confidence,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_smart_collaboration_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SMART_COLLABORATION_ENABLED", raising=False)
    assert Settings().smart_collaboration_enabled is False


def test_smart_collaboration_can_be_enabled_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("SMART_COLLABORATION_ENABLED", "true")
    assert Settings().smart_collaboration_enabled is True


@pytest.mark.asyncio
async def test_disabled_memory_does_not_query_database(monkeypatch) -> None:
    class NoDatabaseAccess:
        def __getattr__(self, name):
            raise AssertionError(f"database access attempted: {name}")

    monkeypatch.setattr(settings, "smart_collaboration_enabled", False)
    scheduler = RunScheduler.__new__(RunScheduler)
    result = await scheduler._shared_memory_prompt(
        NoDatabaseAccess(),
        Workspace(id="workspace-1", name="Project", path="."),
        Thread(id="thread-1", workspace_id="workspace-1", title="Task"),
    )
    assert result == ""


@pytest.mark.asyncio
async def test_enabled_shared_prompt_contains_goal_commit_and_open_risk(
    monkeypatch,
) -> None:
    facts = [
        _fact(
            fact_id="goal",
            kind="requirement",
            content="confirmed goal",
            source="user",
            confidence="confirmed",
        ),
        _fact(
            fact_id="commit",
            kind="repository",
            content="Current commit: abc123",
            source="git",
            confidence="verified",
        ),
        _fact(
            fact_id="risk",
            kind="risk",
            content="open migration risk",
            source="user",
            confidence="confirmed",
        ),
    ]

    class ScalarResult:
        def all(self):
            return facts

    class FakeDatabase:
        async def scalars(self, _statement):
            return ScalarResult()

    async def snapshot(*_args):
        return []

    monkeypatch.setattr(settings, "smart_collaboration_enabled", True)
    monkeypatch.setattr("dualcode.scheduler.snapshot_thread_facts", snapshot)
    scheduler = RunScheduler.__new__(RunScheduler)
    result = await scheduler._shared_memory_prompt(
        FakeDatabase(),
        Workspace(id="workspace-1", name="Project", path="."),
        Thread(id="thread-1", workspace_id="workspace-1", title="Task"),
    )

    assert result.startswith("SHARED MEMORY:\n")
    assert "confirmed goal" in result
    assert "Current commit: abc123" in result
    assert "open migration risk" in result


def test_memory_section_orders_goal_repository_risk_and_evidence() -> None:
    facts = [
        _fact(
            fact_id="e",
            kind="evidence",
            content="tests pass",
            source="test",
            confidence="verified",
        ),
        _fact(
            fact_id="r",
            kind="risk",
            content="open migration risk",
            source="user",
            confidence="confirmed",
        ),
        _fact(
            fact_id="g",
            kind="requirement",
            content="confirmed goal",
            source="user",
            confidence="confirmed",
        ),
        _fact(
            fact_id="c",
            kind="repository",
            content="Current commit: abc123",
            source="git",
            confidence="verified",
        ),
    ]

    result = build_memory_section(facts)

    assert result.index("confirmed goal") < result.index("Current commit: abc123")
    assert result.index("Current commit: abc123") < result.index("open migration risk")
    assert result.index("open migration risk") < result.index("tests pass")


def test_budget_drops_low_confidence_first_and_preserves_confirmed() -> None:
    confirmed = _fact(
        fact_id="confirmed",
        kind="requirement",
        content="confirmed goal must remain",
        source="user",
        confidence="confirmed",
    )
    stale = _fact(
        fact_id="stale",
        kind="repository",
        content="obsolete repository fact",
        source="git",
        confidence="stale",
    )
    unverified = _fact(
        fact_id="guess",
        kind="assumption",
        content="agent guess",
        source="codex",
        confidence="unverified",
    )

    result = build_memory_section([stale, confirmed, unverified], budget=50)

    assert "confirmed goal must remain" in result
    assert "obsolete repository fact" not in result
    assert "agent guess" not in result
    assert result.endswith(MEMORY_TRUNCATION_MARKER)


def test_confirmed_fact_is_never_truncated_even_when_it_exceeds_budget() -> None:
    content = "confirmed " + "x" * 200
    result = build_memory_section(
        [
            _fact(
                fact_id="confirmed",
                kind="requirement",
                content=content,
                source="user",
                confidence="confirmed",
            )
        ],
        budget=20,
    )

    assert content in result
    assert result.endswith(MEMORY_TRUNCATION_MARKER)
