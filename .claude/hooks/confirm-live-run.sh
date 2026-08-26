#!/usr/bin/env bash
# PreToolUse(Bash) — asks before the two commands that reach the model.
#
# Every test here is offline by design, so the two commands that reach the model
# are the only ones that spend money: main.py, and `streamlit run
# streamlit_app.py`, whose Chat page drives the same opus-5 orchestrator fanning
# out to four specialists. Serving the app is what gets asked about, since that
# is the last point a hook sees -- the first prompt typed into the Chat page is
# what actually bills, and no hook is in the loop by then.
#
# This asks rather than denies, which is why 3b5aef8 restored it. 8a2241c
# deleted it outright alongside toolchain-guard.sh -- both Bash guards -- and
# only this one came back, on that reasoning: a leaky "ask" costs a keystroke,
# a leaky "deny" reads as protection it cannot provide. `M=main.py; uv run
# python $M` still gets through, and that is an accepted limit of matching
# shell strings, not an oversight.
#
# **Every rule below matches one command, not the whole string.** The version
# this replaces matched the whole normalised string, and ending a pattern at
# `( |$)` is only correct once a segment cannot contain a separator. Measured
# against it: `uv run python main.py; echo done`, `uv run python main.py;`,
# `(uv run python main.py)`, `{ uv run python main.py; }` and
# `uv run python -m main; echo done` were all allowed with no prompt, because `;`
# is neither a space nor end-of-string.
#
# A newline is a separator too, so it becomes one here rather than a space. The
# alternative was tried and dropped: collapsing a newline into a space and then
# skipping any command whose first word is a reader. That works until the reader
# is on line 1 and the live run is on line 2, and the fix for it is a list of
# command names that has to grow -- `ls`, `stat`, `find`, `xargs` and `git` were
# all missing from the seventeen it started with. Anchoring `streamlit run` at
# the *start* of a segment is the same protection without the list: quotes are
# stripped by then, so `grep -rn streamlit run README.md` is a segment that
# *contains* `streamlit run` without starting with it.
#
# The helpers this used to source from _lib.sh are inlined. That file existed to
# share three functions across four scripts; after the 2026-08-25 hook review
# there is one script left, and a shared library with a single consumer is
# indirection whose only remaining effect is a way for the guard to vanish.
set -uo pipefail

ask() {
  # jq when it is there, printf when it is not -- the degraded branch below has
  # to emit this shape too, and jq is precisely what is missing there. Keep
  # DEGRADED free of double quotes and backslashes so the printf path stays valid
  # JSON; the model string can carry anything, so it always goes through jq.
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg m "$1" '{hookSpecificOutput:{
      hookEventName:"PreToolUse", permissionDecision:"ask", permissionDecisionReason:$m}}'
  else
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$1"
  fi
  exit 0
}

DEGRADED='The live-run guard cannot inspect this command, so it cannot tell a live run from anything else. Approve if this is not main.py or a streamlit run. Install jq (brew install jq) to restore the guard.'

# jq is an undeclared prerequisite, and an ask hook must degrade to *asking*.
# The two obvious alternatives are both wrong here, measured: `exit 2` denied
# every Bash call -- including the `brew install jq` this very message prescribes
# -- because the hook is registered on the whole Bash matcher, so a machine
# without jq could not run any shell command until someone edited settings.json
# from outside the session. `exit 1` is non-blocking for PreToolUse, so it warns
# and allows, which is the live run going through unprompted. Asking is the only
# degradation that is neither a lockout nor a silent pass.
command -v jq >/dev/null 2>&1 || ask "$DEGRADED"

payload=$(cat)
# jq is present but the payload did not parse. Same reasoning, and this is the
# half the previous version missed: it guarded the binary being *absent* but not
# the binary *failing*, and an unchecked `cmd=$(jq ...)` under `set -uo pipefail`
# with no `set -e` yields an empty string and falls through to `exit 0`.
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null) \
  || ask "$DEGRADED"
[ -n "$cmd" ] || exit 0

# Normalise, in an order where every step depends on the one before it:
#   1. a backslash-newline is a line *join*, so it disappears;
#   2. quotes go, so `python "main.py"` reads like every other form -- quoting
#      was one of the four bypasses in the first version;
#   3. remaining backslashes go, so `python \main.py` cannot hide either;
#   4. every separator -- newline included -- becomes `;`, so one split covers
#      all of them and no pattern needs to know how a separator is spelled;
#   5. runs of whitespace collapse, which is now safe: there are no newlines
#      left to lose.
norm=${cmd//\\$'\n'/}
norm=${norm//\"/}
norm=${norm//\'/}
norm=${norm//\\/}
norm=$(printf '%s' "$norm" | tr '\n&|(){}`' ';;;;;;;;' | tr -s '[:space:]' ' ')

#      python [flags] main.py   /   python3 /abs/path/main.py
#      `([^ ]*/)?` rather than `(\./)?`: the old form admitted only a bare or
#      ./-prefixed name, so an absolute path -- the form most tooling actually
#      emits -- walked straight through the guard.
re_python='(^| )python[0-9.]* +(-[^ ]+ +)*([^ ]*/)?main\.py( |$)'
#      uv run [flags] main.py   -- uv runs the script itself, no `python` token
re_uvrun='(^| )uv run( +--?[^ ]+)* +([^ ]*/)?main\.py( |$)'
#      ./main.py  /  /abs/path/main.py  -- direct execution needs a path prefix,
#      so a bare `main.py` argument to something else does not match here.
re_direct='(^| )(\./|/)([^ ]*/)*main\.py( |$)'
#      -m main
re_module='(^| )-m +main( |$)'
#      streamlit run -- matched on the subcommand rather than the script path,
#      because the flags between them vary (--server.port, --server.headless) and
#      this repo has exactly one Streamlit app, whose Chat page is live. One
#      pattern covers the bare, `uv run` and `-m` forms. Anchored at `^`, which
#      is a *segment* start now and is what lets a doc grep through.
re_streamlit='^(uv +run +)?(--?[^ ]+ +)*(python[0-9.]* +-m +)?streamlit +run( |$)'

# `[[ =~ ]]` rather than five `printf | grep -qE` pipelines: this runs on every
# Bash call and fired on roughly one in a hundred, so ten forked processes per
# call were most of its cost. The ERE has to sit in an unquoted variable to work
# on bash 3.2, which is what /usr/bin/env bash is on macOS.
live=0
set -f
saved_ifs=$IFS
IFS=';'
for seg in $norm; do
  seg=${seg# }
  seg=${seg% }
  [ -n "$seg" ] || continue
  if [[ $seg =~ $re_python ]] || [[ $seg =~ $re_uvrun ]] || [[ $seg =~ $re_direct ]] \
    || [[ $seg =~ $re_module ]] || [[ $seg =~ $re_streamlit ]]; then
    live=1
    break
  fi
done
IFS=$saved_ifs
set +f

[ "$live" -eq 1 ] || exit 0

model=$(grep -sE '^REA_MODEL=' "${CLAUDE_PROJECT_DIR:-.}/.env" | cut -d= -f2-)
model=${model:-anthropic:claude-opus-5}

ask "Reaches the agent live against ${model} — real billable calls fanning out to four specialists. Every test in this repo is offline; main.py and the Streamlit app are the only ways to spend money. Serving the app does not call the model, but the first prompt typed into its Chat page does, and no hook sees that."
