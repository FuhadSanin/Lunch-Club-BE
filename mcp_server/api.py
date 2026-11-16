"""A small client for the Lunch Club REST API, used by the MCP tools."""

from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

MISSING = (
    "You're not signed in to Lunch Club. Connect the assistant again: add this server "
    "in your assistant and press Connect, then Allow (Connect to an AI assistant, in "
    "Lunch Club, shows how)."
)
REJECTED = (
    "Lunch Club didn't accept that sign-in. It may have expired or been ended: connect "
    "the assistant again (Connect to an AI assistant, in Lunch Club)."
)


class Api:
    """Calls the REST API as one person (their token). Errors become ToolErrors with
    the API's own message, which is written for people, so the assistant can say it."""

    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport

    async def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    f"/api/{path}",
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=json,
                )
        except httpx.HTTPError as error:
            raise ToolError(f"Couldn't reach Lunch Club: {error}") from error
        if response.status_code == 401:
            raise ToolError(REJECTED)
        if response.status_code >= 400:
            raise ToolError(_message(response))
        return response.json() if response.content else None

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, params=params)


def _text(problem: Any) -> str:
    return " ".join(map(str, problem)) if isinstance(problem, list) else str(problem)


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and "matches the given query" not in detail:
        return detail  # the API's own message, written for people
    if isinstance(body, dict) and body and not isinstance(detail, str):
        # a validation error: {"field": ["problem"]}
        return "; ".join(
            f"{field}: {_text(problem)}" for field, problem in body.items()
        )
    if response.status_code == 404:
        return "Lunch Club couldn't find that (or you can't see it)."
    return f"Lunch Club answered HTTP {response.status_code}."
