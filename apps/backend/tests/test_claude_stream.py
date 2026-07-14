import json

from dualcode.adapters import AgentStreamEventType
from dualcode.claude_stream import ClaudeStreamParser


def test_system_init_is_metadata_not_chat_content() -> None:
    parser = ClaudeStreamParser()
    events = parser.feed(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "session-1",
                "tools": ["Read", "Bash"],
            }
        )
    )
    assert events == []
    assert parser.session_id == "session-1"


def test_assistant_text_and_tools_are_normalized() -> None:
    parser = ClaudeStreamParser()
    events = parser.feed(
        json.dumps(
            {
                "type": "assistant",
                "session_id": "session-2",
                "message": {
                    "content": [
                        {"type": "text", "text": "你好"},
                        {"type": "tool_use", "name": "Read", "input": {"file": "a.py"}},
                    ]
                },
            },
            ensure_ascii=False,
        )
    )
    assert [event.type for event in events] == [
        AgentStreamEventType.DELTA,
        AgentStreamEventType.TOOL_EVENT,
    ]
    assert events[0].text == "你好"
    assert events[1].event == "tool_use"


def test_result_does_not_duplicate_streamed_assistant_text() -> None:
    parser = ClaudeStreamParser()
    parser.feed(
        json.dumps(
            {
                "type": "assistant",
                "session_id": "session-3",
                "message": {"content": [{"type": "text", "text": "正文"}]},
            },
            ensure_ascii=False,
        )
    )
    events = parser.feed(
        json.dumps(
            {"type": "result", "session_id": "session-3", "result": "正文"},
            ensure_ascii=False,
        )
    )
    assert [event.type for event in events] == [AgentStreamEventType.FINAL]


def test_result_is_fallback_when_no_assistant_text_was_streamed() -> None:
    events = ClaudeStreamParser().feed(
        json.dumps(
            {"type": "result", "session_id": "session-4", "result": "最终答案"},
            ensure_ascii=False,
        )
    )
    assert [event.type for event in events] == [
        AgentStreamEventType.DELTA,
        AgentStreamEventType.FINAL,
    ]
    assert events[0].text == "最终答案"


def test_non_json_output_goes_to_terminal_instead_of_chat() -> None:
    events = ClaudeStreamParser().feed("diagnostic output")
    assert len(events) == 1
    assert events[0].type == AgentStreamEventType.TERMINAL
    assert events[0].text == "diagnostic output"
