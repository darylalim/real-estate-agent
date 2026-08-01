#!/usr/bin/env bash
# SessionStart — record the commit the session began at.
#
# done-gate.sh gated on `git status` alone, which meant a turn that edited and
# committed in one go reached Stop with a clean tree and skipped the gate --
# on exactly the turn whose commit message asserts the done-line. The baseline
# lets the gate see work that has already been committed this session.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

session=$(jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
head=$(git rev-parse HEAD 2>/dev/null) || exit 0
printf '%s\n' "$head" > "${TMPDIR:-/tmp}/rea-hook-baseline-${session}"
exit 0
