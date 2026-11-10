# Design: Group lunch backend

## Technical approach

```
Web app ──Firebase ID token──▶ Django API (Cloud Run) ──▶ PostgreSQL (Cloud SQL, RLS)
   ▲                               │        │
   │ listens                       │        └─ enqueue ─▶ Cloud Tasks ─▶ /internal/tasks/<job>
   └──── Firestore boards/ ◀───────┘ write after commit
                                   └─▶ FCM topic org-<id>, email
```

| App | Owns |
|-----|------|
| `apps/core` | Base model, jobs (Cloud Tasks), tenancy (RLS scope), notifications, JSON logs |
| `apps/accounts` | `organizations`, `users`, Firebase authentication, roles |
| `apps/lunches` | `order_sessions`, `order_items`, `swiggy_orders`, `order_session_skips`, live board |
| `apps/ordering` | Confirm, place, hand-off, delivery; provider interface |

Business rules live in each app's `services.py`; views only parse input and shape output.

## Decisions

### Decision: Firebase is the only sign-in, so Django's auth app is not installed
Users are a plain model keyed by `firebase_uid`. This removes passwords, the admin site,
sessions and CSRF from the attack surface. DRF runs with `UNAUTHENTICATED_USER = None`.

### Decision: Companies are matched by email domain
"Invite the team" is sharing the app link: anyone with a verified `@company.com` Google
account joins as a member on first sign-in. Consumer domains are blocked from becoming
companies so strangers on gmail.com are never grouped together.

### Decision: Row-level security with a transaction-local setting
Lunch tables have a `company_isolation` policy on `app.org_id`, set with
`set_config(..., true)` so it ends with the transaction and can't leak across pooled
connections. With no company set, nothing is visible. `FORCE` applies it to the owner
role the app connects as. Verified on PostgreSQL 16: company A sees 1/1/1/1 rows across
the four tables, company B sees only its own, unscoped sees 0, and a cross-company insert
is rejected. Consequence: the app's database role must not be a superuser; the test
suite, by contrast, needs one (fixtures write without a company scope, and the isolation
test creates a probe role).

### Decision: One lunch row lock serialises everything in a lunch
Picks, skips, lock, confirm and order updates take `SELECT … FOR UPDATE` on the lunch
row first (then the order row, never the reverse). A pick can't slip in while the cart
is being built, and two checkouts finishing together can't both miss the "all placed"
transition.

### Decision: Exactly-once placement without an extra table
The `swiggy_orders.id` UUID is the one-time key. The place job commits `placing` before
the slow checkout starts. A retry within the 10-minute lease backs off; after it, the
provider's `find_existing` is asked before ordering again. Cloud Tasks task names
(`place-order-<order id>`) stop the job being queued twice.

### Decision: Any unexpected ordering error hands the order to the host
Blind retries of a website checkout risk double orders, so an error marks the checkout
`needs_host`, emails the host and raises a Sentry alert. The host checks the Swiggy app
and enters the order number.

### Decision: The live board is a server-written Firestore copy
PostgreSQL stays the source of truth. After commit, the server writes the same JSON the
API returns to `boards/<lunch id>`. Firestore rules allow reads only when the
`org_id` custom claim (set when someone joins a company) matches, and deny all client
writes. A failed board write is logged and never fails the change.

### Decision: Jobs run inline locally
With `CLOUD_TASKS_ENABLED=false`, due jobs run right after commit and future ones are
skipped, so the full flow works on a laptop; the host locks lunches by hand there.

## Deviations from the design document's schema

| Change | Why |
|--------|-----|
| `users.org_id` is nullable | Someone can sign in before their company is set up |
| `order_session_skips` join table | "Skip the day" needs to record who skipped |
| `swiggy_orders.last_error` | Tells the host what to do when an order needs them |

## File changes

All new: `config/`, `apps/`, `tests/`, `firestore.rules`, `Dockerfile`, `pyproject.toml`.
