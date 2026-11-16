"""Serves the Tiffmate MCP server from the API's own web address (/mcp), so a host
that runs only the Django app (Vercel) needs no second service.

The MCP server is an ASGI app and the API runs as WSGI, so the MCP requests are handed
to it through a2wsgi; everything else goes to Django as before. If the MCP packages
aren't installed, nothing is mounted and the API is exactly as it was. The API's own
address is API_PUBLIC_URL, or the address the request came to.

The web app forwards its own /mcp to this one (lunch-board/vercel.json), so people and
assistants only ever see the web app's address (MCP_PUBLIC_URL there is APP_URL).
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

MCP_PATH = "/mcp"
METADATA_PREFIX = "/.well-known/oauth-protected-resource"


def is_mcp_path(path: str) -> bool:
    path = path.rstrip("/")
    return path == MCP_PATH or path.startswith(METADATA_PREFIX)


def mount_mcp(django_app: Callable[..., Any], api_url: str, web_url: str) -> Any:
    try:
        from a2wsgi import ASGIMiddleware

        from mcp_server.server import Config
        from mcp_server.web import build_app
    except ImportError:
        return django_app

    def fresh(scope: Any) -> Any:
        own = api_url
        if not own:
            sent = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            own = f"https://{sent.get('host', 'localhost')}"
        hosts = [urlsplit(u).netloc for u in (own, web_url) if urlsplit(u).netloc]
        return build_app(
            Config(api_url=own, web_url=web_url),
            public_url=web_url,
            issuer=own,
            allowed_hosts=hosts,
        )

    async def per_request(scope: Any, receive: Any, send: Any) -> None:
        # Under WSGI nothing runs the MCP app's start-up, and its session manager can
        # only be started once, so each request gets its own copy, started and stopped
        # around it. The server keeps no state between calls (stateless HTTP), so
        # nothing is lost.
        app = fresh(scope)
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)

    mcp_app = ASGIMiddleware(per_request)

    def application(environ: Any, start_response: Callable[..., Any]):
        if is_mcp_path(environ.get("PATH_INFO", "")):
            return mcp_app(environ, start_response)
        return django_app(environ, start_response)

    return application
