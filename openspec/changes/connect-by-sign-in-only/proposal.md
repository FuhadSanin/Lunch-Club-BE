# Proposal: One way to connect an AI assistant: signing in

## Why

An assistant could connect in two ways: by signing in (the person presses Allow and the
assistant is given an hour-long token it renews), or with a personal access token that the
person made in the web app, copied and pasted into the assistant's settings. Two ways meant
two sets of instructions on the Connect page, a long-lived secret that people had to keep
safe, and a second path to review. The sign-in way is the better one (nothing to copy, short
tokens, the person sees who is asking), so it should be the only one.

Looking at what "Disconnect" did also found a gap: ending an assistant's connection from the
person's list revoked only its access token, so an assistant holding a valid refresh token
could sign itself straight back in.

## What Changes

- **No more personal access tokens.** `POST /api/me/tokens`, its serializer, the service
  that made them (`create_api_token`), the ten-token cap and the `tokens` rate limit are
  gone. Access tokens are only made when an assistant signs in (`apps/oauth`).
- **Connected assistants.** `GET /api/me/tokens` still lists the assistants signed in as
  you (the table and the endpoint are unchanged), and `DELETE /api/me/tokens/<id>` now
  disconnects one: its access token and the refresh token behind it (everything that app has
  from the person), so it can't renew itself.
- **Wording.** Errors and the MCP server's messages say "connect the assistant again"
  instead of "make a new token".
- **Web app (`../lunch-board`).** The page is "Connect to an AI assistant". It shows the one
  way (three steps), the assistants connected with Disconnect, four examples with pictures of
  the app, and the 23 tools.

## Impact

- API: `POST /api/me/tokens` answers 405; people who had made a token must connect their
  assistant by signing in. Nothing else changes for assistants that already signed in.
- Specs: accounts (connecting an assistant).
- The stdio mode of the MCP server (`LUNCHCLUB_TOKEN`) stays for developing the server;
  there is no longer a way to make such a token in the app.
