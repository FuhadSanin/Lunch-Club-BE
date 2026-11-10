# Proposal: Group lunch backend

## Why

Swiggy's multi-restaurant checkout (since 15 October 2026) is still one person's cart,
capped at five restaurants. Collecting a whole team's picks is the gap. This change builds
the backend for roadmap steps 1 and 2 of the architecture design, plus everything in
step 3 except the order bot itself.

## What Changes

- Google sign-in through Firebase; companies by email domain; admin, host, member roles
- Company isolation in the API and in PostgreSQL (row-level security)
- Lunches: start with a cutoff, pick dishes, skip, lock at the cutoff, cancel
- Splitting picks into Swiggy checkouts of at most 5 restaurants
- Host confirmation, exactly-once placement, delivery tracking, host hand-off
- Live board in Firestore; push and email notifications; structured logs and Sentry

Not included (separate changes): the order bot that checks out on the Swiggy website
(`add-swiggy-order-bot`), Swiggy MCP ordering, bill splitting, repeat lunches, budgets,
local menus and Zomato.

## Impact

New Django service for Cloud Run, backed by Cloud SQL, Firestore, Cloud Tasks and
Firebase. Specs merged into `openspec/specs/`: accounts, lunches, ordering, live-board,
notifications. Ordering sits behind a provider interface whose first implementation hands
every order to the host, which is also the fallback when automatic ordering fails.
