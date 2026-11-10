# Proposal: Swiggy order bot

## Why

Today every confirmed lunch is handed to the host, who checks out in the Swiggy app.
Roadmap step 3 automates that checkout on the Swiggy website using the host's own
account. Until Swiggy approves MCP access (the review can take 4+ weeks), this is the
only way to order automatically.

## What Changes

- Hosts can link their Swiggy account once with their phone number and an OTP; the login
  is stored encrypted in Secret Manager.
- A new ordering provider fills one Swiggy cart (up to 5 restaurants) and checks out in
  headless Chromium, enabled with `ORDER_PROVIDER`.
- The provider finds an existing order before any retry and reads delivery status.
- Pilot for our own team only, a few orders a day.

Not included: Swiggy MCP ordering (its own change once approved; ₹1,000 cap per order)
and opening the bot to other companies.

## Risks

- Swiggy's terms don't allow bots, so the host's Swiggy account could be blocked.
  Mitigation: own team only, a few orders a day; switch to the MCP once approved.
- Swiggy changes its website and the bot breaks. Mitigation: every failure already hands
  the order to the host with an alert (see the ordering spec).

## Impact

No change to the lunch flow: exactly-once placement, hand-off and delivery tracking
already go through `OrderProvider` (`apps/ordering/providers/base.py`).
