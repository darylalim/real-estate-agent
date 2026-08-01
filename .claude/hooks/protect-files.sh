#!/usr/bin/env bash
# PreToolUse(Edit|Write|NotebookEdit|Bash) — .env holds a live key; uv.lock is
# generated.
#
# The Bash arm is a speed bump, not a boundary. `E=.env; cat $E` defeats it, as
# does any eval or subshell -- string matching a shell command only holds
# against a cooperative caller. It exists because the realistic risk is an
# accidental `cat .env` pasting a live key into the transcript, not an adversary.
set -uo pipefail
# Sourcing must be fatal: without `set -e`, a failed source leaves every helper
# undefined and the hook runs on to `exit 0` -- allowing what it exists to deny.
# shellcheck source=/dev/null
. "${BASH_SOURCE[0]%/*}/_lib.sh" || { printf '%s: cannot source _lib.sh\n' "${0##*/}" >&2; exit 1; }
hook_require_jq

payload=$(cat)
ENV_REASON=".env holds a live ANTHROPIC_API_KEY and is gitignored. Read it yourself if you need it; .env.example is the tracked template."
LOCK_REASON="uv.lock is generated. Change dependencies in pyproject.toml, then run: uv sync"

file=$(printf '%s' "$payload" | hook_file_path)
if [ -n "$file" ]; then
  case "$(basename "$file")" in
    .env.example) ;;
    .env|.env.*) hook_deny "$ENV_REASON" ;;
    uv.lock)     hook_deny "$LOCK_REASON" ;;
  esac
  exit 0
fi

cmd=$(printf '%s' "$payload" | hook_command)
[ -n "$cmd" ] || exit 0

# `.env` as an argument to something that prints file contents. The trailing
# class stops this matching .env.example, which is the tracked template and the
# thing `cp .env.example .env` in the README copies *from*.
readers='cat|less|more|head|tail|grep|rg|egrep|fgrep|awk|sed|od|xxd|strings|nl|bat|open'
if printf '%s' "$cmd" | grep -qE "(^|[|&;(]|[[:space:]])(${readers})[[:space:]][^|&;]*\.env([[:space:]]|\$|[\"'])"; then
  hook_deny "$ENV_REASON"
fi

# Redirecting into it, or an in-place edit of it.
if printf '%s' "$cmd" | grep -qE '>>?[[:space:]]*\.env([[:space:]]|$)'; then
  hook_deny "$ENV_REASON"
fi

# No Bash rule for uv.lock: `uv lock`, `uv sync` and `uv add` are the sanctioned
# ways to change it, so there is nothing here to deny.
exit 0
