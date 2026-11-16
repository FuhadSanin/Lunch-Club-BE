#!/usr/bin/env bash
# Stops a password, key or pasted shell command from being committed. Used by the commit
# hook (on the staged files), by CI (on every file) and by `make ready`.
#   scripts/check_secrets.sh               every file git would commit
#   scripts/check_secrets.sh a.py b.ts     just these
# Exit 1 and a list of the lines when something looks like a secret.
set -uo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -gt 0 ]; then
  files=$(printf '%s\n' "$@")
else
  files=$(git ls-files -co --exclude-standard)
fi

# A real .env would carry every secret at once.
if echo "$files" | grep -E '(^|/)\.env$' >/dev/null; then
  echo "✗ A real .env file would be committed. Keep it out of git."
  exit 1
fi

# Tests hold made-up credentials; the lock file has hashes; this file has the patterns.
scan=$(echo "$files" | grep -vE '^(tests/|uv\.lock$|scripts/check_secrets\.sh$|.*\.env\.example$|.*\.env\.production\.example$)' || true)
[ -z "$scan" ] && exit 0

hits=$(echo "$scan" | while read -r f; do [ -f "$f" ] && echo "$f"; done | xargs grep -nIE \
  "npg_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|\"private_key\": *\"-----BEGIN|postgres(ql)?://[^:/@ <'\"]+:[^@ <'\"\$\{]{6,}@|^DATABASE_URL='|^DJANGO_SECRET_KEY=[^<x ]{12,}|GEOAPIFY_API_KEY=[A-Za-z0-9]{16,}" \
  2>/dev/null | head -10)

if [ -n "$hits" ]; then
  echo "✗ Looks like a secret or a pasted shell command:"
  echo "$hits" | sed 's/^/    /' | cut -c1-140
  echo "  Remove it. If it was a real password, reset it: it may already be in your shell history or chat."
  exit 1
fi
