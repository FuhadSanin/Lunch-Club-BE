# Tasks

## 1. Roles and permissions
- [x] 1.1 `Role`, `RolePermission`, `UserRole`; `User.permissions` computed from roles; Host and Member made with each company
- [x] 1.2 `apps/accounts/rbac.py`: make, change, delete and give roles, with the four rules and a lock on the company
- [x] 1.3 `GET/POST /api/company/roles`, `PATCH/DELETE /api/company/roles/<id>`, `PUT /api/company/members/<id>/roles`; roles on `/api/me` and the members list
- [x] 1.4 Existing people's permissions moved into roles (Host, Member, "Custom N")
- Verified by: `tests/test_roles.py`, `tests/test_permissions.py`, `tests/test_accounts.py`

## 2. Addresses, skips, orders
- [x] 2.1 `Address` (text, `lat`, `lng`, `saved`) replaces office addresses; add and remove, reusing the same text in any case
- [x] 2.2 `OrderSession.address`; delivery columns, the company's office point and lunch defaults removed
- [x] 2.3 Skips table removed; `skipped` on the board is completed with no dishes
- [x] 2.4 `SwiggyOrder` → `Order`, `swiggy_order_id` → `platform_order_id`
- [x] 2.5 `Organization.created_by`, filled in for existing companies
- Verified by: `tests/test_company_settings.py`, `tests/test_lunches.py`, `tests/test_parties.py`, `tests/test_ordering.py`

## 3. Running a lunch
- [x] 3.1 The four tick lists on the lunch and `PUT /api/lunches/<id>/ticks` (`session_update`); review ticks cleared when a pick changes
- [x] 3.2 A company can't start a second lunch while one is running (409)
- [x] 3.3 The Firestore board document holds only status, change time and who may listen
- Verified by: `tests/test_ticks.py`, `tests/test_lunches.py` (live board), `tests/test_parties.py` (live board)

## 4. Errors and messages
- [x] 4.1 `apps.core.errors.exception_handler`: not found and rate limit as sentences
- [x] 4.2 `apps/accounts/messages.py`: what a role doesn't let someone do
- Verified by: `tests/test_permissions.py`, `tests/test_throttling.py`, `tests/test_mcp.py`

## 5. Migrations
- [x] 5.1 One initial migration per app, row-level security recreated in `lunches/0002`; schema compared with the original chain's
- [x] 5.2 Old data is not kept (decided 16 Nov 2026): the `upgrade/` folder with the original steps was removed; `manage.py reset_database` starts an old database over
- [ ] 5.3 Run `manage.py reset_database --yes --allow-production` against production as the owner, then deploy the API and the web app together

## 6. Web app
- [x] 6.1 Roles in Settings, addresses with coordinates and a way to remove them, ticks through the API
- [x] 6.2 `model.ts`, `api/adapt.ts`, `data/people.ts`, `data/catalog.ts`; `Pick.tsx` split; the store's value memoized
- [x] 6.3 Skeletons, an error boundary, a lunch that fails to load says so, friendlier messages
- [x] 6.4 ESLint and Vitest, both in CI
- [x] 6.5 A finished or cancelled lunch leaves the list at once (list copies carry every field; close and cancel refetch); ticks wait for the API; "Next" is disabled while saving and the page only moves on after the answer
- Verified by: `npm test`, `npm run lint`, before-and-after screenshots, and a two-device run against a throwaway API

## 7. UX and speed pass
- [x] 7.1 `notifications.link` removed (`accounts/0003`); the bell's lines open today's lunch
- [x] 7.2 Controls disabled while saving, celebrations and "saved" toasts after the API answers (`use-pending.ts`)
- [x] 7.3 Sign-out dialog; saved addresses can be removed; skeletons; a lunch that fails to load says so
- [x] 7.4 Landing page and signed-in app as separate downloads, library chunks, WebP hero
- [x] 7.5 Landing page: the story film, the six real steps, the board to try inside "Made for the person who orders."
- Verified by: `tests/test_notifications.py`, `tests/test_mcp.py`; in the web app, three end-to-end runs against a throwaway API (two devices; slowed and refused requests; Settings, sign-out and the bell)
