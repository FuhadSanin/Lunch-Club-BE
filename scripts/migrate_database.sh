#!/usr/bin/env bash
# Copy the whole database to a new Postgres (for example a new Neon project in another region), then make the
# app's row-level-security role there and check the copy. Nothing is changed in the old database.
#
#   scripts/migrate_database.sh '<OLD owner url>' '<NEW owner url>'
#
# Use the OWNER's URL of each (the role you run migrations with; for Neon, the direct host, not the -pooler one),
# with ?sslmode=require. The new database must be empty (a fresh Neon project is). The steps and the rollback are in
# docs/database-migration.md. You need pg_dump, pg_restore and psql, at least as new as the old server
# (macOS: brew install libpq, then PATH="$(brew --prefix libpq)/bin:$PATH").
set -euo pipefail

if [ "$#" -ne 2 ]; then
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
OLD="$1"
NEW="$2"
PYTHON="${PYTHON:-.venv/bin/python}"
APP_ROLE="${APP_ROLE:-lunch_app}"
cd "$(dirname "$0")/.."

for tool in pg_dump pg_restore psql; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Install the Postgres client tools (see the top of this file)."; exit 1; }
done

server_major() { psql "$1" -Atc 'SHOW server_version_num' | cut -c1-2; }
client_major=$(pg_dump --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')
old_major=$(server_major "$OLD")
new_major=$(server_major "$NEW")
echo "Old server: Postgres $old_major. New server: Postgres $new_major. Client tools: $client_major."
if [ "$client_major" -lt "$old_major" ] || [ "$client_major" -lt "$new_major" ]; then
  echo "pg_dump is older than a server. Install newer client tools."; exit 1
fi
if [ "$new_major" -lt "$old_major" ]; then
  echo "The new server is older than the old one. Create the new project with Postgres $old_major or newer."; exit 1
fi

tables_in_new=$(psql "$NEW" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
if [ "$tables_in_new" != "0" ]; then
  echo "The new database already has $tables_in_new tables. Use an empty one."; exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
echo "1/5 Dumping the old database (this reads it; it does not change it)..."
pg_dump "$OLD" --format=custom --no-owner --no-privileges --schema=public --file "$work/db.dump"
echo "    $(du -h "$work/db.dump" | cut -f1) dumped."

echo "2/5 Restoring into the new database..."
# a new database already has its public schema, so leave that one entry out of the restore
pg_restore --list "$work/db.dump" | grep -v ' SCHEMA - public ' > "$work/restore.list"
pg_restore --no-owner --no-privileges --exit-on-error --use-list "$work/restore.list" --dbname "$NEW" "$work/db.dump"

echo "3/5 Making the app's database role (no superuser, cannot skip row-level security)..."
APP_PASSWORD=$(openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-40)
DATABASE_URL="$NEW" DJANGO_SECRET_KEY=x DJANGO_DEBUG=false "$PYTHON" manage.py create_app_role --name "$APP_ROLE" --password "$APP_PASSWORD" >/dev/null
# the new database's address, with the app role in place of the owner
APP_URL=$(python3 - "$NEW" "$APP_ROLE" "$APP_PASSWORD" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit
url, role, password = sys.argv[1:4]
p = urlsplit(url)
print(urlunsplit(p._replace(netloc=f"{role}:{password}@{p.hostname}" + (f":{p.port}" if p.port else ""))))
PY
)

echo "4/5 Checking the role and the migrations..."
DATABASE_URL="$APP_URL" DJANGO_SECRET_KEY=x DJANGO_DEBUG=false "$PYTHON" manage.py check_rls
DATABASE_URL="$NEW" DJANGO_SECRET_KEY=x DJANGO_DEBUG=false "$PYTHON" manage.py migrate --check

echo "5/5 Comparing the number of rows in every table..."
count_sql="SELECT string_agg(format('SELECT %L AS t, count(*) AS n FROM public.%I', tablename, tablename), ' UNION ALL ' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'public'"
counts() { psql "$1" -Atc "$(psql "$1" -Atc "$count_sql") ORDER BY t"; }
if diff <(counts "$OLD") <(counts "$NEW") >/dev/null; then
  echo "    Every table has the same number of rows in both."
else
  echo "    DIFFERENT row counts (old on the left, new on the right):"
  diff <(counts "$OLD") <(counts "$NEW") || true
  echo "    If someone used the app during the copy, small differences are expected: run it again at a quiet time."
  exit 1
fi

echo
echo "Done. The new database is a copy. Now point the app at it (see docs/database-migration.md)."
echo "Put this in Vercel as DATABASE_URL (for Neon, add -pooler to the host name, as in your old URL). It is shown only now:"
echo "  $APP_URL"
