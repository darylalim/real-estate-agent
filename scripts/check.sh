#!/usr/bin/env bash
# The definition of done, in one command: "N tests, ty clean, ruff clean".
#
# The pins live here rather than only in a Markdown fence, so a human shell, a
# Claude Code hook, and any future CI all run the same versions. A fence
# documents a version; this enforces it for whoever calls the script.
# `test_check_script_pins_match_the_docs` fails when this file and the docs
# disagree, the same way `required-version` fails a mismatched ruff.
#
#   scripts/check.sh            ruff, ty, pytest
#   scripts/check.sh --floor    the above, then the requires-python 3.11 leg
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

RUFF_VERSION=0.16.1
TY_VERSION=0.0.65

floor=0
[ "${1:-}" = "--floor" ] && floor=1

status=0
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

step "ruff ${RUFF_VERSION}"
uvx "ruff@${RUFF_VERSION}" check . || status=1

step "ty ${TY_VERSION}"
uvx "ty@${TY_VERSION}" check || status=1

step "pytest"
uv run --quiet pytest tests/ -q || status=1

if [ "$floor" -eq 1 ]; then
  # .python-version pins development to 3.14, so requires-python = ">=3.11" is
  # executed nowhere unless something runs it. --isolated keeps the throwaway
  # environment out of the project venv.
  step "pytest on the 3.11 floor"
  uv run --python 3.11 --isolated pytest tests/ -q || status=1
fi

exit $status
