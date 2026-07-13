import asyncio
import json

import pytest

from dualcode.adapters import AgentRequest
from dualcode.codex_app_server import AppServerProtocolError, CodexAppServerAdapter


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

    def emit(self, value):
        self.stdout.queue.put_nowait((json.dumps(value) + "\n").encode())

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
async def test_app_server_resumes_existing_thread(monkeypatch, tmp_path):
    process = FakeProcess()
    adapter = CodexAppServerAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    response = await adapter.send(AgentRequest("local-thread", "again", {"workspace_path": str(tmp_path), "session_id": "thread-app-1"}))

    assert response.run_id == "thread-app-1"
    assert not any(item.get("method") == "thread/start" for item in process.stdin.writes)
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
