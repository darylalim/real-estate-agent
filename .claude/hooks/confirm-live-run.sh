#!/usr/bin/env bash
# PreToolUse(Bash) — ask before running the agent for real.
#
# Every test here is offline by design, so main.py is the only command in the
# repo that spends money: an opus-5 orchestrator fanning out to four
# specialists. This asks rather than denies, which is why it survives while the
# deleted toolchain-guard.sh did not -- a leaky "ask" costs a keystroke, a leaky
# "deny" reads as protection it cannot provide. `M=main.py; uv run python $M`
# still gets through, and that is an accepted limit, not an oversight.
set -uo pipefail
# Sourcing must be fatal: without `set -e`, a failed source leaves every helper
# undefined and the hook runs on to `exit 0` -- allowing what it exists to deny.
# shellcheck source=/dev/null
. "${BASH_SOURCE[0]%/*}/_lib.sh" || { printf '%s: cannot source _lib.sh\n' "${0##*/}" >&2; exit 1; }
hook_require_jq

cmd=$(hook_command)
[ -n "$cmd" ] || exit 0

# Normalise quoting and runs of whitespace, so `python "main.py"` reads like
# every other form. Quoting was one of the four bypasses in the first version.
norm=$(printf '%s' "$cmd" | tr -d "\"'" | tr -s '[:space:]' ' ')

live=0
#      python [flags] main.py   /   python3 ./main.py
printf '%s' "$norm" | grep -qE '(^| )python[0-9.]* +(-[^ ]+ +)*(\./)?main\.py( |$)' && live=1
#      uv run [flags] main.py   -- uv runs the script itself, no `python` token
printf '%s' "$norm" | grep -qE '(^| )uv run( +--?[^ ]+)* +(\./)?main\.py( |$)' && live=1
#      ./main.py
printf '%s' "$norm" | grep -qE '(^| )\./main\.py( |$)' && live=1
#      -m main
printf '%s' "$norm" | grep -qE '(^| )-m +main( |$)' && live=1

[ "$live" -eq 1 ] || exit 0

model=$(grep -sE '^REA_MODEL=' "${CLAUDE_PROJECT_DIR:-.}/.env" | cut -d= -f2-)
model=${model:-anthropic:claude-opus-5}

jq -n --arg m "$model" '{hookSpecificOutput:{
  hookEventName:"PreToolUse",
  permissionDecision:"ask",
  permissionDecisionReason:("Runs the agent live against " + $m +
    " — a real billable call fanning out to four specialists. Every test in this repo is offline; this is the only command that costs money.")}}'
exit 0
