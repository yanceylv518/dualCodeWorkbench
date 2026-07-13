from collections.abc import AsyncIterable

from .models import Message


CONVERSATION_CHAR_BUDGET = 60_000
CONTRACT_CHAR_BUDGET = 20_000
CONVERSATION_TRUNCATION_MARKER = "【较早对话已截断】"
CONTRACT_TRUNCATION_MARKER = "【项目与任务契约已截断】"


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
