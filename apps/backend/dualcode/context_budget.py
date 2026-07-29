from collections.abc import AsyncIterable

from .collaboration_protocol import FACT_CONFIDENCE_RANK, MemoryFactContent
from .models import MemoryFact, Message


CONVERSATION_CHAR_BUDGET = 60_000
CONTRACT_CHAR_BUDGET = 20_000
MEMORY_CHAR_BUDGET = 8_000
CONVERSATION_TRUNCATION_MARKER = "【较早对话已截断】"
CONTRACT_TRUNCATION_MARKER = "【项目与任务契约已截断】"
MEMORY_TRUNCATION_MARKER = "【共享记忆已截断】"

_MEMORY_KIND_ORDER = {
    "requirement": 0,
    "decision": 1,
    "repository": 2,
    "risk": 3,
    "evidence": 4,
    "assumption": 5,
}


async def build_recent_transcript(
    newest_first: AsyncIterable[Message],
    budget: int = CONVERSATION_CHAR_BUDGET,
) -> str:
    if budget <= 0:
        return ""
    selected: list[str] = []
    used = 0
    truncated = False
    async for message in newest_first:
        line = f"{message.role}: {message.content}"
        separator = 1 if selected else 0
        if used + separator + len(line) > budget:
            truncated = True
            break
        selected.append(line)
        used += separator + len(line)
    transcript = "\n".join(reversed(selected))
    if not truncated:
        return transcript
    marker = CONVERSATION_TRUNCATION_MARKER[:budget]
    if not transcript:
        return marker
    available = budget - len(marker) - 1
    if available <= 0:
        return marker
    return f"{marker}\n{transcript[-available:]}"


def truncate_contract(value: str, budget: int = CONTRACT_CHAR_BUDGET) -> str:
    if len(value) <= budget:
        return value
    if budget <= 0:
        return ""
    marker = CONTRACT_TRUNCATION_MARKER[:budget]
    available = budget - len(marker) - 1
    if available <= 0:
        return marker
    return f"{value[:available]}\n{marker}"


def build_memory_section(
    facts: list[MemoryFact],
    budget: int = MEMORY_CHAR_BUDGET,
) -> str:
    """Render active facts predictably, discarding low-confidence facts first."""

    if budget <= 0 and not any(fact.confidence == "confirmed" for fact in facts):
        return ""

    def line_for(fact: MemoryFact) -> str:
        content = MemoryFactContent.model_validate_json(fact.content_json).content
        return (
            f"- [{fact.kind}/{fact.confidence}/{fact.source}] {content}"
        )

    ordered = sorted(
        facts,
        key=lambda fact: (
            _MEMORY_KIND_ORDER.get(fact.kind, 99),
            -FACT_CONFIDENCE_RANK[fact.confidence],
            fact.created_at,
            fact.id,
        ),
    )
    selected = [(fact, line_for(fact)) for fact in ordered]
    truncated = False
    while selected and len("\n".join(line for _, line in selected)) > budget:
        removable = [
            (index, FACT_CONFIDENCE_RANK[fact.confidence])
            for index, (fact, _) in enumerate(selected)
            if fact.confidence != "confirmed"
        ]
        if not removable:
            truncated = True
            break
        remove_index = min(removable, key=lambda item: (item[1], -item[0]))[0]
        selected.pop(remove_index)
        truncated = True

    body = "\n".join(line for _, line in selected)
    if not truncated:
        return body
    if not body:
        return MEMORY_TRUNCATION_MARKER[:budget]
    return f"{body}\n{MEMORY_TRUNCATION_MARKER}"
