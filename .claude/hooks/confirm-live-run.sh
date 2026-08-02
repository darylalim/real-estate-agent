#!/usr/bin/env bash
# PreToolUse(Bash) — ask before running the agent for real.
#
# Every test here is offline by design, so the two commands that reach the model
# are the only ones that spend money: main.py, and `streamlit run
# streamlit_app.py`, whose Chat page drives the same opus-5 orchestrator fanning
# out to four specialists. The Streamlit entry point was added later and this
# guard did not know about it for a while -- launching the UI is cheap, but the
# first prompt typed into it is not, and by then no hook is in the loop. Serving
# the app is what gets asked about, since that is the last point a hook sees.
# This asks rather than denies, which is why it survives while the
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
#      streamlit run ... -- matched on the subcommand rather than the script
#      path, because the flags between them vary (--server.port, --server.headless)
#      and this repo has exactly one Streamlit app, whose Chat page is live.
#      Anchored to an invocation shape, not a bare substring: quotes are stripped
#      above, so `grep -rn "streamlit run" README.md` otherwise asked too.
printf '%s' "$norm" | grep -qE '(^|; |&& )streamlit +run( |$)' && live=1
printf '%s' "$norm" | grep -qE '(^| )uv run( +--?[^ ]+)* +streamlit +run( |$)' && live=1
printf '%s' "$norm" | grep -qE '(^| )-m +streamlit +run( |$)' && live=1

[ "$live" -eq 1 ] || exit 0

model=$(grep -sE '^REA_MODEL=' "${CLAUDE_PROJECT_DIR:-.}/.env" | cut -d= -f2-)
model=${model:-anthropic:claude-opus-5}

jq -n --arg m "$model" '{hookSpecificOutput:{
  hookEventName:"PreToolUse",
  permissionDecision:"ask",
  permissionDecisionReason:("Reaches the agent live against " + $m +
    " — real billable calls fanning out to four specialists. Every test in this repo is offline; main.py and the Streamlit app are the only ways to spend money. Serving the app does not call the model, but the first prompt typed into its Chat page does, and no hook sees that.")}}'
exit 0
