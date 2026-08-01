#!/usr/bin/env bash
# Shared by every hook in this directory. Sourced, never executed.
#
# The three helpers below were copy-pasted across four scripts before this
# existed, which is how the NotebookEdit path bug came to need fixing twice.

# jq is an undeclared prerequisite. Without this probe an absent jq yields an
# empty extraction, every guard falls through to "allow", and nothing says so --
# the opposite of the fail-closed rule the approval gate follows.
# ${0##*/} rather than basename: an external command here would be one more way
# to fail open, which is the thing this function exists to prevent.
hook_require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    printf '%s: jq is not installed, so this hook cannot inspect the tool call. Install jq (brew install jq) or remove the hook rather than leaving it silently disabled.\n' \
      "${0##*/}" >&2
    exit 1
  fi
}

# Deny a tool call, with a reason the model reads.
hook_deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",
    permissionDecision:"deny", permissionDecisionReason:$r}}'
  exit 0
}

# The file a tool is acting on. NotebookEdit uses notebook_path, not file_path;
# omitting it made the NotebookEdit matcher dead configuration that read as
# protection.
hook_file_path() {
  jq -r '.tool_input.file_path // .tool_input.notebook_path // .file_path // empty'
}

hook_command() {
  jq -r '.tool_input.command // empty'
}
