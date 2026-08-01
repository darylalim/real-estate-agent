#!/usr/bin/env bash
# PreToolUse(Edit|Write|NotebookEdit) — .env holds a live key; uv.lock is generated.
set -uo pipefail
file=$(cat | jq -r '.file_path // .tool_input.file_path // empty')
[ -n "$file" ] || exit 0
base=$(basename "$file")

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",
    permissionDecision:"deny", permissionDecisionReason:$r}}'
  exit 0
}

case "$base" in
  .env.example) ;;
  .env|.env.*)
    deny ".env holds a live ANTHROPIC_API_KEY and is gitignored. Edit it yourself; .env.example is the tracked template." ;;
  uv.lock)
    deny "uv.lock is generated. Change dependencies in pyproject.toml, then run: uv sync" ;;
esac
exit 0
