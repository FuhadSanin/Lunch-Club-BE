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
apps/accounts/   companies, people, Firebase sign-in, roles and permissions, addresses
apps/lunches/    lunches, picks, completing or skipping, locking, live board
apps/ordering/   confirm, place exactly once, hand-off to host, delivery; ordering providers
apps/catalog/    Swiggy restaurants, menus and dishes (Swiggy's public web endpoints, cached briefly)
tests/           one test per spec scenario
openspec/        specs and changes
```

Each app keeps business rules in `services.py`; views only parse input and shape output.

## API

All endpoints take `Authorization: Bearer <Firebase ID token>`. Money is in paise.

Access is by permission, and people get permissions through roles. A role belongs to a
company and is a named set of permissions (the `roles`, `role_permissions` and `user_roles`
tables); a person has any number of roles and may do whatever they grant together, so an
"Auditor" with `session_read` and `member_read` sees lunches and the team and can do nothing
else. Each company endpoint names the permission it needs (the `required_permissions` of its
view), and a method that names none is refused. Every company has two built-in roles that
can't be changed: **Host** (every permission, given to whoever sets the company up) and
**Member** (`session_read`, `pick_create`, `pick_update`, `pick_delete`, `restaurant_read`,
`member_read`, given to people who join). Anyone with `member_update` can make roles and
give them to people, granting or removing only permissions they hold themselves, and a
company always keeps at least one person with `member_update`. The rules live in
`apps/accounts/rbac.py`. `GET /api/permissions` lists every permission.

| Method | Path | Permission | What |
|---|---|---|---|
| GET | `/api/me` | signed in | You, your org (`org: null` until set up or until a public-email person hosts), `org.kind` (`company` or `personal`), `public_email`, your roles, and the permissions they give |
| POST | `/api/me/push-token` | in a company | Subscribe this device to company notifications |
| POST | `/api/company` | no company yet | Set up your company; you get the Host role (public emails like Gmail can't: they host a personal party instead) |
| GET | `/api/permissions` | in a company | Every permission and what it allows |
| GET | `/api/company/members` | `member_read` | Everyone in the company, with their roles and permissions |
| PUT | `/api/company/members/<id>/roles` | `member_update` | Set someone's roles: `{"roles": [role ids]}` |
| GET | `/api/company/roles` | `member_read` | The company's roles: `name`, `system`, `permissions`, `members` |
| POST | `/api/company/roles` | `member_update` | Make a role: `{"name": "Auditor", "permissions": [...]}` |
| PATCH, DELETE | `/api/company/roles/<id>` | `member_update` | Rename a role or change its permissions; delete one nobody has (Host and Member can't change) |
| POST | `/api/company/addresses` | `company_update` | Add an address to the host's list: `address`, and `lat`, `lng` if picked from the place suggestions |
| DELETE | `/api/company/addresses/<id>` | `company_update` | Take an address off the list (lunches that went there keep it) |
| GET | `/api/restaurants?lat=&lng=[&q=]` | `restaurant_read` | Swiggy restaurants near a place, or matching `q` |
| GET | `/api/restaurants/<id>/menu?lat=&lng=` | `restaurant_read` | A restaurant's details and full menu (prices in paise) |
| GET | `/api/dishes?lat=&lng=&q=` | `restaurant_read` | Dishes by name, each with its restaurant |
| GET | `/api/places?q=[&lat=&lng=]` | `restaurant_read`, or a public email | Place suggestions while typing an address (OpenStreetMap: Photon) |
| GET | `/api/places/<place_id>` | `restaurant_read`, or a public email | The address and `lat`/`lng` of a suggested place |
| GET | `/api/lunches` | `session_read` | Last 30 lunches: your org's and the ones you joined (`joined: true`) |
| POST | `/api/lunches` | `session_create` | Start a lunch (a company runs one at a time: 409 while one isn't closed or called off): `cutoff_at`, `address` (`{address, lat, lng}`, kept in the company's addresses; the lunch points at it), optional `name`, `offered_restaurants`. A public-email person with no org gets a personal party made for them |
| GET | `/api/lunches/<id>` | `session_read` | The full board (what a shared link opens) |
| POST | `/api/lunches/<id>/join` | signed in | Join a personal party's lunch from its link (a company's lunch only its own people); answers with the board |
| POST | `/api/lunches/<id>/lock` | `session_update` | Close picks before the cutoff |
| PUT | `/api/lunches/<id>/ticks` | `session_update` | Tick or untick what the host tracks while running the lunch: `{kind, key, done}`, where `kind` is `reviewed_restaurant`, `reviewed_person`, `arrived` or `handed_out` and `key` is the restaurant's name or the person's id. Kept on the lunch, so every device shows the same; changing a pick clears its review ticks |
| POST | `/api/lunches/<id>/cancel` | `session_delete` | Call off a lunch not yet confirmed |
| POST | `/api/lunches/<id>/items` | `pick_create` | Pick a dish |
| PATCH | `/api/lunches/<id>/items/<item_id>` | `pick_update` | Change your pick (only your own) |
| DELETE | `/api/lunches/<id>/items/<item_id>` | `pick_delete` | Drop your pick (only your own) |
| PUT, DELETE | `/api/lunches/<id>/skip` | `pick_update` | Skip the lunch (completed with nothing picked; only the host can reopen it) |
| POST | `/api/lunches/<id>/confirm` | `review_create` | Approve the reviewed cart; orders go out |
| POST | `/api/orders/<id>/placed` | `order_update` | Enter the platform's order number (`platform_order_id`) after checking out yourself |
| POST | `/api/orders/<id>/delivered` | `order_update` | Mark food arrived |
| POST | `/internal/tasks/<job>` | Cloud Tasks only | `lock-lunch`, `place-order`, `poll-delivery` |
| GET | `/healthz` | anyone | Liveness |

The lunch's host is still the person who started it: they get the emails about it.

**Companies and personal parties.** People with a company email domain share a company:
the first to set it up has the Host role, and people who join get the Member role
(they pick food but can't start lunches or change settings). People with a
public email (`PUBLIC_EMAIL_DOMAINS`: Gmail and the like) never share an org: each gets
a personal party when they first start a lunch, and others reach that lunch by joining
it from its link. A joined person has the member set on that one lunch only; the
database shows them nothing else (row-level security by org, or by membership).

**Errors** are sentences a person can act on (`apps/core/errors.py`,
`apps/accounts/messages.py`): what a role doesn't allow and who can change it, something
that isn't there, when to try again after a rate limit.

**An existing database** made before the migrations were squashed can't be brought up to
date by a plain `migrate`. Its old data isn't kept: start it over, as the database's
owner, with `python manage.py reset_database --yes` (add `--allow-production` when
`DJANGO_DEBUG` is off). It drops every table and builds today's schema.

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

## MCP server (use Lunch Club from an AI assistant)

`mcp_server/` lets an AI assistant (Claude Code, Claude Desktop, Cursor, ...) run a lunch
for one person: start it from a prompt, share the link, join and pick dishes, lock and
confirm, record orders. It is a thin layer over the REST API: every tool makes the same
calls the web app does, as the signed-in person (the access token their assistant got when they pressed Allow), so it can do
only what that person can. 23 tools (`whoami`, `start_lunch`, `share_lunch`, `join_lunch`,
`find_restaurants`, `get_menu`, `pick_dish`, `lock_lunch`, `confirm_lunch`, ...) and two
prompts (`start_team_lunch`, `pick_my_lunch`). `confirm_lunch` and `cancel_lunch` refuse
until called with `confirm=true`, which the assistant should only do after asking.

**Signing in (the callback flow).** The person adds the server's address to their assistant
and clicks Connect; nothing is copied or pasted. The assistant finds where to sign in
(`/.well-known/oauth-protected-resource` on the MCP server, then
`/.well-known/oauth-authorization-server` on the API), registers itself (`POST /oauth/register`),
and opens `/oauth/authorize`, which sends the browser to the web app's consent page
(`#/authorize/<id>`). The person, already signed in with Google, presses Allow; the browser
returns to the assistant's callback with a one-time code, which the assistant trades at
`/oauth/token` (PKCE, S256, required) for an hour-long access token and a refresh token. The
authorization server is the API (`apps/oauth`); the MCP server only checks tokens and points
unauthenticated calls at the sign-in (401 with `WWW-Authenticate`). Safety: redirect
addresses must be https or loopback and match what was registered exactly; codes live two
minutes and work once (using one twice cancels what it gave out); refresh tokens are
replaced on every use, and one used twice revokes the whole line; the consent screen shows
where the person will be sent back to; and the token can do only what any access token can
(lunches, restaurants, places, orders, notifications). People see and end connections in
Connect to an AI assistant (they appear in its list under the app's name, with a Disconnect
button). Signing in is the only way to connect: there are no tokens to make or copy. Only a
token's SHA-256 is stored.

**On Vercel, with the API (the setup in use).** `config/mcp_mount.py` serves `/mcp` and
`/.well-known/oauth-protected-resource` from the API's own WSGI app, so there is no second service. The web
app forwards its own `/mcp` to the API (`lunch-board/vercel.json`), so people and assistants only see
`https://<web>/mcp`. Needs `API_PUBLIC_URL` (the API's address) and `APP_URL` (the web app) set on the API's
project; the server is the same code as below, started for each request (it keeps no state).

**Run it** (needs `uv sync --group mcp`; the web link and API come from environment
variables):

```bash
# for developing the server (stdio): one person, an access token from a sign-in in the environment
LUNCHCLUB_API_URL=https://<api> LUNCHCLUB_WEB_URL=https://<web> LUNCHCLUB_TOKEN=lc_... \
  uv run --group mcp python -m mcp_server

# as a shared web service (streamable HTTP at /mcp): each caller sends their own
# `Authorization: Bearer lc_...`; the server keeps none. No token in the environment is
# ever used for a web caller.
MCP_ALLOWED_HOSTS=<this host> uv run --group mcp python -m mcp_server --http --port 8080
docker build -f Dockerfile.mcp -t lunchclub-mcp .      # the same, as a small image
```

Connect a client by signing in: `claude mcp add --transport http lunchclub
https://<mcp-host>/mcp`, then `/mcp` in Claude Code and Authenticate; in claude.ai or
Claude Desktop, Settings, Connectors, Add custom connector, paste the address. That is the one
way to connect; the web app's Connect to an AI assistant page explains it with examples and lists
the tools.

Settings. **API**: `API_PUBLIC_URL` (its own public address, the sign-in "issuer";
default: from the request), `APP_URL` (the web app, where the consent page lives),
`OAUTH_ACCESS_TOKEN_SECONDS` (3600), `OAUTH_REFRESH_TOKEN_DAYS` (30). **MCP server**:
`LUNCHCLUB_API_URL`, `LUNCHCLUB_WEB_URL`, `MCP_PUBLIC_URL` (its own public address; set it
when behind a proxy), `MCP_AUTH_ISSUER` (default: the API), `MCP_ALLOWED_HOSTS`,
`LUNCHCLUB_TZ` (default Asia/Kolkata, for times like "12:30").

## Security hardening

What the code does for you, and the few steps only you can do (they are in Neon, Vercel
and Google Cloud).

**Done in code**
- **Rate limits** (`apps/core/throttling.py`): 600 requests a minute per signed-in person,
  and tighter ones for place search (60), restaurant browsing (60), joining by link (20).
  Change them with `THROTTLE_USER`, `THROTTLE_CATALOG`,
  `THROTTLE_PLACES`, `THROTTLE_JOIN`. With several server instances
  (Vercel) set `THROTTLE_CACHE=database` so they share one count; if that table is ever
  missing the limit is skipped rather than breaking requests.
- **The host can close the share link** to new people (`PUT /api/lunches/<id>/joining`),
  and the link stops letting new people in at the cutoff or once picks lock.
- **Notification devices** are capped at 10 per person (the least recently seen go first).
- **Allowed hosts**: `make check` warns if `DJANGO_ALLOWED_HOSTS` has a wildcard
  (`.vercel.app`, `*`).
- **Secrets**: `scripts/check_secrets.sh` runs in the commit hook, in `make ready` and in CI.
- **Dependencies**: Dependabot opens one grouped pull request a month; CI runs the security
  audit on every push.

**Do once: a database role that can't skip row-level security.** Neon's default
`neondb_owner` can, so company isolation would rest on the code's own filtering alone.
1. Run as the owner: `DATABASE_URL='<owner url>' DJANGO_SECRET_KEY=x DJANGO_DEBUG=false
   uv run python manage.py create_app_role` (it prints the new role's URL once).
2. Put that URL (pooled host) in Vercel as `DATABASE_URL`. Keep the owner's URL for
   `migrate` only.
3. Check it: `... manage.py check_rls` says the role is subject to row-level security.
4. Set `REQUIRE_RLS=true` in Vercel, so the app refuses to start as a role that can skip it.

**Do once: secrets.** Reset the Neon password (Neon, Branch, Roles, Reset password) if it
has ever been in a chat or a terminal. Keep `FIREBASE_CREDENTIALS_JSON`,
`GEOAPIFY_API_KEY` and `DJANGO_SECRET_KEY` as Vercel secrets only.

**Do once: restrict the Firebase web API key.** Google Cloud Console, APIs & Services,
Credentials, open the "Browser key" (the one in `lunch-board/src/lib/firebase.ts`),
Application restrictions: Websites, add `https://lunchclubfe.vercel.app/*` and
`http://localhost:5173/*`. Under API restrictions allow only Identity Toolkit, Token
Service, Firebase Installations and Firebase Cloud Messaging Registration.
