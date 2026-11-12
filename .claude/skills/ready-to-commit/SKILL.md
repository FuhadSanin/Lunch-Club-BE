---
name: ready-to-commit
description: Get lunch-backend (and lunch-board) ready to commit so the pre-commit hook passes first time. Use whenever the user asks to commit, says the commit hook failed (ruff, mypy, tests, coverage), or finishes a change and wants it committed.
---

# Ready to commit

The commit hook (`.pre-commit-config.yaml`) runs ruff, ruff format, mypy, Django checks,
bandit + pip-audit and the tests with an 80% coverage floor. It fails for two reasons that
have nothing to do with the code being wrong:

1. **Half-staged work.** pre-commit sets aside every *unstaged* change and tests only the
   *staged* snapshot. If `places.py` is staged but the new setting it reads in
   `config/settings.py` is not, the hook fails with `no attribute ...` even though the
   working tree is fine. Always stage the whole change together.
2. **Pasted text.** A shell command or a connection string pasted into a source file
   (this has happened with `DATABASE_URL='postgresql://...'`) breaks the build and leaks
   a password.

## Steps

1. **Look first:** `git status --short`. Note anything unexpected (scratch files, `.env`,
   a file you did not mean to touch). Never commit `.env`, secrets, or `__pycache__`.
2. **Stage everything that belongs to the change, together:** `git add -A`
   (or `git add <paths>` for all of it, then `git stash` the rest on purpose).
   Do not leave a mix of staged and unstaged files.
3. **Run the full check:** `make ready` (runs `scripts/ready.sh`). It checks, in order:
   half-staged files, secrets and stray pasted commands, lint, mypy, Django checks,
   bandit/pip-audit, tests + coverage, and the `../lunch-board` type check and build.
   It takes a minute or two (pip-audit and the tests are the slow parts); run it in the
   background if needed and wait for it, do not start a second copy.
4. **Fix every `✗`** it prints, then run `make ready` again until it prints
   `READY: git commit will pass.` Common fixes:
   - `E501` line too long: wrap at 88 columns (`uv run ruff format .` handles most).
   - mypy `call-arg` on `many=True` serializers: `# type: ignore[call-arg]`.
   - Missing setting in tests: it is probably unstaged; `git add -A`.
   - Failing migration/RLS test: new tables need grants in the isolation probe role.
   - Coverage under 80%: add tests, never lower `fail_under`.
   - Secrets hit: remove the line from the file; if a real password was exposed, tell
     the user to rotate it.
5. **Commit** with `git commit -m "<clear message>"` plus the attribution line the harness
   gives. Never use `--no-verify`; if the hook still fails after `make ready` passed,
   read the hook output, it is a real difference (usually an unstaged file: step 2).
   ruff may rewrite files during the hook; if it does, `git add -A` and commit again.
6. **The web app is a separate repo** (`../lunch-board`). If `make ready` says it has
   changes, commit them there too (`git -C ../lunch-board status`), staging only the
   files that belong to the change (it often has unrelated edits).

## Do not

- Do not skip hooks, lower the coverage floor, or exclude files from ruff/mypy to get green.
- Do not run `clear_data` or anything else against the Neon/production database while
  committing. Migrations on Neon are a separate, deliberate step.
- Do not commit scratch scripts or screenshots (they belong in the scratchpad directory).
