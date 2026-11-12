#!/usr/bin/env bash
# Everything `git commit` will check, run on the whole working tree before you commit,
# plus the two mistakes that keep failing the hook:
#   1. Half-staged work. pre-commit sets aside every unstaged change and tests only what
#      is staged, so a staged file that needs an unstaged one (a new setting, a new test)
#      fails even though your working tree is fine.
#   2. Secrets and pasted shell commands in files that are about to be committed.
# Usage: make ready        (exit 0 means `git commit` will pass)
set -uo pipefail
cd "$(dirname "$0")/.."

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
failed=0
problem() { red "✗ $*"; failed=1; }

bold "1/4 Is everything staged together?"
unstaged=$(git diff --name-only)
untracked=$(git ls-files --others --exclude-standard)
if [ -n "$unstaged" ] || [ -n "$untracked" ]; then
  if [ -n "$(git diff --cached --name-only)" ]; then
    problem "Some changes are staged and some are not. The hook only tests the staged ones."
    [ -n "$unstaged" ] && printf '  not staged:\n%s\n' "$(echo "$unstaged" | sed 's/^/    /')"
    [ -n "$untracked" ] && printf '  new, not added:\n%s\n' "$(echo "$untracked" | sed 's/^/    /')"
    echo "  Fix: git add -A   (or git stash the files you are not committing)"
  fi
else
  green "✓ nothing half-staged"
fi

bold "2/4 Secrets or pasted commands in what would be committed"
files=$(git ls-files -co --exclude-standard -- . ':!tests' ':!scripts/ready.sh' ':!uv.lock')
if git ls-files -co --exclude-standard | grep -E '(^|/)\.env$' >/dev/null; then
  problem "A real .env file would be committed. Keep it out of git."
fi
hits=$(echo "$files" | xargs grep -nIE \
  "npg_[A-Za-z0-9]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY|postgres(ql)?://[^:/@ <'\"]+:[^@ <'\"]{6,}@|^DATABASE_URL='|^DJANGO_SECRET_KEY=[^<x ]{12,}" \
  2>/dev/null | grep -v '\.env\.example' | head -10)
if [ -n "$hits" ]; then
  problem "Looks like a secret or a pasted shell command:"
  echo "$hits" | sed 's/^/    /' | cut -c1-140
else
  green "✓ no secrets or stray commands"
fi

bold "3/4 Backend checks (same as the hook and CI)"
make lint >/tmp/ready-lint.log 2>&1 && green "✓ lint" || { problem "lint"; tail -25 /tmp/ready-lint.log; }
make typecheck >/tmp/ready-types.log 2>&1 && green "✓ mypy" || { problem "mypy"; tail -25 /tmp/ready-types.log; }
make check >/tmp/ready-check.log 2>&1 && green "✓ django checks" || { problem "django checks"; tail -25 /tmp/ready-check.log; }
make security >/tmp/ready-sec.log 2>&1 && green "✓ bandit and pip-audit" || { problem "security"; tail -25 /tmp/ready-sec.log; }
make test >/tmp/ready-test.log 2>&1 && green "✓ tests ($(grep -E 'passed' /tmp/ready-test.log | tail -1))" \
  || { problem "tests"; grep -E "^FAILED|Error|passed|failed|Required test coverage" /tmp/ready-test.log | head -25; }

bold "4/4 Web app (../lunch-board), if it has changes"
web="../lunch-board"
if [ -d "$web/.git" ] && [ -n "$(git -C "$web" status --short -- src public vercel.json package.json)" ]; then
  (cd "$web" && npx tsc --noEmit >/tmp/ready-tsc.log 2>&1 && npx vite build >/tmp/ready-vite.log 2>&1) \
    && green "✓ lunch-board type check and build" \
    || { problem "lunch-board does not build"; tail -15 /tmp/ready-tsc.log /tmp/ready-vite.log; }
  echo "  (lunch-board is its own git repo: commit it there too)"
else
  echo "  no changes there"
fi

echo
if [ "$failed" -eq 0 ]; then
  green "READY: git commit will pass."
else
  red "NOT READY: fix the ✗ items above, then run make ready again."
  exit 1
fi
