"""Tracing off for the suite, before anything imports the package.

`config.py` calls `load_dotenv()` at import time and `.env` sets
`LANGSMITH_TRACING=true`, so merely importing `real_estate_agent` was enough to
turn tracing on for every test. Each `tool.invoke()` below is then a LangSmith
**root** run — one billable trace apiece, and LangSmith bills per trace, not per
span. Measured when this was found: 17 per full run, against a Stop hook that
runs the whole suite every turn. A live agent turn, by contrast, is *one* trace
with every subagent and tool call nested inside it for free, so the suite was
outspending the thing worth tracing by a wide margin.

Nothing raises when this file is missing. The suite stays green, the docs keep
saying "no API calls", and the only symptom is an exhausted quota — which is why
`test_the_suite_does_not_trace_to_langsmith` asserts the effective state rather
than trusting this file to stay put.

Two details that make it work, both easy to undo by accident:

- pytest imports `conftest.py` before any test module, so this runs before
  `real_estate_agent.config` is first imported. The same lines inside the test
  module would be too late — `load_dotenv()` would already have fired.
- `load_dotenv()` defaults to `override=False`, so an already-set variable wins
  over `.env`. Passing `override=True` there would defeat this entirely.

All four names are set because `langsmith.utils.get_env_var` reads `LANGSMITH_*`
then `LANGCHAIN_*`, for both `TRACING_V2` and `TRACING`. The predicate is
`== "true"`, so "false" disables it by not matching.

Live runs — `main.py` and the Streamlit app — never import this file and keep
tracing exactly as `.env` configures it. That is the point: trace the agent,
not the unit tests.
"""

from __future__ import annotations

import os

for _variable in (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
):
    os.environ[_variable] = "false"
