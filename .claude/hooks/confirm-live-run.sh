#!/usr/bin/env bash
# PreToolUse(Bash) — the whole test suite is offline by design. main.py is the one
# command that spends real Anthropic money (orchestrator + 4 specialists).
set -uo pipefail
cmd=$(cat | jq -r '.tool_input.command // empty')

printf '%s' "$cmd" | grep -qE 'python[0-9.]* +(\./)?main\.py' || exit 0

model=$(grep -sE '^REA_MODEL=' "${CLAUDE_PROJECT_DIR:-.}/.env" | cut -d= -f2-)
model=${model:-anthropic:claude-opus-5}

jq -n --arg m "$model" '{hookSpecificOutput:{
  hookEventName:"PreToolUse",
  permissionDecision:"ask",
  permissionDecisionReason:("Runs the agent live against " + $m +
    " — a real billable call fanning out to four specialists. Every test in this repo is offline; this is the only command that costs money.")}}'
exit 0
