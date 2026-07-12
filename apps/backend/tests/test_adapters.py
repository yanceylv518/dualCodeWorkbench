import pytest
from dualcode.adapters import AgentRequest, MockCodexAdapter


@pytest.mark.asyncio
async def test_mock_codex_declares_vision_and_responds():
    adapter = MockCodexAdapter()
    assert adapter.capabilities.vision
    response = await adapter.send(AgentRequest("thread-1", "hello", {}))
    assert "hello" in response.content
