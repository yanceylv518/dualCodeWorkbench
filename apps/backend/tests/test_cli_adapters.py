from pathlib import Path

import pytest

from dualcode.adapters import AgentAttachment, AgentRequest
from dualcode.cli_adapters import ClaudeCliAdapter, CodexCliAdapter


def test_codex_builds_parameterized_image_arguments(tmp_path: Path):
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    request = AgentRequest(
        "thread-1",
        "inspect",
        {"workspace_path": str(tmp_path)},
        [AgentAttachment("a1", image, "image/png", 3, "hash")],
    )
    args = CodexCliAdapter().command_args(request)
    assert args[:5] == ["exec", "--json", "--sandbox", "danger-full-access", "--skip-git-repo-check"]
    assert args[-3:] == ["--image", str(image.resolve()), "-"]


def test_claude_is_forced_to_read_only_plan_mode():
    request = AgentRequest("thread-1", "review", {"workspace_path": "."})
    args = ClaudeCliAdapter().command_args(request)
    assert "--verbose" in args
    assert args[args.index("--permission-mode") + 1] == "plan"
    assert args[args.index("--tools") + 1] == ""


def test_codex_resume_uses_explicit_session(tmp_path: Path):
    session = "11111111-1111-4111-8111-111111111111"
    request = AgentRequest("thread-1", "continue", {"workspace_path": str(tmp_path), "session_id": session})
    args = CodexCliAdapter(model="gpt-test", reasoning_effort="high").command_args(request)
    assert args[:3] == ["exec", "resume", "--json"]
    assert session in args
    assert args[-1] == "-"


def test_claude_resume_uses_explicit_session():
    session = "22222222-2222-4222-8222-222222222222"
    request = AgentRequest("thread-1", "continue", {"workspace_path": ".", "session_id": session})
    args = ClaudeCliAdapter().command_args(request)
    assert args[args.index("--resume") + 1] == session


@pytest.mark.asyncio
async def test_missing_cli_is_unhealthy():
    adapter = CodexCliAdapter("definitely-not-a-real-dualcode-cli")
    assert not await adapter.health_check()
