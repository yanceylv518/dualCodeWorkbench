import json

from .adapters import AgentStreamEvent, AgentStreamEventType


class ClaudeStreamParser:
    """Translate Claude CLI stream-json envelopes into workbench events."""

    def __init__(self) -> None:
        self.session_id = ""
        self.emitted_text = False

    def feed(self, chunk: str) -> list[AgentStreamEvent]:
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            if not chunk:
                return []
            return [
                AgentStreamEvent(
                    AgentStreamEventType.TERMINAL,
                    session_id=self.session_id,
                    text=chunk,
                )
            ]

        if not isinstance(payload, dict):
            return []
        self.session_id = str(payload.get("session_id") or self.session_id)
        event_type = payload.get("type")
        events: list[AgentStreamEvent] = []

        if event_type in {"system", "rate_limit_event"}:
            return events

        if event_type in {"assistant", "user"}:
            message = payload.get("message")
            blocks = message.get("content", []) if isinstance(message, dict) else []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if event_type == "assistant" and block_type == "text":
                    text = str(block.get("text") or "")
                    if text:
                        self.emitted_text = True
                        events.append(
                            AgentStreamEvent(
                                AgentStreamEventType.DELTA,
                                session_id=self.session_id,
                                text=text,
                            )
                        )
                elif block_type in {"tool_use", "tool_result"}:
                    events.append(
                        AgentStreamEvent(
                            AgentStreamEventType.TOOL_EVENT,
                            session_id=self.session_id,
                            event=str(block_type),
                            item={str(key): value for key, value in block.items()},
                        )
                    )
            return events

        if event_type == "result":
            result = str(payload.get("result") or "")
            if result and not self.emitted_text:
                self.emitted_text = True
                events.append(
                    AgentStreamEvent(
                        AgentStreamEventType.DELTA,
                        session_id=self.session_id,
                        text=result,
                    )
                )
            events.append(
                AgentStreamEvent(AgentStreamEventType.FINAL, session_id=self.session_id)
            )
            return events

        events.append(
            AgentStreamEvent(
                AgentStreamEventType.TERMINAL,
                session_id=self.session_id,
                text=chunk,
            )
        )
        return events
