#!/usr/bin/env bash
# PreToolUse(Bash) — enforce the two toolchain conventions CLAUDE.md documents
# but nothing checks: the ty pin, and "ruff check yes, ruff format no".
set -uo pipefail
cmd=$(cat | jq -r '.tool_input.command // empty')

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",
    permissionDecision:"deny", permissionDecisionReason:$r}}'
  exit 0
}

# Don't fire on commands that merely mention the string (grep/rg over the docs).
case "$cmd" in
  grep\ *|rg\ *|cat\ *|head\ *|tail\ *|less\ *) exit 0 ;;
esac

# `ruff format`, `ruff@0.16.1 format`, `uv run ruff format` — but not the
# read-only `--check`/`--diff` forms, whose output CLAUDE.md actually quotes.
if printf '%s' "$cmd" | grep -qE 'ruff(@[0-9.]+)? +format' \
   && ! printf '%s' "$cmd" | grep -qE 'format.*--(check|diff)'; then
  deny "CLAUDE.md: 'ruff check yes, ruff format no.' The formatter rewrites 7 of the 14 .py files over line-wrapping disagreements, burying real diffs under cosmetic ones. Use: uvx ruff@0.16.1 check ."
fi

if printf '%s' "$cmd" | grep -qE '(^|[^-@[:alnum:]_])uvx +ty([^@]|$)'; then
  deny "ty has no required-version, so its pin is a convention nothing enforces. Use: uvx ty@0.0.65 check — an unpinned pre-1.0 ty can report different diagnostics on identical source."
fi
exit 0
