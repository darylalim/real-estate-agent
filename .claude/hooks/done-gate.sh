#!/usr/bin/env bash
# Stop — the expensive half of the definition of done, once per turn, and only
# when Python actually changed. There is no CI here; this is the only gate.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

[ -n "$(git status --porcelain -- '*.py' 2>/dev/null)" ] || exit 0

# 1. The suite (3.14, the pinned interpreter).
if ! out=$(uv run --quiet pytest tests/ -q 2>&1); then
  printf 'Tests fail — the definition of done is "N tests, ty clean, ruff clean".\n\n%s\n' "$out" >&2
  exit 2
fi

# 2. The requires-python floor. .python-version pins dev to 3.14, so ">=3.11" is
#    never executed unless something runs it. ~7s, once per turn.
if ! floor=$(uv run --python 3.11 --isolated pytest tests/ -q 2>&1); then
  printf 'The 3.11 floor leg failed — the code no longer honours requires-python = ">=3.11".\nNarrowing requires-python is usually the honest fix, not deleting the leg.\n\n%s\n' "$floor" >&2
  exit 2
fi

# 3. The suite size is written into CLAUDE.md and README.md and nothing checks
#    it. A docs nit, not a broken build — warn the human, do not block.
count=$(printf '%s' "$out" | grep -oE '[0-9]+ passed' | grep -oE '^[0-9]+')
stale=""
for doc in CLAUDE.md README.md; do
  [ -z "$count" ] || grep -q "${count} tests" "$doc" 2>/dev/null || stale="${stale}${doc} "
done
[ -z "$stale" ] || jq -n --arg m "Suite is now ${count} tests; the count is stale in: ${stale}" '{systemMessage:$m}'
exit 0
