"""Tracing off for the suite, before anything imports the package.

`config.py` calls `load_dotenv()` at import time and `.env` sets
`LANGSMITH_TRACING=true`, so merely importing `real_estate_agent` was enough to
turn tracing on for every test. Each `tool.invoke()` in the suite is then a
LangSmith **root** run — one billable trace apiece, and LangSmith bills per
trace, not per span. Measured when this was found: 17 per full run, against a
Stop hook that runs the whole suite every turn. A live agent turn, by contrast,
is *one* trace with every subagent and tool call nested inside it for free, so
the suite was outspending the thing worth tracing by a wide margin.

Two details make the override work, both easy to undo by accident:

- pytest imports `conftest.py` before any test module, so this runs before
  `real_estate_agent.config` is first imported. The same lines inside the test
  module would be too late — `load_dotenv()` would already have fired.
- `load_dotenv()` defaults to `override=False`, so an already-set variable wins
  over `.env`. Passing `override=True` there would defeat this entirely, which
  is what `pytest_collection_finish` below is watching for.

`os.environ.setdefault` is the tempting simplification and it is **wrong**:
`.env` has not been read at conftest time, so setdefault leaves the name unset
and the later `load_dotenv()` then sets it to `true` — restoring the exact
defect this file exists to prevent. Assign unconditionally.

Live runs — `main.py` and the Streamlit app — never import this file and keep
tracing exactly as `.env` configures it. That is the point: trace the agent,
not the unit tests.
"""

from __future__ import annotations

import os

import pytest

# Set together and checked together. `langsmith.utils.get_env_var` reads
# LANGSMITH_* before LANGCHAIN_*, and TRACING_V2 before TRACING, so leaving any
# one of the four live leaves a way back in.
TRACING_VARIABLES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)

# The one legitimate reason to want tracing here is debugging a single tool's
# trace, and without an opt-in that developer exports LANGSMITH_TRACING=true,
# watches this file overwrite it, and concludes tracing is broken. Explicit, so
# it cannot happen by inheriting a shell.
TRACING_OPT_IN = "REA_TEST_TRACING"

if not os.environ.get(TRACING_OPT_IN):
    for _variable in TRACING_VARIABLES:
        os.environ[_variable] = "false"


def pytest_collection_finish() -> None:
    """Abort before the first tool call rather than reporting after the last one.

    `test_the_suite_does_not_trace_to_langsmith` runs in definition order, which
    puts it after all 17 traced `invoke`s — so on the run that detects a
    regression the spend has already happened, and a `-k`-filtered run that
    never reaches it spends with no report at all. Collection has imported the
    test module by now, and therefore `config.py`, so `load_dotenv()` has fired
    and a `override=True` there is visible here — before any test executes.

    This cannot catch its own file being deleted; a hook in the file being
    removed does not run. That case belongs to the test, which asserts on the
    values this sets rather than on tracing merely being off.
    """
    if os.environ.get(TRACING_OPT_IN):
        return
    live = [name for name in TRACING_VARIABLES if os.environ.get(name) != "false"]
    if live:
        pytest.exit(
            "LangSmith tracing was re-enabled after tests/conftest.py set it off "
            f"({', '.join(live)}). Every tool.invoke() in this suite is a billable "
            "root run; aborting before any of them execute. The usual cause is "
            "load_dotenv(override=True) in real_estate_agent/config.py.",
            returncode=1,
        )
