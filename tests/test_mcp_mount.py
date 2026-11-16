"""/mcp served from the API's own WSGI app (what Vercel runs)."""

import io
import json

import pytest

from config.mcp_mount import is_mcp_path, mount_mcp

API = "https://api.example.test"
WEB = "https://web.example.test"


def django_stub(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"django"]


def call(app, path, method="GET", host="api.example.test", body=b"", headers=None):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "SERVER_NAME": host,
        "SERVER_PORT": "443",
        "wsgi.url_scheme": "https",
        "wsgi.input": io.BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
        "HTTP_HOST": host,
        "HTTP_ACCEPT": "application/json, text/event-stream",
        **(headers or {}),
    }
    seen = {}

    def start(status, response_headers, exc_info=None):
        seen["status"] = status
        seen["headers"] = dict(response_headers)

    chunks = b"".join(app(environ, start))
    return seen["status"], seen["headers"], chunks


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/mcp", True),
        ("/mcp/", True),
        ("/.well-known/oauth-protected-resource", True),
        ("/.well-known/oauth-protected-resource/mcp", True),
        ("/.well-known/oauth-authorization-server", False),
        ("/api/me", False),
        ("/mcpx", False),
    ],
)
def test_which_paths_go_to_the_assistant_server(path, expected):
    assert is_mcp_path(path) is expected


def test_without_a_configured_address_the_one_it_was_reached_at_is_used():
    app = mount_mcp(django_stub, "", WEB)

    _, _, body = call(app, "/.well-known/oauth-protected-resource/mcp")

    assert json.loads(body)["authorization_servers"] == [API]


def test_other_paths_still_reach_django():
    app = mount_mcp(django_stub, API, WEB)

    status, _, body = call(app, "/api/me")

    assert status.startswith("200") and body == b"django"


def test_the_sign_in_details_name_the_web_app_and_the_api():
    app = mount_mcp(django_stub, API, WEB)

    status, _, body = call(app, "/.well-known/oauth-protected-resource/mcp")

    assert status.startswith("200")
    found = json.loads(body)
    assert found["resource"] == f"{WEB}/mcp"
    assert found["authorization_servers"] == [API]


def test_a_call_without_a_token_is_asked_to_sign_in():
    app = mount_mcp(django_stub, API, WEB)

    status, headers, _ = call(app, "/mcp", method="POST", body=b"{}")

    assert status.startswith("401")
    assert (
        f'resource_metadata="{WEB}/.well-known/oauth-protected-resource/mcp"'
        in (headers["www-authenticate"])
    )


def test_the_server_starts_for_each_request_under_wsgi(monkeypatch):
    """The MCP app's start-up is run around the request (nothing else would run it);
    a call with a token the API accepts gets the tools."""
    import httpx

    from mcp_server import web

    class Accepting(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json={"name": "A", "email": "a@x.test"})

    real = web.OAuthGate.__init__

    def init(self, *args, **kwargs):
        kwargs["transport"] = Accepting()
        real(self, *args, **kwargs)

    monkeypatch.setattr(web.OAuthGate, "__init__", init)
    app = mount_mcp(django_stub, API, WEB)
    hello = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }

    status, _, body = call(
        app,
        "/mcp",
        method="POST",
        body=json.dumps(hello).encode(),
        headers={"HTTP_AUTHORIZATION": "Bearer lc_abc"},
    )

    assert status.startswith("200"), body
    assert json.loads(body)["result"]["serverInfo"]["name"] == "Tiffmate"


def test_a_host_that_is_not_ours_is_refused():
    app = mount_mcp(django_stub, API, WEB)

    status, _, _ = call(
        app,
        "/mcp",
        method="POST",
        host="evil.example",
        body=b"{}",
        headers={"HTTP_AUTHORIZATION": "Bearer lc_abc"},
    )

    assert not status.startswith("200")
