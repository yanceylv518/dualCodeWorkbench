import pytest
from pydantic import TypeAdapter, ValidationError

from dualcode.collaboration_protocol import (
    FACT_CONFIDENCE_RANK,
    MEMORY_FACT_CONTENT_MAX_LENGTH,
    FactConfidence,
    FactKind,
    FactSource,
    MemoryFactContent,
)


@pytest.mark.parametrize(
    ("fact_type", "valid", "invalid"),
    [
        (FactKind, "requirement", "feature"),
        (FactSource, "claude", "assistant"),
        (FactConfidence, "verified", "trusted"),
    ],
)
def test_fact_enums_accept_only_frozen_values(fact_type, valid: str, invalid: str) -> None:
    adapter = TypeAdapter(fact_type)

    assert adapter.validate_python(valid) == valid
    with pytest.raises(ValidationError):
        adapter.validate_python(invalid)


def test_memory_fact_content_is_single_line_and_limited_to_500_characters() -> None:
    item = MemoryFactContent(content=f"{'x' * 250}\n{'y' * 300}")

    assert len(item.content) == MEMORY_FACT_CONTENT_MAX_LENGTH
    assert item.content.endswith("…")
    assert "\n" not in item.content


def test_short_memory_fact_content_is_preserved() -> None:
    assert MemoryFactContent(content="Acceptance criterion").content == "Acceptance criterion"


def test_memory_fact_content_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MemoryFactContent.model_validate({"content": "goal", "prompt": "not allowed"})


def test_confidence_rank_is_strictly_descending() -> None:
    assert [
        FACT_CONFIDENCE_RANK[level]
        for level in ("confirmed", "verified", "unverified", "stale")
    ] == [3, 2, 1, 0]
