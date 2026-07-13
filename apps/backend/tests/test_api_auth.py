import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dualcode.config import sidecar_token
from dualcode.main import app


@pytest.mark.asyncio
async def test_api_rejects_missing_and_wrong_token_and_accepts_correct_token():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/health")).status_code == 401
        assert (
            await client.get("/api/health", headers={"X-DualCode-Token": "wrong"})
        ).status_code == 401
        response = await client.get(
            "/api/health", headers={"X-DualCode-Token": sidecar_token}
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_api_accepts_token_query_parameter():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(f"/api/health?token={sidecar_token}")).status_code == 200


@pytest.mark.parametrize("query", ["", "?token=wrong"])
def test_websocket_rejects_missing_and_wrong_token_with_4401(query: str):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(f"/api/ws/threads/auth-test{query}"):
                pass

    assert rejected.value.code == 4401


def test_websocket_accepts_correct_token_and_sends_connected_event():
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/ws/threads/auth-test?token={sidecar_token}"
        ) as websocket:
            event = websocket.receive_json()

    assert event["type"] == "connected"
    assert event["thread_id"] == "auth-test"
