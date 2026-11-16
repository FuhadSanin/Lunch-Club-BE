# Design: Roles, one addresses table, and the host's ticks on the server

Each decision says what was chosen, why, and what was turned down.

## Roles and permissions

### Decision: Our own role tables, not Django's groups and permissions
`roles (org, name, is_system)`, `role_permissions (role, permission)` and
`user_roles (user, role)`. Django's `Group` and `Permission` aren't scoped to a company
and need `django.contrib.auth`, which this project doesn't use (people sign in with
Firebase, and `User` is our own model). A company must be able to have its own "Auditor"
without another company seeing or sharing it.

### Decision: Permissions stay a fixed list in code
`role_permissions.permission` holds a code such as `session_read`, checked against the
`Permission` list. A permission only means something when an endpoint asks for it, so a
table of them would let people invent ones nothing checks.

### Decision: Host and Member are built in and can't be changed
Every company gets Host (every permission) and Member (picking food) when it is made.
The app relies on them: whoever sets a company up is a Host, whoever joins is a Member,
and a guest in someone's party has what Member has. Custom roles carry everything else.

### Decision: The rules live in one module, behind a lock on the company
`apps/accounts/rbac.py`: nobody grants or removes a permission they don't hold; someone
always keeps `member_update`; a role people have can't be deleted; a person only gets
roles of their own company. Every change first locks the company's row, so two people
changing roles at once run one after the other. (Before, each person's row was locked;
with roles a change can affect many people, so one lock per company is simpler and
can't deadlock.)

### Decision: A person's permissions are read once per request
`User.permissions` is one query (or the prefetched roles when listing people), cached on
the object until it is refreshed. Not stored on the user: a copy would have to be
rewritten for everyone whenever a role changes.

## Data that was stored twice

### Decision: One `addresses` table, and a lunch points at it
`addresses (org, address, lat, lng, saved)`. The lunch has `address_id`. The same text in
any case is the same address, so a lunch to "tower a" reuses "Tower A" and gives it
coordinates if it had none. Turned down: keeping the text on the lunch "for history". The
foreign key is `PROTECT`, so the row can't disappear under a lunch.

### Decision: `saved` instead of deleting
Taking an address off the host's list sets `saved = false` when a lunch went there, and
deletes it when none did. That keeps the list short without losing where a past lunch was
delivered.

### Decision: The company has no office point and no lunch defaults
`office_lat`, `office_lng`, `restaurant_mode` and `radius_km` are gone. Nothing on the
server read them; the web app never set the office point, and the other two only
pre-filled the Start a lunch form. Restaurants are looked up near the address a lunch is
delivered to, and each lunch keeps the restaurants it offered.

### Decision: No skips table
A skip removes a person's picks and completes their order. So "skipped" is exactly
"completed with no dishes", and the board works it out. The completions table stays:
"this person is done" can't be worked out from their dishes.

### Decision: `Order`, not `SwiggyOrder`
The checkout, its status and its order number are the same whichever platform places it.
Swiggy-specific code stays in `apps/ordering/swiggy_web.py` and its provider.

## Running a lunch

### Decision: The host's ticks are four lists on the lunch
`reviewed_restaurants`, `reviewed_people`, `arrived_restaurants`, `handed_out_people`
(Postgres arrays), changed through `PUT /api/lunches/<id>/ticks` with
`{kind, key, done}`. Turned down: a table of ticks (more to join for what is a handful of
values read with every board) and one JSON column (untyped, and four named columns say
what they hold). The lunch's row is already locked for every change, so two hosts
ticking at once don't lose each other's ticks.

### Decision: Restaurants are ticked by name, people by id
Picks store a restaurant's name, not an id every browser agrees on (a browser that hasn't
loaded a menu makes a stand-in id). The name is the one thing the server and every
browser share. Only a restaurant someone picked from, or a person with dishes, can be
ticked; unticking never fails.

### Decision: Changing a pick clears its review ticks
The host ticked what they saw. If a person's picks change, their tick and the ticks of the
restaurants in their old and new picks are cleared, so the host looks again.

### Decision: One lunch at a time, enforced by the API
A company can't start a lunch while one isn't called off or closed (409 naming it). The
web app already hid the button; the API now holds the rule for every client, and locks the
company's row so two hosts starting at the same moment can't both succeed.

### Decision: The live board is a signal, not a copy
Firestore's `boards/<lunch id>` holds `status`, `changed_at`, `org_id` and `member_uids`.
Browsers only ever used it to know that something changed, then fetched the board from
the API. Copying picks, names and emails there cost a full board query per change and put
personal data in a second place for no reader.

## Errors and messages

### Decision: One place turns framework errors into sentences
`apps.core.errors.exception_handler`: a lookup that finds nothing says "We couldn't find
that…" instead of naming the model, and a rate limit says when to try again (keeping
`Retry-After`). Messages the services write themselves pass through unchanged.

### Decision: "Your role doesn't let you …"
A refusal says what the person can't do in the app's words, that a host can change their
role, and the permission's code in brackets for whoever is asked
(`apps/accounts/messages.py`).

## Migrations

### Decision: Squash to one initial migration per app, keep the original steps aside
`apps/*/migrations` build today's schema from nothing; row-level security is recreated by
hand in `lunches/0002`. The two schemas were compared fact by fact (columns, constraints,
indexes, policies) and were identical. The original steps, which moved existing data into
the new shape, were kept aside in `upgrade/` at first and then removed: the old data
wasn't needed, so a database from before the squash is started over with
`manage.py reset_database` instead.

## Smaller decisions

- **Place search** keeps one HTTP client with open connections (a new TLS handshake per
  keystroke was more than a third of the wait), and gives up on a dead server after 2 s.
- **Notifications** are sorted newest first in the query; before, the order depended on
  which index Postgres chose.

## Decisions from the UX and speed pass

### Decision: A notification has no link of its own
`notifications.link` is gone. Every line in the bell is about the lunch the person is in,
so tapping one marks it read and shows that lunch. The push still carries the lunch's
link, because tapping a push has to open the app somewhere.

### Decision: A control is disabled until its request has answered, and the celebration waits for it
Every button and tick that saves something is disabled (and says "Saving…", "Locking…")
while its request is on its way, so a double click sends one request. What follows a save
(a celebration, a "saved" toast) happens when the API has answered, never on the click;
if the API refuses, there is no celebration and the reason shows as a toast. One hook holds
this (`src/lib/use-pending.ts`). The host's ticks are not optimistic either: a tick shows when
the API has answered, one at a time in the order they were clicked, and while any change is on its
way the "Next" bar says "Saving…" and is not a link, and the page does not move to the next step by
itself. A step's pop-up and the move to the next step therefore both follow the API's answer.
Closing a lunch used to celebrate even when the close failed; it no longer does. Completing your order
is the strictest: it is final, so the screen does not show it as complete either (no optimistic update)
until the API has answered 200; saving a pick without completing still shows at once and rolls back.

### Decision: Finishing or cancelling a lunch refetches the list
The lunch list holds a short copy of each lunch. A copy made from the board answer is built from
every field the list has (the type makes a missing one a compile error); it used to drop `closed_at`,
so a lunch the host had just finished still counted as running. Closing and cancelling now also
fetch the list again, so the "Active party" card, "Finish party" and the one-lunch-at-a-time
check go away as soon as the API has answered.

### Decision: Signing out asks first
The sign-out icon is small and sits next to the theme switch, so it opens a dialog
("Sign out of Lunch Board?") instead of signing out at once.

### Decision: Two downloads, one per kind of visitor
The landing page and the signed-in app (`src/Shell.tsx`) are separate files, and the
libraries (React, Firebase, the animation and UI libraries) are their own cacheable
files. The app guesses which one is needed from the last visit and starts fetching it
while Firebase is still checking who is signed in. The hero illustration is WebP, with a
half-size file for phones. On a simulated slow phone the landing page's largest paint
went from 5.6 s to 4.5 s, and first-load images from 634 kB to about 115 kB.

### Decision: The landing page follows the demo site, plus the story
Hero, "The story." (the two-minute promo film, fetched only when played), "How lunch
runs." with the app's real six steps, "Made for the person who orders." with the board to
try, and the closing call. "Any restaurant. One cart." was dropped, as on the demo site.

### Decision: A same-company caller reads a lunch's people directly
The board's list of people switched the database's company scope and back (three round
trips) even for someone already in that company. It now does so only for a guest: the
board is 11 queries instead of 14.

## The web app (`../lunch-board`)

- **Roles**: Settings → Members gives the Host or Member role. Making custom roles is
  API-only for now.
- **One store, two sources**: every screen reads `useStore()`. `src/model.ts` holds the
  shapes; `src/api/adapt.ts` turns API answers into them; `src/live.tsx` fills the store
  from the API. Sample data (`data/demo.ts`, `data/menus.ts`) is only sample data; lookups
  by id are in `data/people.ts` and `data/catalog.ts`.
- **Ticks** show at once and are taken back if the API refuses them. A lunch is polled
  until its host closes it (it used to stop when the food arrived, which would have hidden
  hand-out ticks from a second device).
- **Loading and errors**: screens are fetched on demand and ahead of time once the app is
  idle; while waiting, a skeleton in the page's own layout; a screen that crashes shows a
  message with Reload; a lunch that fails to load says so instead of "no lunch today"; no
  bare status codes.
- **Pick my lunch** is split into parts under `screens/pick/`; the cart shows three dishes
  and scrolls for more, so the note and Complete my order stay in view.
- **Checks**: `npm run lint` (ESLint), `npm test` (Vitest on the plain functions), both in CI.
