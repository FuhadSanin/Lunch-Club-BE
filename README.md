# Group lunch backend

Django API for the multi-restaurant group lunch app: a host starts a lunch, the team picks
dishes from any Swiggy restaurant, picks lock at the cutoff, and the app turns them into
Swiggy orders (at most 5 restaurants per checkout).

Behaviour is specified in [`openspec/specs`](openspec/specs); design decisions are in
[`openspec/changes/archive/2026-11-03-add-group-lunch-backend/design.md`](openspec/changes/archive/2026-11-03-add-group-lunch-backend/design.md).
The order bot is the next change: [`openspec/changes/add-swiggy-order-bot`](openspec/changes/add-swiggy-order-bot).

## Layout

```
config/          settings (environment variables only), URLs, WSGI
apps/core/       base model, background jobs, company scoping (RLS), notifications, JSON logs
apps/accounts/   companies, people, Firebase sign-in, roles
apps/lunches/    lunches, picks, skips, locking, live board
apps/ordering/   confirm, place exactly once, hand-off to host, delivery; ordering providers
apps/catalog/    Swiggy restaurants, menus and dishes (Swiggy's public web endpoints, cached briefly)
tests/           one test per spec scenario
openspec/        specs and changes
```

Each app keeps business rules in `services.py`; views only parse input and shape output.

## API

All endpoints take `Authorization: Bearer <Firebase ID token>`. Money is in paise.

Access is by permission, not role: each company endpoint names the one it needs (the
`required_permissions` of its view), and a method that names none is refused. Whoever sets
up a company gets every permission; people who join get the member set (`session_read`,
`pick_create`, `pick_update`, `pick_delete`, `restaurant_read`, `member_read`). Anyone with
`member_update` can grant or remove permissions they hold themselves, and a company always
keeps at least one person with `member_update`. `GET /api/permissions` lists them all.

| Method | Path | Permission | What |
|---|---|---|---|
| GET | `/api/me` | signed in | You, your company (`org: null` until set up) and your permissions |
| POST | `/api/me/push-token` | in a company | Subscribe this device to company notifications |
| POST | `/api/company` | no company yet | Set up your company; you get every permission |
| GET | `/api/permissions` | in a company | Every permission and what it allows |
| GET | `/api/company/members` | `member_read` | Everyone in the company, with their permissions |
| PUT | `/api/company/members/<id>/permissions` | `member_update` | Set someone's permissions: `{"permissions": [...]}` |
| GET | `/api/restaurants?lat=&lng=[&q=]` | `restaurant_read` | Swiggy restaurants near a place, or matching `q` |
| GET | `/api/restaurants/<id>/menu?lat=&lng=` | `restaurant_read` | A restaurant's details and full menu (prices in paise) |
| GET | `/api/dishes?lat=&lng=&q=` | `restaurant_read` | Dishes by name, each with its restaurant |
| GET | `/api/lunches` | `session_read` | Last 30 lunches |
| POST | `/api/lunches` | `session_create` | Start a lunch: `cutoff_at`, `delivery_address` |
| GET | `/api/lunches/<id>` | `session_read` | The full board (what a shared link opens) |
| POST | `/api/lunches/<id>/lock` | `session_update` | Close picks before the cutoff |
| POST | `/api/lunches/<id>/cancel` | `session_delete` | Call off a lunch not yet confirmed |
| POST | `/api/lunches/<id>/items` | `pick_create` | Pick a dish |
| PATCH | `/api/lunches/<id>/items/<item_id>` | `pick_update` | Change your pick (only your own) |
| DELETE | `/api/lunches/<id>/items/<item_id>` | `pick_delete` | Drop your pick (only your own) |
| PUT, DELETE | `/api/lunches/<id>/skip` | `pick_update` | Skip the lunch, or rejoin |
| POST | `/api/lunches/<id>/confirm` | `review_create` | Approve the reviewed cart; orders go out |
| POST | `/api/orders/<id>/placed` | `order_update` | Enter the Swiggy order number after checking out yourself |
| POST | `/api/orders/<id>/delivered` | `order_update` | Mark food arrived |
| POST | `/internal/tasks/<job>` | Cloud Tasks only | `lock-lunch`, `place-order`, `poll-delivery` |
| GET | `/healthz` | anyone | Liveness |

The lunch's host is still the person who started it: they get the emails about it.

## Run locally

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/) and PostgreSQL 14+.

```bash
cp .env.example .env            # then fill in DJANGO_SECRET_KEY and DB_*
set -a && source .env && set +a
uv sync
createuser lunch && createdb -O lunch lunch   # the app role must NOT be a superuser
uv run python manage.py migrate
uv run python manage.py runserver
```

### With Docker

```bash
cp .env.example .env            # fill in DJANGO_SECRET_KEY and DB_PASSWORD
docker compose up --watch
```

This runs Postgres, the API on http://localhost:8001 and the web app (`../lunch-board`)
on http://localhost:5174. Both mount your code: Django restarts on any `.py` change and
Vite reloads the page; `--watch` also rebuilds the API image when `pyproject.toml` or
`uv.lock` changes. Migrations run on start. Change the ports with `API_PORT`, `WEB_PORT`
and `DB_HOST_PORT`. Set `DEV_AUTH_EMAIL` in `.env` to use the app without Firebase
sign-in; its company is created on start. Tests:
`docker compose exec -e DB_USER=postgres -e DB_PASSWORD=postgres api pytest`.

With `CLOUD_TASKS_ENABLED=false`, jobs that are due run right away and scheduled ones are
skipped, so lock lunches by hand (`POST /api/lunches/<id>/lock`). Firestore and push stay
off until you set `FIRESTORE_ENABLED` / `PUSH_ENABLED` and run
`gcloud auth application-default login`.

## Order bot

The order bot (`ORDER_PROVIDER=apps.ordering.providers.swiggy_web.SwiggyWebProvider`)
fills the host's Swiggy cart in the host's own Chrome. It never pays: the host checks out
in the Swiggy app and enters the order number. Each order card in the web app lists the
bot's steps with the reason for each, and a failed step says why it failed.

1. **Start Chrome with a debug port.** Quit Chrome (Cmd+Q), then run
   `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-swiggy-debug"`.
   *Why:* the bot can only drive a browser that opened this port. Check it at
   `http://127.0.0.1:9222/json/version` (JSON, not an error).
2. **Log in to swiggy.com in that window** and set your delivery address.
   *Why:* the cart must be on your account, or you couldn't check it out.
3. **Run the API on your Mac:** `make bot-api` (stops the Docker API and uses port 8001).
   *Why:* the bot needs to reach your Chrome; a container can't.
4. **Confirm a lunch**, or press **Retry automatic ordering** on an order that needs you.
   *Why:* the bot runs when an order is placed; retry runs it again after you fix a problem.
5. **Check out in the Swiggy app** and enter the order number on the order card.
   *Why:* paying is left to you.

Settings: `SWIGGY_BOT_CDP_URL`, `SWIGGY_BOT_PROFILE_DIR`, `SWIGGY_BOT_HEADLESS`,
`SWIGGY_BOT_REQUIRE_LOGIN`. Install Playwright with `uv sync --group bot`.

## Checks

`make` lists every command. Run `make hooks` once after cloning: each `git commit` then runs
the same checks as CI (`.github/workflows/ci.yml`) and stops the commit if one fails.

| Command | What it checks |
|---|---|
| `make lint` | Ruff: 88-character lines, naming, import order, unused code, complexity under 10, at most 5 arguments, security rules; and formatting |
| `make format` | Fixes what Ruff can and formats the code |
| `make typecheck` | MyPy with the Django and DRF plugins |
| `make check` | `manage.py check`, and `check --deploy` with production settings |
| `make security` | Bandit on the code, pip-audit on the locked dependencies |
| `make test` | pytest with coverage; fails under 80% |
| `make ci` | All of the above |

## Tests

```bash
make test                          # starts the compose database, then runs pytest
DB_USER=postgres uv run pytest     # against your own Postgres
```

The test database role must be a superuser: fixtures write without a company scope
(superusers bypass row-level security) and `test_isolation.py` creates a probe role to
check the policies as a normal user.

## Deploy (Google Cloud, asia-south1)

1. Cloud SQL for PostgreSQL; create a non-superuser role that owns the database.
2. Cloud Tasks queue `lunch-jobs`, and a service account (`TASKS_SERVICE_ACCOUNT`) with
   `roles/run.invoker` on this service.
3. Deploy the image to Cloud Run with the variables from `.env.example`, the Cloud SQL
   connection attached, and secrets from Secret Manager. The runtime service account needs
   Cloud Tasks Enqueuer, `iam.serviceAccountUser` on the task account, Firebase Admin and
   Cloud SQL Client.
4. Run migrations as a Cloud Run job: `python manage.py migrate`.
5. `firebase deploy --only firestore:rules` with `firestore.rules`.
6. In Firebase Hosting, rewrite `/api/**` to the Cloud Run service so the web app calls
   the API on its own origin (no CORS needed).
