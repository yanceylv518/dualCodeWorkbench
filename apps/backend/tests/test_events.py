from dualcode.events import AgentEvent, EventType


def test_event_serializes_with_sequence():
    event = AgentEvent(
        type=EventType.RUN_OUTPUT, thread_id="thread-1", sequence=2, payload={"chunk": "ok"}
    )
    assert event.model_dump(mode="json")["sequence"] == 2
    assert event.payload["chunk"] == "ok"
