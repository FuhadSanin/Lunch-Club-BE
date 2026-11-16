"""Run the Tiffmate MCP server.

    python -m mcp_server                  for one person on their own computer (stdio);
                                          set LUNCHCLUB_TOKEN to their access token
    python -m mcp_server --http --port 8080
                                          as a shared web service (HTTP at /mcp);
                                          each caller sends their own token

Settings: LUNCHCLUB_API_URL (the REST API), LUNCHCLUB_WEB_URL (links to share),
LUNCHCLUB_TZ (default Asia/Kolkata), LUNCHCLUB_TOKEN (stdio only).
"""

import argparse
import os

from mcp_server.server import Config, create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Tiffmate MCP server")
    parser.add_argument(
        "--http", action="store_true", help="serve over HTTP, not stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.http:
        import uvicorn

        from mcp_server.web import app

        uvicorn.run(app, host=args.host, port=args.port)
    else:
        create_server(Config(env_token=os.environ.get("LUNCHCLUB_TOKEN"))).run()


if __name__ == "__main__":
    main()
