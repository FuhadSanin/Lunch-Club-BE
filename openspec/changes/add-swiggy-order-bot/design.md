# Design: Swiggy order bot

## Technical approach

A separate Cloud Run service (`order-bot`), built from the same code with Playwright and
Chromium installed, receives the `place-order` and `poll-delivery` jobs. The API service
stays small and fast to start; only the bot pays for a browser.

```
Cloud Tasks ─▶ order-bot (Cloud Run, 2 GiB, concurrency 1)
                 └─ SwiggyWebProvider ─▶ swiggy.com (headless Chromium)
                       └─ storage state ◀─ Secret Manager: swiggy-session-<host id>
```

## Decisions

### Decision: Store Playwright storage state, not credentials
Linking runs the phone + OTP login once in the bot and saves the browser storage state
(cookies, local storage) to Secret Manager as `swiggy-session-<host id>`. No password or
OTP is ever stored, nothing is sent to the browser, and revoking is deleting the secret.

### Decision: Concurrency 1 per bot instance
One browser checkout at a time per container keeps memory predictable and makes the
logs of one attempt easy to follow.

### Decision: The order note carries the one-time key
`place` writes the first 8 characters of the checkout id into Swiggy's order
instructions, and `find_existing` looks for it in the host's recent orders. This is how
a retry after a crash recognises an order that already went through.

## Open questions

- Selectors and flow must be recorded against the live site with the host's account;
  they cannot be written or tested from a sandbox without access to swiggy.com.
- Does Swiggy's multi-restaurant cart accept order instructions per checkout?
  If not, match recent orders by restaurants, items and total within the last 15 minutes.
