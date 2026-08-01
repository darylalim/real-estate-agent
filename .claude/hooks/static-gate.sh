#!/usr/bin/env bash
# PostToolUse(Edit|Write) — ruff and ty on every Python edit. 0.12s combined,
# which is cheap enough to spend per-edit; the suite runs once per turn on Stop.
set -uo pipefail
# Sourcing must be fatal: without `set -e`, a failed source leaves every helper
# undefined and the hook runs on to `exit 0` -- allowing what it exists to deny.
# shellcheck source=/dev/null
. "${BASH_SOURCE[0]%/*}/_lib.sh" || { printf '%s: cannot source _lib.sh\n' "${0##*/}" >&2; exit 1; }
hook_require_jq
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

file=$(hook_file_path)
case "$file" in *.py) ;; *) exit 0 ;; esac

fail() { printf '%s failed — fix before continuing:\n%s\n' "$1" "$2" >&2; exit 2; }

# Versions come from scripts/check.sh so there is one place to bump them.
RUFF_VERSION=$(sed -n 's/^RUFF_VERSION=//p' scripts/check.sh | head -1)
TY_VERSION=$(sed -n 's/^TY_VERSION=//p' scripts/check.sh | head -1)

out=$(uvx "ruff@${RUFF_VERSION}" check . 2>&1) || fail "ruff" "$out"
out=$(uvx "ty@${TY_VERSION}" check 2>&1)       || fail "ty"   "$out"
exit 0
