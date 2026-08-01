#!/usr/bin/env bash
# PostToolUse(Edit|Write) — the two cheap thirds of "N tests, ty clean, ruff
# clean", on every Python edit. Measured at 0.12s combined; tests run on Stop.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

file=$(cat | jq -r '.file_path // .tool_input.file_path // empty')
case "$file" in *.py) ;; *) exit 0 ;; esac

fail() { printf '%s failed — fix before continuing:\n%s\n' "$1" "$2" >&2; exit 2; }

out=$(uvx ruff@0.16.1 check . 2>&1) || fail "ruff" "$out"
out=$(uvx ty@0.0.65 check 2>&1)     || fail "ty"   "$out"
exit 0
