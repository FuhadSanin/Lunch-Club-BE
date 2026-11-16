# Proposal: Roles, one addresses table, and the host's ticks on the server

## Why

Three things about the first version got in the way as the app grew:

- **Permissions sat on each person.** Giving someone access meant editing that person's
  list, there was no way to say "this is what an Auditor may do", and two people with the
  same job could drift apart.
- **The same facts were stored in several places.** A lunch repeated its delivery address
  and coordinates, the company had its own office point, and a second list of "office
  addresses" held text with no coordinates. Skips had a table that said nothing the
  completions table didn't. Orders were named after Swiggy, though the model is the same
  for any ordering platform.
- **What a host did while running a lunch stayed in one browser.** Ticks for "reviewed",
  "arrived" and "handed out" were in `localStorage`, so a second host or a second device saw
  none of them. A company could also start two lunches at once, and the live board copied
  everyone's picks and emails to Firestore although browsers only used it as a signal.

## What Changes

- **Roles.** A role is a named set of permissions inside a company (`roles`,
  `role_permissions`, `user_roles`). People get permissions only through roles. Every
  company has Host (everything) and Member, which can't be changed; people with
  `member_update` make others and give them out. New: `GET/POST /api/company/roles`,
  `PATCH/DELETE /api/company/roles/<id>`, `PUT /api/company/members/<id>/roles`. Gone:
  `users.permissions` and `PUT /api/company/members/<id>/permissions`.
- **Addresses.** One `addresses` table (text, `lat`, `lng`, `saved`). A lunch points at its
  address (`order_sessions.address_id`); `delivery_address`, `delivery_lat` and
  `delivery_lng` are gone, and so are the company's `office_lat`, `office_lng`,
  `restaurant_mode` and `radius_km`. Starting a lunch takes `address: {address, lat, lng}`.
- **Skips.** The `order_session_skips` table is gone: skipped means completed with no
  dishes. The board still lists `skipped`.
- **Orders.** `SwiggyOrder` is `Order` (table `orders`), `swiggy_order_id` is
  `platform_order_id`.
- **Who set a company up** is recorded (`organizations.created_by`).
- **The host's ticks** live on the lunch (`reviewed_restaurants`, `reviewed_people`,
  `arrived_restaurants`, `handed_out_people`) and are set with
  `PUT /api/lunches/<id>/ticks`. Changing a pick clears its review ticks.
- **One lunch at a time** is enforced by the API for companies too (409).
- **The live board** document holds only the status, when it changed, and who may listen.
- **Errors** are sentences a person can act on: what a role doesn't allow, something that
  isn't there, a rate limit.
- **Migrations** were squashed to one initial per app. Old data is not carried over:
  `manage.py reset_database` starts a database made before the squash over.

## Impact

- Specs: accounts (roles, addresses), lunches (starting, skipping, one at a time, ticks),
  ordering (order number), live-board (signal only), errors (new).
- **Breaking for the web app**: `/api/me` and members carry `roles`; lunches carry `address`
  instead of `delivery_*`; orders carry `platform_order_id`; company settings lost
  `office_*`, `restaurant_mode` and `radius_km`. The API and the web app deploy together.
- **Existing databases** are started over with `manage.py reset_database` (their old data
  is not kept); a plain `migrate` can't update them.
- Web app (`../lunch-board`): roles in Settings, addresses with coordinates, ticks through
  the API, and the restructuring listed in the design.
