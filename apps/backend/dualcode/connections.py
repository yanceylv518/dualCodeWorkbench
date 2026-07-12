import asyncio
from collections import defaultdict
from fastapi import WebSocket
from .events import AgentEvent


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, thread_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[thread_id].add(websocket)

    async def disconnect(self, thread_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[thread_id].discard(websocket)

    async def publish(self, event: AgentEvent) -> None:
        stale: list[WebSocket] = []
        for websocket in tuple(self._connections[event.thread_id]):
            try:
                await websocket.send_json(event.model_dump(mode="json"))
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            await self.disconnect(event.thread_id, websocket)


manager = ConnectionManager()
