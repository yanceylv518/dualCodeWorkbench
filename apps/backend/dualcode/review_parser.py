"""Deterministic extraction and validation of review.v1 responses."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import model_validator

from .collaboration_protocol import ReviewV1, StrictModel
from .evidence import summarize_single_line

_JSON_FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.IGNORECASE | re.DOTALL)


class ReviewParseResult(StrictModel):
    outcome: Literal["parsed", "no_json", "invalid_json", "schema_mismatch"]
    review: ReviewV1 | None
    raw_text: str
    error: str | None = None

    @model_validator(mode="after")
    def enforce_outcome_shape(self) -> ReviewParseResult:
        if (self.outcome == "parsed") != (self.review is not None):
            raise ValueError("review must be present only for parsed outcomes")
        if self.error is not None:
            self.error = summarize_single_line(self.error)
        return self


def _bare_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidates.append(text[start : index + 1])
                start = None
    if start is not None:
        candidates.append(text[start:])
    return candidates


def parse_review(text: str) -> ReviewParseResult:
    fenced = [match.group(1).strip() for match in _JSON_FENCE.finditer(text)]
    candidates = fenced if fenced else _bare_json_candidates(text)
    if not candidates:
        return ReviewParseResult(
            outcome="no_json",
            review=None,
            raw_text=text,
            error="No JSON object found",
        )

    valid: ReviewV1 | None = None
    last_outcome: Literal["invalid_json", "schema_mismatch"] = "invalid_json"
    last_error = ""
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError) as exc:
            last_outcome = "invalid_json"
            last_error = str(exc)
            continue
        try:
            review = ReviewV1.model_validate(payload)
        except ValueError as exc:
            last_outcome = "schema_mismatch"
            last_error = str(exc)
            continue
        valid = review

    if valid is not None:
        return ReviewParseResult(
            outcome="parsed",
            review=valid,
            raw_text=text,
        )
    return ReviewParseResult(
        outcome=last_outcome,
        review=None,
        raw_text=text,
        error=last_error,
    )
