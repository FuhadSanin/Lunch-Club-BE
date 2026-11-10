# Tasks

## 1. Foundation
- [x] 1.1 Django project with settings from environment variables, JSON logging, Sentry
- [x] 1.2 Background jobs: Cloud Tasks with OIDC-verified internal endpoint, inline locally
- [x] 1.3 Dockerfile for Cloud Run

## 2. Accounts (tests/test_accounts.py)
- [x] 2.1 Organization and User models with migration
- [x] 2.2 Firebase ID token authentication; first sign-in joins by email domain
- [x] 2.3 Set up a company; members list; role changes with the last-admin guard
- [x] 2.4 Device subscription to company push notifications

## 3. Company isolation (tests/test_isolation.py)
- [x] 3.1 Row-level security policies on the four lunch tables
- [x] 3.2 Every company request and job scopes its transaction to one company
- [x] 3.3 Policies verified on PostgreSQL 16 as a non-superuser owner

## 4. Lunches (tests/test_lunches.py)
- [x] 4.1 Lunch, pick and Swiggy order models with migration
- [x] 4.2 Start, pick, edit, remove, skip, unskip, cancel endpoints
- [x] 4.3 Lock at cutoff (job) or early (host); split into checkouts of at most 5 restaurants
- [x] 4.4 Live board written to Firestore after commit; firestore.rules

## 5. Ordering (tests/test_ordering.py)
- [x] 5.1 Provider interface and manual provider
- [x] 5.2 Confirm; exactly-once place job with lease and existing-order check
- [x] 5.3 Hand-off to host on failure; host records order number and delivery
- [x] 5.4 Delivery polling for providers that track it; lunch status roll-up
- [x] 5.5 Push and email notifications at start, lock, hand-off, ordered, delivered
