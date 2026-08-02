import json

import pytest
from pydantic import ValidationError

from dualcode.review_parser import ReviewParseResult, parse_review


def _review(summary: str = "ready", verdict: str = "pass") -> dict:
    return {
        "schema": "review.v1",
        "verdict": verdict,
        "summary": summary,
        "findings": [],
    }


def test_parses_json_fence_and_preserves_raw_text_byte_for_byte() -> None:
    raw = f"Review follows.\r\n```json\r\n{json.dumps(_review())}\r\n```\r\nEnd."
    result = parse_review(raw)

    assert result.outcome == "parsed"
    assert result.review is not None
    assert result.review.summary == "ready"
    assert result.raw_text == raw
    assert result.error is None


def test_parses_bare_json_object() -> None:
    raw = f"prefix {json.dumps(_review('bare'))} suffix"
    result = parse_review(raw)

    assert result.outcome == "parsed"
    assert result.review is not None
    assert result.review.summary == "bare"


def test_missing_required_field_is_schema_mismatch() -> None:
    payload = _review()
    payload.pop("verdict")
    result = parse_review(json.dumps(payload))

    assert result.outcome == "schema_mismatch"
    assert result.review is None
    assert result.error


def test_numeric_finding_line_is_normalized() -> None:
    raw = json.dumps(
        {
            "schema": "review.v1",
            "verdict": "blocking",
            "summary": "发现问题",
            "findings": [
                {
                    "id": "F-1",
                    "type": "risk",
                    "severity": "advisory",
                    "file": "docs/plan.md",
                    "line": 3,
                    "description": "描述",
                    "acceptance": "验收",
                }
            ],
        },
        ensure_ascii=False,
    )

    result = parse_review(raw)

    assert result.outcome == "parsed"
    assert result.review is not None
    assert result.review.findings[0].line == "3"


def test_invalid_json_is_reported_without_guessing() -> None:
    raw = '```json\n{"schema":"review.v1","verdict": pass}\n```'
    result = parse_review(raw)

    assert result.outcome == "invalid_json"
    assert result.review is None
    assert result.raw_text == raw


def test_text_without_json_is_no_json() -> None:
    raw = "Looks good, but this is not a machine-readable verdict."
    result = parse_review(raw)

    assert result.outcome == "no_json"
    assert result.review is None
    assert result.raw_text == raw


def test_uses_last_candidate_that_validates() -> None:
    first = json.dumps(_review("first"))
    invalid = '{"schema":"review.v1","verdict":"pass"}'
    last = json.dumps(_review("last", "blocking"))
    result = parse_review(f"{first}\n{invalid}\n{last}")

    assert result.outcome == "parsed"
    assert result.review is not None
    assert result.review.summary == "last"
    assert result.review.verdict == "blocking"


def test_json_fences_take_priority_over_bare_objects() -> None:
    bare = json.dumps(_review("outside"))
    fenced = json.dumps(_review("inside"))
    result = parse_review(f"{bare}\n```json\n{fenced}\n```")

    assert result.review is not None
    assert result.review.summary == "inside"


def test_all_failed_candidates_use_last_failure_category() -> None:
    schema_mismatch = '{"schema":"review.v1","summary":"missing fields"}'
    invalid = '{"schema":"review.v1",'
    result = parse_review(f"```json\n{schema_mismatch}\n```\n```json\n{invalid}\n```")

    assert result.outcome == "invalid_json"


def test_error_is_single_line_and_at_most_200_characters() -> None:
    payload = {"schema": "review.v1", "unknown": "x" * 500}
    result = parse_review(json.dumps(payload))

    assert result.error is not None
    assert "\n" not in result.error
    assert len(result.error) <= 200


@pytest.mark.parametrize(
    "payload",
    [
        {
            "outcome": "parsed",
            "review": None,
            "raw_text": "{}",
        },
        {
            "outcome": "no_json",
            "review": _review(),
            "raw_text": "none",
        },
    ],
)
def test_result_rejects_inconsistent_outcome_shape(payload) -> None:
    with pytest.raises(ValidationError):
        ReviewParseResult.model_validate(payload)
