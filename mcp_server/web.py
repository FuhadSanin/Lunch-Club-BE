"""The MCP server as a web app (ASGI): what a host like Vercel or uvicorn runs.

Stateless: each request stands alone, which serverless hosting needs. Every caller sends
their own `Authorization: Bearer <access token>`; none is kept here.

It is an OAuth 2.1 *resource server*: it says where to sign in (RFC 9728 metadata) and
answers a call with no valid token with 401 and a `WWW-Authenticate` pointer to that
metadata, which is what makes an assistant open the sign-in page. The API
(apps/oauth) is the authorization server that issues the tokens.
"""

import hashlib
import json
import os
import time
from typing import Any

import httpx
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.server import Config, create_server

SCOPE = "lunch"
MCP_PATH = "/mcp"
METADATA_PATHS = {
    "/.well-known/oauth-protected-resource",
    f"/.well-known/oauth-protected-resource{MCP_PATH}",
}
# How long a token already checked with the API is trusted, so a busy assistant isn't
# one extra API call per message. A revoked token stops working within this long.
CHECK_SECONDS = 60
MAX_CHECKED = 2000


class OAuthGate:
    """Wraps the MCP app: publishes the sign-in metadata, and lets a call through only
    with a token the API accepts."""

    def __init__(
        self,
        app: Any,
        *,
        api_url: str,
        issuer: str,
        public_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.app = app
        self.api_url = api_url.rstrip("/")
        self.issuer = issuer.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.transport = transport
        self._checked: dict[str, float] = {}

    # The wrapped app's router, so a host can run its startup and shutdown.
    @property
    def router(self) -> Any:
        return self.app.router

    def _base(self, scope: dict) -> str:
        if self.public_url:
            return self.public_url
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        proto = headers.get("x-forwarded-proto", scope.get("scheme", "https"))
        return f"{proto}://{headers.get('host', 'localhost')}"

    def metadata(self, scope: dict) -> dict:
        base = self._base(scope)
        return {
            "resource": f"{base}{MCP_PATH}",
            "authorization_servers": [self.issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [SCOPE],
            "resource_name": "Lunch Club",
        }

    async def _valid(self, token: str) -> bool:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        if self._checked.get(key, 0) > now:
            return True
        try:
            async with httpx.AsyncClient(
                base_url=self.api_url, timeout=15, transport=self.transport
            ) as client:
                answer = await client.get(
                    "/api/me", headers={"Authorization": f"Bearer {token}"}
                )
        except httpx.HTTPError:
            return (
                True  # the API is unreachable: the tools will say so, no sign-in loop
            )
        if answer.status_code == 401:
            return False
        if len(self._checked) >= MAX_CHECKED:
            self._checked.clear()
        self._checked[key] = now + CHECK_SECONDS
        return True

    @staticmethod
    async def _json(
        send: Any, status: int, body: dict, extra: list | None = None
    ) -> None:
        payload = json.dumps(body).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
            (b"cache-control", b"no-store"),
            *(extra or []),
        ]
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": payload})

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"].rstrip("/") or "/"
        if path in METADATA_PATHS and scope["method"] == "GET":
            return await self._json(send, 200, self.metadata(scope))
        if path == MCP_PATH and scope["method"] != "OPTIONS":
            sent = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            word, _, token = sent.get("authorization", "").partition(" ")
            token = token if word.lower() == "bearer" else ""
            if not token or not await self._valid(token):
                base = self._base(scope)
                where = f"{base}/.well-known/oauth-protected-resource{MCP_PATH}"
                challenge = f'Bearer resource_metadata="{where}", scope="{SCOPE}"'
                if token:
                    challenge += ', error="invalid_token"'
                return await self._json(
                    send,
                    401,
                    {
                        "error": "invalid_token" if token else "unauthorized",
                        "error_description": "Sign in to Lunch Club to use this.",
                    },
                    [(b"www-authenticate", challenge.encode())],
                )
        return await self.app(scope, receive, send)


def build_app(
    config: Config | None = None,
    *,
    public_url: str | None = None,
    issuer: str | None = None,
    allowed_hosts: list[str] | None = None,
) -> OAuthGate:
    cfg = config or Config()
    hosts = (
        allowed_hosts
        if allowed_hosts is not None
        else [
            h.strip()
            for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ]
    )
    inner = create_server(cfg).streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(hosts),
            allowed_hosts=hosts,
            allowed_origins=[f"https://{h}" for h in hosts],
        ),
    )
    return OAuthGate(
        inner,
        api_url=cfg.api_url,
        issuer=issuer or os.environ.get("MCP_AUTH_ISSUER") or cfg.api_url,
        public_url=public_url or os.environ.get("MCP_PUBLIC_URL", ""),
        transport=cfg.transport,
    )


app = build_app()
