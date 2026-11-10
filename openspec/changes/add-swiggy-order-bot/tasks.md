# Tasks

## 1. Linking the host's Swiggy account
- [ ] 1.1 `PUT /api/me/swiggy` starts a login with the host's phone number; `POST` with the OTP finishes it
- [ ] 1.2 Save the storage state to Secret Manager as `swiggy-session-<host id>`
- [ ] 1.3 `DELETE /api/me/swiggy` unlinks (deletes the secret)
- Verified by: tests with a fake browser; manual link with the pilot host's account

## 2. Checkout
- [ ] 2.1 `SwiggyWebProvider.place`: open with saved state, set address, add each cart line, add the one-time key to the instructions, check the total, pay
  - Done and checked on the live site: open each restaurant, add each line (idempotent), confirm the cart; then hands the order to the host. Still to do: address, one-time key, total check, pay (`apps/ordering/providers/swiggy_web.py`)
- [ ] 2.2 Raise `HostActionRequired` when the login expired, an item is unavailable or the total differs from the lunch
  - Done for signed-out, dish not found/unavailable, options needed, cart mismatch; total check still to do
- [ ] 2.3 Record a screenshot of the final page to Cloud Storage for every attempt
- Verified by: recorded-site tests (Playwright HAR replay); one supervised live order

## 3. Double-order check and delivery
- [ ] 3.1 `find_existing`: search recent orders for the one-time key
- [ ] 3.2 `is_delivered`: read the order's status page; set `tracks_delivery = True`
- Verified by: HAR replay of an interrupted checkout and of a delivered order

## 4. Deployment
- [ ] 4.1 `order-bot` Cloud Run service with Playwright's Chromium image, concurrency 1
- [ ] 4.2 Route `place-order` and `poll-delivery` jobs to it (`TASKS_BASE_URL` per job)
- [ ] 4.3 Switch `ORDER_PROVIDER` for our own company only during the pilot
