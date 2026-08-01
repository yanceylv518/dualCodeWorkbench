import asyncio
import json

import pytest

from dualcode.adapters import AgentRequest, AgentStreamEventType
from dualcode.codex_app_server import (
    AppServerNoProgressError,
    AppServerProtocolError,
    CodexAppServerAdapter,
)


class FakeStdin:
    def __init__(self, process):
        self.process = process
        self.writes = []

    def write(self, data):
        message = json.loads(data)
        self.writes.append(message)
        self.process.reply(message)

    async def drain(self):
        pass


class FakeOutput:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def readline(self):
        return await self.queue.get()


class FakeProcess:
    def __init__(self, request_approval=False):
        self.stdout = FakeOutput()
        self.stderr = FakeOutput()
        self.stdin = FakeStdin(self)
        self.returncode = None
        self.request_approval = request_approval
        self.thread_read_result = {}

    def emit(self, value):
        data = (json.dumps(value) + "\n").encode()
        if hasattr(self.stdout, "feed_data"):
            self.stdout.feed_data(data)
        else:
            self.stdout.queue.put_nowait(data)

    def reply(self, request):
        if "id" not in request:
            return
        if "method" not in request:
            if request["id"] == 900:
                self.emit_turn()
            return
        method = request["method"]
        result = {}
        if method == "thread/start":
            result = {"thread": {"id": "thread-app-1"}}
        elif method == "thread/resume":
            result = {"thread": {"id": request["params"]["threadId"]}}
        elif method == "thread/read":
            result = self.thread_read_result
        elif method == "turn/start":
            result = {"turn": {"id": "turn-1"}}
        self.emit({"id": request["id"], "result": result})
        if method == "turn/start":
            if self.request_approval:
                self.emit({"id": 900, "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread-app-1", "turnId": "turn-1", "itemId": "cmd-1", "command": "pytest -q", "startedAtMs": 1}})
            else:
                self.emit_turn()

    def emit_turn(self):
        self.emit({"method": "item/reasoning/summaryTextDelta", "params": {"threadId": "thread-app-1", "turnId": "turn-1", "itemId": "reason-1", "delta": "Inspecting"}})
        self.emit({"method": "item/agentMessage/delta", "params": {"threadId": "thread-app-1", "turnId": "turn-1", "delta": "hello "}})
        self.emit({"method": "item/agentMessage/delta", "params": {"threadId": "thread-app-1", "turnId": "turn-1", "delta": "world"}})
        self.emit({"method": "turn/completed", "params": {"threadId": "thread-app-1", "turn": {"id": "turn-1", "status": "completed"}}})

    async def wait(self):
        return 0

    def terminate(self):
        self.returncode = -15


class FailingOutput:
    async def readline(self):
        raise RuntimeError("reader exploded")


@pytest.mark.asyncio
async def test_app_server_streams_real_deltas_and_activity(monkeypatch, tmp_path):
    process = FakeProcess()
    adapter = CodexAppServerAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    response = await adapter.send(AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)}))

    assert response.run_id == "thread-app-1"
    assert response.content == "hello world"
    methods = [item.get("method") for item in process.stdin.writes]
    assert methods[:4] == ["initialize", "initialized", "thread/start", "turn/start"]
    await adapter.close()


@pytest.mark.asyncio
async def test_app_server_exposes_normalized_stream_events(monkeypatch, tmp_path):
    process = FakeProcess()
    adapter = CodexAppServerAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    events = [
        event
        async for event in adapter.stream_events(
            AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)})
        )
    ]

    assert [event.type for event in events] == [
        AgentStreamEventType.TOOL_EVENT,
        AgentStreamEventType.DELTA,
        AgentStreamEventType.DELTA,
        AgentStreamEventType.FINAL,
    ]
    assert all(event.session_id == "thread-app-1" for event in events)
    assert "".join(event.text for event in events) == "hello world"
    await adapter.close()


@pytest.mark.asyncio
async def test_app_server_accepts_json_lines_larger_than_asyncio_default(
    monkeypatch, tmp_path
):
    process = FakeProcess()
    process.stdout = asyncio.StreamReader(
        limit=CodexAppServerAdapter._MAX_PROTOCOL_LINE_BYTES
    )
    large_text = "x" * (70 * 1024)

    def emit_large_turn():
        process.emit({
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-app-1", "turnId": "turn-1",
                "delta": large_text,
            },
        })
        process.emit({
            "method": "turn/completed",
            "params": {
                "threadId": "thread-app-1",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        })

    process.emit_turn = emit_large_turn
    captured = {}

    async def create_process(*args, **kwargs):
        captured.update(kwargs)
        return process

    adapter = CodexAppServerAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    response = await adapter.send(
        AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)})
    )

    assert response.content == large_text
    assert captured["limit"] == 10 * 1024 * 1024
    await adapter.close()


@pytest.mark.asyncio
async def test_reader_failure_notifies_pending_requests_and_turns_without_hanging():
    process = FakeProcess()
    process.stdout = FailingOutput()
    adapter = CodexAppServerAdapter("fake")
    adapter._process = process
    pending = asyncio.get_running_loop().create_future()
    adapter._pending[7] = pending
    queue = asyncio.Queue()
    adapter._turn_queues["turn-1"] = queue

    await asyncio.wait_for(adapter._read_messages(), timeout=0.1)

    with pytest.raises(AppServerProtocolError, match="读取通道失败"):
        await pending
    event = await asyncio.wait_for(queue.get(), timeout=0.1)
    assert event["method"] == "transport/error"
    assert process.returncode == -15


@pytest.mark.asyncio
async def test_app_server_resumes_existing_thread(monkeypatch, tmp_path):
    process = FakeProcess()
    adapter = CodexAppServerAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    response = await adapter.send(AgentRequest("local-thread", "again", {"workspace_path": str(tmp_path), "session_id": "thread-app-1"}))

    assert response.run_id == "thread-app-1"
    assert not any(item.get("method") == "thread/start" for item in process.stdin.writes)
    assert any(item.get("method") == "thread/resume" for item in process.stdin.writes)
    await adapter.close()


@pytest.mark.asyncio
async def test_app_server_resets_transport_when_turn_stops_emitting_events(
    monkeypatch, tmp_path
):
    process = FakeProcess()
    process.emit_turn = lambda: None
    adapter = CodexAppServerAdapter(
        "fake", progress_timeout_seconds=0.01, probe_grace_seconds=0.01
    )
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        lambda *a, **k: asyncio.sleep(0, result=process),
    )

    with pytest.raises(AppServerNoProgressError, match="没有可见进展") as caught:
        await adapter.send(
            AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)})
        )

    assert caught.value.context["probe_succeeded"] is True
    assert caught.value.context["retry_safe"] is True
    assert process.returncode == -15
    assert any(item.get("method") == "turn/interrupt" for item in process.stdin.writes)
    await adapter.close()


def test_default_command_watchdog_does_not_wait_ten_minutes():
    assert CodexAppServerAdapter("fake").command_timeout_seconds == 180


def test_recovered_result_does_not_duplicate_already_streamed_text():
    assert CodexAppServerAdapter._recovered_suffix("hello world", "hello world") == ""
    assert CodexAppServerAdapter._recovered_suffix("hello world", "hello ") == "world"


@pytest.mark.asyncio
async def test_completed_turn_is_recovered_when_completion_event_is_lost(
    monkeypatch, tmp_path
):
    process = FakeProcess()

    def emit_partial_turn():
        process.emit({
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-app-1", "turnId": "turn-1",
                "delta": "hello ",
            },
        })

    process.emit_turn = emit_partial_turn
    process.thread_read_result = {"thread": {"turns": [{
        "id": "turn-1", "status": "completed",
        "items": [{
            "id": "message-1", "type": "agentMessage",
            "phase": "final_answer", "text": "hello world",
        }],
    }]}}
    adapter = CodexAppServerAdapter(
        "fake", progress_timeout_seconds=0.01, probe_grace_seconds=0.01
    )
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        lambda *a, **k: asyncio.sleep(0, result=process),
    )

    response = await adapter.send(
        AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)})
    )

    assert response.content == "hello world"
    assert not any(
        item.get("method") == "turn/interrupt" for item in process.stdin.writes
    )
    assert process.returncode is None
    await adapter.close()


@pytest.mark.asyncio
async def test_active_command_uses_extended_timeout(monkeypatch, tmp_path):
    process = FakeProcess()

    def emit_command_then_complete():
        process.emit({"method": "item/started", "params": {
            "threadId": "thread-app-1", "turnId": "turn-1",
            "item": {"id": "cmd-1", "type": "commandExecution", "command": "slow-test"},
        }})

        async def finish():
            await asyncio.sleep(0.04)
            process.emit({"method": "item/completed", "params": {
                "threadId": "thread-app-1", "turnId": "turn-1",
                "item": {"id": "cmd-1", "type": "commandExecution"},
            }})
            process.emit({"method": "turn/completed", "params": {
                "threadId": "thread-app-1",
                "turn": {"id": "turn-1", "status": "completed"},
            }})

        asyncio.create_task(finish())

    process.emit_turn = emit_command_then_complete
    adapter = CodexAppServerAdapter(
        "fake", progress_timeout_seconds=0.01,
        command_timeout_seconds=0.1, probe_grace_seconds=0.01,
    )
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        lambda *a, **k: asyncio.sleep(0, result=process),
    )

    events = [event async for event in adapter.stream_events(
        AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)})
    )]

    assert events[-1].type == AgentStreamEventType.FINAL
    assert not any(item.get("method") == "turn/interrupt" for item in process.stdin.writes)
    await adapter.close()


@pytest.mark.asyncio
async def test_stalled_active_command_is_interrupted_after_probe_grace(
    monkeypatch, tmp_path
):
    process = FakeProcess()

    def emit_stalled_command():
        process.emit(
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread-app-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "cmd-stalled",
                        "type": "commandExecution",
                        "command": "stalled-command",
                    },
                },
            }
        )

    process.emit_turn = emit_stalled_command
    adapter = CodexAppServerAdapter(
        "fake",
        progress_timeout_seconds=0.01,
        command_timeout_seconds=0.01,
        probe_grace_seconds=0.01,
    )
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        lambda *a, **k: asyncio.sleep(0, result=process),
    )

    with pytest.raises(AppServerNoProgressError) as caught:
        await adapter.send(
            AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)})
        )

    assert caught.value.context["active_items"] == {
        "cmd-stalled": "commandExecution"
    }
    assert any(
        item.get("method") == "turn/interrupt" for item in process.stdin.writes
    )
    await adapter.close()


@pytest.mark.asyncio
async def test_app_server_routes_native_command_approval(monkeypatch, tmp_path):
    process = FakeProcess(request_approval=True)
    adapter = CodexAppServerAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))
    requests = []

    async def approve(method, params):
        requests.append((method, params["command"]))
        return True

    response = await adapter.send(AgentRequest("local-thread", "hello", {
        "workspace_path": str(tmp_path), "approval_callback": approve,
    }))

    assert response.content == "hello world"
    assert requests == [("item/commandExecution/requestApproval", "pytest -q")]
    decision = next(item for item in process.stdin.writes if item.get("id") == 900 and "result" in item)
    assert decision["result"] == {"decision": "accept"}
    await adapter.close()


@pytest.mark.asyncio
async def test_full_access_mode_uses_never_and_danger_full_access(monkeypatch, tmp_path):
    process = FakeProcess()
    adapter = CodexAppServerAdapter("fake", permission_mode="full_access")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    await adapter.send(AgentRequest("local-thread", "hello", {"workspace_path": str(tmp_path)}))

    thread_start = next(item for item in process.stdin.writes if item.get("method") == "thread/start")
    turn_start = next(item for item in process.stdin.writes if item.get("method") == "turn/start")
    assert thread_start["params"]["approvalPolicy"] == "never"
    assert thread_start["params"]["sandbox"] == "danger-full-access"
    assert turn_start["params"]["approvalPolicy"] == "never"
    assert turn_start["params"]["sandboxPolicy"] == {"type": "dangerFullAccess"}
    await adapter.close()


def test_app_server_redacts_credentials_from_stderr():
    line = "request failed api_key=sk-super-secret-value token: bearer-value"

    safe = CodexAppServerAdapter._safe_stderr(line)

    assert "super-secret" not in safe
    assert "bearer-value" not in safe
    assert safe.count("[redacted]") == 2


def test_failed_turn_is_not_reported_as_success():
    adapter = CodexAppServerAdapter("fake")

    with pytest.raises(AppServerProtocolError, match="model unavailable"):
        adapter._normalize({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-1", "status": "failed", "error": {"message": "model unavailable"}}},
        }, "thread-1")


def test_reasoning_delta_matches_any_item_reasoning_method_variant():
    adapter = CodexAppServerAdapter("fake")

    for method in (
        "item/reasoning/summaryTextDelta",
        "item/reasoning/textDelta",
        "item/reasoning/delta",
        "item/reasoning/summaryDelta",
    ):
        event = adapter._normalize(
            {"method": method, "params": {"itemId": "r-1", "delta": "思考片段"}},
            "thread-1",
        )
        assert event == {
            "type": "activity.delta",
            "thread_id": "thread-1",
            "item": {"id": "r-1", "type": "reasoning", "text": "思考片段"},
        }


def test_reasoning_item_text_is_extracted_from_summary_and_content():
    adapter = CodexAppServerAdapter("fake")

    event = adapter._normalize(
        {
            "method": "item/updated",
            "params": {
                "item": {
                    "id": "r-2",
                    "type": "reasoning",
                    "summary": [{"text": "第一段"}, {"text": "第二段"}],
                }
            },
        },
        "thread-1",
    )
    assert event is not None
    assert event["item"]["text"] == "第一段\n第二段"

    event = adapter._normalize(
        {
            "method": "item/completed",
            "params": {
                "item": {"id": "r-3", "type": "reasoning", "content": "整段思考"}
            },
        },
        "thread-1",
    )
    assert event is not None
    assert event["item"]["text"] == "整段思考"


def test_unmapped_notification_methods_are_recorded_for_diagnostics():
    adapter = CodexAppServerAdapter("fake")

    assert adapter._normalize({"method": "turn/started", "params": {}}, "t") is None
    assert adapter._normalize({"method": "turn/started", "params": {}}, "t") is None
    assert (
        adapter._normalize(
            {"method": "item/commandExecution/requestApproval", "params": {}}, "t"
        )
        is None
    )

    assert adapter.unhandled_methods == {"turn/started": 2}


def test_updated_terminal_item_clears_active_activity():
    active = {"cmd-1": "commandExecution"}

    CodexAppServerAdapter._track_activity(
        {
            "method": "item/updated",
            "params": {
                "item": {
                    "id": "cmd-1",
                    "type": "commandExecution",
                    "status": "completed",
                }
            },
        },
        active,
    )

    assert active == {}
