# Tasks: One way to connect an AI assistant

## 1. API
- [x] 1.1 Remove `POST /api/me/tokens`, `NewApiTokenSerializer`, `create_api_token`, `MAX_ACTIVE_TOKENS` and the `tokens` throttle scope
- [x] 1.2 `DELETE /api/me/tokens/<id>` disconnects through `oauth.disconnect`: the access token and the refresh token behind it
- [x] 1.3 Messages: "connect the assistant again" (API and MCP server); `Connect to an AI assistant` in the docs
- [x] 1.4 Tests: tokens are made by `issue_api_token` as sign-in does; a token can't be made by hand (405); disconnecting also ends the refresh token
- Verified by: `tests/test_api_tokens.py`, `tests/test_oauth.py`, `tests/test_mcp.py`, `tests/test_throttling.py`; ruff, mypy; a browser run that signs an assistant in, disconnects it, and tries to renew it (refused)

## 2. Web app
- [x] 2.1 Connect to an AI assistant: the three steps, the connected list with Disconnect, four examples with pictures (light and dark, press to enlarge), the tools
- [x] 2.2 No token form, token list or token snippets; `useConnections` and `useDisconnect` replace the token hooks
- [x] 2.3 Pictures are real screens of the app in demo mode, made by `scripts/connect-shots.mjs`
- Verified by: `npm test` (the examples only use tools that exist), lint, tsc, and a browser run at desktop and phone width
