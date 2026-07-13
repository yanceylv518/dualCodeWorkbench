import hmac
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SidecarTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not str(scope.get("path", "")).startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        supplied = self._token(scope)
        if supplied and hmac.compare_digest(supplied, self.token):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401, "reason": "Unauthorized"})
            return
        response: Message = {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        }
        await send(response)
        await send({"type": "http.response.body", "body": b'{"detail":"Unauthorized"}'})

    @staticmethod
    def _token(scope: Scope) -> str:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        header = headers.get(b"x-dualcode-token")
        if header:
            return header.decode("utf-8", errors="ignore")
        query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="ignore"))
        return query.get("token", [""])[0]
