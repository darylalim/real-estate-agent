#!/usr/bin/env bash
# Stop — the definition of done, once per turn.
#
# This is the local half of a two-machine arrangement, and it is deliberately
# the cheap half. `.github/workflows/check.yml` runs `scripts/check.sh --floor`
# on every push and PR, on Linux, on a clean checkout, with `uv sync --locked`.
# What this buys that CI cannot is timing: a commit message asserting "N tests,
# ty clean, ruff clean" is true when it is written, not several minutes later.
#
# Two pieces of gating history worth keeping:
#
# 1. This used to run only when `git status` showed a changed *.py, which
#    skipped the suite on exactly the edits its toolchain tests exist to catch
#    -- a pyproject.toml that drops `required-version`, or a CLAUDE.md whose
#    test count has gone stale. check.sh is 2.4s, so it runs unconditionally.
#
# 2. It used to also run the 3.11 floor leg, gated on a changed *.py against a
#    baseline commit written by a SessionStart hook. That leg was removed on
#    2026-08-25 and session-start.sh deleted with it. Three measured reasons:
#    the leg costs ~27s warm and ~37s cold, not the 7s this header used to claim
#    (`--isolated` rebuilds the environment every run, and the suite is ~22s on
#    3.11 against ~1.4s on 3.14); the baseline was never advanced, so one
#    mid-session .py commit made every remaining Stop pay it, read-only turns
#    included; and CI runs --floor unconditionally, which is a strict superset of
#    what the *.py gate ever triggered. Run `scripts/check.sh --floor` by hand
#    before changing anything the type system touches.
#
# **Only exit 2 blocks a Stop.** Every other non-zero status is a non-blocking
# error: Claude Code prints it and lets the turn end anyway. That is the whole
# reason the payload is read before the `cd` below rather than after -- a failure
# has to be able to reach `block`, and `block` needs the session id to find its
# strike file.
set -uo pipefail

payload=$(cat)
session=$(printf '%s' "$payload" | jq -r '.session_id // "unknown"' 2>/dev/null || echo unknown)
strikes_file="${TMPDIR:-/tmp}/rea-hook-strikes-${session}"

# A Stop hook that exits 2 forces the turn to continue. If the failure is one no
# code change fixes -- an unset or wrong CLAUDE_PROJECT_DIR, a uvx cache that
# cannot reach the network to fetch the pinned ruff and ty -- blocking forever is
# worse than reporting. Three strikes, then hand it to the human and let the turn
# end. Note what this costs: three consecutive *genuine* failures also stand the
# gate down, and since static-gate.sh was deleted a lint error no longer surfaces
# at the edit that caused it. The stand-down message is the only thing that says
# so, which is why it names the command to run.
strikes=$(cat "$strikes_file" 2>/dev/null || echo 0)
# Non-numeric content here used to be fatal in the worst direction: `strikes=$((
# strikes + 1 ))` under `set -u` treats a non-numeric value as a variable name,
# so the shell died with "unbound variable" *before* reaching `exit 2` and the
# hook returned 0 -- a failing definition-of-done silently allowing the turn to
# end. Reproduced, then fixed here rather than by dropping `set -u`.
case $strikes in '' | *[!0-9]*) strikes=0 ;; esac

# block <headline> <detail> <what to run>
block() {
  strikes=$((strikes + 1))
  printf '%s' "$strikes" > "$strikes_file"
  if [ "$strikes" -ge 3 ]; then
    rm -f "$strikes_file"
    # Deliberately no jq prerequisite check in this script: exiting early on a
    # missing jq would skip the gate, which is worse than losing the message.
    stand_down="$1 — blocked 3 times without clearing, so the gate is standing down. $3"
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

# Fail closed, and closed means *2*. The version this replaces exited 1 under a
# comment explaining that failing open here would be "allowing what it exists to
# deny" -- but 1 is non-blocking, so the turn ended, check.sh never ran, and the
# gate was still silently removed. Measured: `CLAUDE_PROJECT_DIR=/nonexistent`
# gave exit 1 and a stderr line nobody was required to act on.
cd "${CLAUDE_PROJECT_DIR:-.}" || block \
  "The done gate cannot run: cannot cd to CLAUDE_PROJECT_DIR (${CLAUDE_PROJECT_DIR:-.})." \
  "" \
  "Set CLAUDE_PROJECT_DIR, or run scripts/check.sh from the repo root yourself."

if ! out=$(./scripts/check.sh 2>&1); then
  block 'The definition of done does not hold: "N tests, ty clean, ruff clean".' \
    "$out" \
    "Run scripts/check.sh yourself."
fi

rm -f "$strikes_file"
exit 0
