# Proposal: Final picks

## Why

Hosts want each person's order settled before they check the cart: once someone says
their order is complete, or that they're skipping today, it shouldn't keep changing. If
someone needs a change, the host decides whether to let them.

## What Changes

- A person can complete their order for a lunch (`PUT /api/lunches/<id>/complete`). After
  that they can't add, change or remove picks, or skip.
- Skipping is final in the same way. Picking a dish no longer undoes a skip.
- The host can reopen someone (`DELETE /api/lunches/<id>/complete/<user id>`), which also
  undoes a skip, so they can pick again.
- The board lists who is done under `completed`.
- A person's whole cart can be saved, and completed, in one request
  (`PUT /api/lunches/<id>/picks`), instead of one request per dish. Skipping answers with the
  board, like the other lunch actions, so the app never has to fetch it again.

## Impact

- Lunches spec: picking, skipping, and a new requirement for completing and reopening.
- Model: `OrderSession.completed_by`, table `order_session_completions` with the same
  company row-level security as skips (migration `lunches/0003`).
- Web app: Pick my lunch asks for confirmation before completing or skipping and then
  stays read-only; Review and order lets the host reopen a person.
