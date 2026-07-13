import httpx
import pytest

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
