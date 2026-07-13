import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from dualcode.adapters import AgentRequest
from dualcode.cli_adapters import ClaudeCliAdapter, CodexCliAdapter
from dualcode.events import EventType

# Scheduler construction reads persisted per-user adapter configuration at import time.
# Keep resilience tests hermetic and away from real SSH paths or credentials.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="dualcode-agent-resilience-"))
from dualcode.scheduler import RunScheduler


class FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, lines=(), tail=b""):
        self.lines = list(lines)
        self.tail = tail

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""

    async def read(self):
        return self.tail


class FakeProcess:
    def __init__(self, *, lines=(), stderr=b"", exit_code=0, wait_gate=None):
        self.stdin = FakeStdin()
        self.stdout = FakeStream(lines)
        self.stderr = FakeStream(tail=stderr)
        self.returncode = None
        self.exit_code = exit_code
        self.wait_gate = wait_gate
        self.terminated = False
        self.killed = False

    async def wait(self):
        if self.wait_gate is not None and not self.killed and not self.terminated:
            await self.wait_gate.wait()
        self.returncode = -9 if self.killed else -15 if self.terminated else self.exit_code
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def request(tmp_path: Path, session_id=None):
    context = {"workspace_path": str(tmp_path)}
    if session_id:
        context["session_id"] = session_id
    return AgentRequest("thread-1", "hello", context)


@pytest.mark.asyncio
async def test_codex_parses_session_and_replaces_invalid_utf8(monkeypatch, tmp_path):
    events = [
        json.dumps({"type": "thread.started", "thread_id": "session-1"}).encode() + b"\n",
        b'not-json-\xff\n',
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}).encode() + b"\n",
    ]
    process = FakeProcess(lines=events)
    adapter = CodexCliAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    response = await adapter.send(request(tmp_path))

    assert (response.run_id, response.content) == ("session-1", "done")
    assert process.stdin.data == b"hello"
    assert process.stdin.closed
    assert not adapter._processes


@pytest.mark.asyncio
async def test_cli_nonzero_exit_surfaces_stderr_and_cleans_registry(monkeypatch, tmp_path):
    process = FakeProcess(stderr=b"specific failure\n", exit_code=7)
    adapter = CodexCliAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    with pytest.raises(RuntimeError, match="specific failure"):
        await adapter.send(request(tmp_path))
    assert not adapter._processes


@pytest.mark.asyncio
async def test_cli_timeout_terminates_process(monkeypatch, tmp_path):
    process = FakeProcess(wait_gate=asyncio.Event())
    adapter = CodexCliAdapter("fake", timeout_seconds=0.01)
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    with pytest.raises(TimeoutError):
        await adapter.send(request(tmp_path))
    assert process.terminated
    assert not adapter._processes


@pytest.mark.asyncio
async def test_cli_cancel_terminates_active_process():
    process = FakeProcess(wait_gate=asyncio.Event())
    adapter = CodexCliAdapter("fake")
    adapter._processes["run-1"] = process

    await adapter.cancel("run-1")

    assert process.terminated


@pytest.mark.asyncio
async def test_health_check_timeout_kills_probe(monkeypatch):
    process = FakeProcess(wait_gate=asyncio.Event())
    adapter = ClaudeCliAdapter("fake")
    monkeypatch.setattr(adapter, "resolve_executable", lambda: "fake")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=process))

    original_wait_for = asyncio.wait_for

    async def immediate_timeout(awaitable, timeout):
        if timeout == 5:
            awaitable.close()
            raise TimeoutError
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)
    assert not await adapter.health_check()
    assert process.killed
    assert process.returncode == -9


class FakeAdapter:
    def __init__(self, chunks):
        self.chunks = chunks

    async def stream(self, request):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent", "chunks", "expected_session", "expected_text"),
    [
        ("codex", [json.dumps({"type": "thread.started", "thread_id": "cx-2"}), json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "second"}})], "cx-2", "second"),
        ("claude", [json.dumps({"type": "system", "session_id": "cl-2"}), json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "continued"}]}})], "cl-2", "continued"),
    ],
)
async def test_scheduler_stream_preserves_session_for_next_turn(agent, chunks, expected_session, expected_text):
    emitted = []

    async def emit(kind, payload):
        emitted.append((kind, payload))

    response = await RunScheduler._stream_agent(None, FakeAdapter(chunks), request=None, agent=agent, emit=emit)

    assert response.run_id == expected_session
    assert response.content == expected_text
    assert (EventType.AGENT_DELTA, {"agent": agent, "text": expected_text}) in emitted


@pytest.mark.asyncio
async def test_scheduler_streams_safe_codex_reasoning_and_tool_progress():
    chunks = [
        json.dumps({"type": "thread.started", "thread_id": "cx-progress"}),
        json.dumps({"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "Inspecting the repository structure"}}),
        json.dumps({"type": "item.started", "item": {"id": "c1", "type": "command_execution", "command": "git status"}}),
        json.dumps({"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "command": "git status", "exit_code": 0}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "finished"}}),
    ]
    emitted = []

    async def emit(kind, payload):
        emitted.append((kind, payload))

    response = await RunScheduler._stream_agent(None, FakeAdapter(chunks), request=None, agent="codex", emit=emit)

    assert response.content == "finished"
    progress = [payload for kind, payload in emitted if kind == EventType.TOOL_EVENT]
    assert [item["item"]["type"] for item in progress] == ["reasoning", "command_execution", "command_execution"]
