import pytest

from dualcode.context_budget import (
    CONTRACT_TRUNCATION_MARKER,
    CONVERSATION_TRUNCATION_MARKER,
    build_recent_transcript,
    truncate_contract,
)
from dualcode.models import Message


async def messages(*items: Message):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_transcript_keeps_newest_messages_with_explicit_truncation_marker():
    newest = Message(role="assistant", content="newest")
    middle = Message(role="user", content="middle")
    oldest = Message(role="assistant", content="oldest content is omitted")

    transcript = await build_recent_transcript(messages(newest, middle, oldest), budget=50)

    assert transcript.startswith(CONVERSATION_TRUNCATION_MARKER)
    assert "user: middle" in transcript
    assert transcript.endswith("assistant: newest")
    assert "oldest content" not in transcript
    assert len(transcript) <= 50


@pytest.mark.asyncio
async def test_transcript_without_overflow_has_no_marker_and_is_chronological():
    transcript = await build_recent_transcript(
        messages(Message(role="assistant", content="two"), Message(role="user", content="one")),
        budget=100,
    )

    assert transcript == "user: one\nassistant: two"


def test_contract_is_bounded_and_explicitly_marked():
    contract = truncate_contract("x" * 100, budget=40)

    assert contract.endswith(CONTRACT_TRUNCATION_MARKER)
    assert len(contract) <= 40
