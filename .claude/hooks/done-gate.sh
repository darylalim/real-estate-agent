#!/usr/bin/env bash
# Stop — the definition of done, once per turn. There is no CI here, so this and
# scripts/check.sh are the only things that run it.
#
# Gating history worth keeping: this used to run only when `git status` showed a
# changed *.py, which skipped the suite on exactly the edits its toolchain tests
# exist to catch -- a pyproject.toml that drops `required-version`, or a
# CLAUDE.md whose test count has gone stale. check.sh is 1.7s, so it now runs
# unconditionally and that whole class of gap is gone. Only the 7s floor leg is
# gated, on Python, which is what the floor leg is actually about.
set -uo pipefail
# Sourcing must be fatal: without `set -e`, a failed source leaves every helper
# undefined and the hook runs on to `exit 0` -- allowing what it exists to deny.
# shellcheck source=/dev/null
. "${BASH_SOURCE[0]%/*}/_lib.sh" || { printf '%s: cannot source _lib.sh\n' "${0##*/}" >&2; exit 1; }
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

payload=$(cat)
session=$(printf '%s' "$payload" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
strikes_file="${TMPDIR:-/tmp}/rea-hook-strikes-${session}"
baseline_file="${TMPDIR:-/tmp}/rea-hook-baseline-${session}"

# A Stop hook that exits 2 forces the turn to continue. If the failure is one no
# code change fixes -- the floor leg needs the network to resolve 59 packages,
# and a cold run downloads a CPython too -- blocking forever is worse than
# reporting. Three strikes, then hand it to the human and let the turn end.
strikes=$(cat "$strikes_file" 2>/dev/null || echo 0)

block() {
  strikes=$((strikes + 1))
  printf '%s' "$strikes" > "$strikes_file"
  if [ "$strikes" -ge 3 ]; then
    rm -f "$strikes_file"
    # Deliberately no hook_require_jq in this script: exiting early on a missing
    # jq would skip the gate, which is worse than losing the pretty message.
    stand_down="$1 — blocked 3 times without clearing, so the gate is standing down. Run scripts/check.sh --floor yourself."
    if command -v jq >/dev/null 2>&1; then
      jq -n --arg m "$stand_down" '{systemMessage:$m}'
    else
      printf '%s\n' "$stand_down" >&2
    fi
    exit 0
  fi
  printf '%s\n\n%s\n' "$1" "$2" >&2
  exit 2
}

if ! out=$(./scripts/check.sh 2>&1); then
  block 'The definition of done does not hold: "N tests, ty clean, ruff clean".' "$out"
fi

# The floor leg, when Python changed -- in the working tree, or in a commit made
# since the session began. Committing inside a turn used to leave a clean tree
# and skip this entirely.
baseline=$(cat "$baseline_file" 2>/dev/null || true)
changed=$(git status --porcelain -- '*.py' 2>/dev/null)
if [ -z "$changed" ] && [ -n "$baseline" ]; then
  changed=$(git diff --name-only "$baseline" HEAD -- '*.py' 2>/dev/null || true)
fi

if [ -n "$changed" ]; then
  if ! floor=$(uv run --python 3.11 --isolated pytest tests/ -q 2>&1); then
    block 'The 3.11 floor leg failed — the code no longer honours requires-python = ">=3.11". Narrowing requires-python is usually the honest fix, not deleting the leg.' "$floor"
  fi
fi

rm -f "$strikes_file"
exit 0
