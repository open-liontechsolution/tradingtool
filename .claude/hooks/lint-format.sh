#!/usr/bin/env bash
# PostToolUse hook (Edit|Write): auto-format/lint the edited file.
#
# Reads the hook JSON from stdin, extracts .tool_input.file_path, and runs the
# project's linter for that file type using LOCAL binaries only — never npx from
# the registry, so no supply-chain surface is added by this hook.
#   .py            -> .venv/bin/ruff check --fix + ruff format
#   .js/.jsx/...   -> frontend/node_modules/.bin/eslint --fix (only under frontend/)
#
# Best-effort: always exits 0 so a lint failure never blocks the edit. The edit
# already landed; this just keeps it CI-clean.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
file="$(jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -z "$file" ] && exit 0
[ -f "$file" ] || exit 0

case "$file" in
  *.py)
    [ -x "$ROOT/.venv/bin/ruff" ] || exit 0
    "$ROOT/.venv/bin/ruff" check --fix "$file" >/dev/null 2>&1
    "$ROOT/.venv/bin/ruff" format "$file" >/dev/null 2>&1
    ;;
  "$ROOT"/frontend/*.js|"$ROOT"/frontend/*.jsx|"$ROOT"/frontend/*.ts|"$ROOT"/frontend/*.tsx)
    eslint="$ROOT/frontend/node_modules/.bin/eslint"
    [ -x "$eslint" ] || exit 0
    ( cd "$ROOT/frontend" && "$eslint" --fix "$file" >/dev/null 2>&1 )
    ;;
esac
exit 0
