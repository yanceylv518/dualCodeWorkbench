import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentCapabilities:
    vision: bool = False
    supported_image_types: frozenset[str] = frozenset()
    max_image_bytes: int = 0
    max_images_per_request: int = 0
    native_file_input: bool = False


@dataclass(frozen=True)
class AgentAttachment:
    id: str
    local_path: Path
    media_type: str
    size: int
    sha256: str


@dataclass
class AgentRequest:
    thread_id: str
    prompt: str
    context: dict[str, object]
    attachments: list[AgentAttachment] = field(default_factory=list)


@dataclass
class AgentResponse:
    run_id: str
    content: str


class AgentAdapter(ABC):
    capabilities = AgentCapabilities()

    @abstractmethod
    async def send(self, request: AgentRequest) -> AgentResponse: ...
    @abstractmethod
    async def stream(self, request: AgentRequest) -> AsyncIterator[str]:
        yield ""

    @abstractmethod
    async def cancel(self, run_id: str) -> None: ...
    @abstractmethod
    async def resume(self, run_id: str) -> AgentResponse: ...
    @abstractmethod
    async def health_check(self) -> bool: ...


class MockAdapter(AgentAdapter):
    name = "mock"

    async def send(self, request):
        return AgentResponse(f"{self.name}-{request.thread_id}", f"[{self.name}] {request.prompt}")

    async def stream(self, request):
        for part in ("正在分析…", "生成方案…", "完成。"):
            await asyncio.sleep(0.05)
            yield part

    async def cancel(self, run_id):
        return None

    async def resume(self, run_id):
        return AgentResponse(run_id, "已恢复")

    async def health_check(self):
        return True


class MockCodexAdapter(MockAdapter):
    name = "codex"
    capabilities = AgentCapabilities(
        True, frozenset({"image/png", "image/jpeg", "image/webp"}), 10 * 1024 * 1024, 8, True
    )


class MockClaudeAdapter(MockAdapter):
    name = "claude"
    capabilities = AgentCapabilities(
        True, frozenset({"image/png", "image/jpeg", "image/webp"}), 10 * 1024 * 1024, 8, True
    )


class ClaudeSshAdapter(MockAdapter):
    name = "claude-ssh"

    async def send(self, request):
        raise NotImplementedError("真实 Claude SSH 适配器尚未配置；实现必须使用参数化 SSH 调用")
