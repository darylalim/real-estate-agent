"""Model, path, and environment configuration.

Every path the agent is allowed to touch is derived from ``PROJECT_ROOT`` here,
so the filesystem backend and the document tools share one notion of "inside
the project".

**This module does not read ``.env``, and must not.** It used to call
``load_dotenv()`` right here, which made importing the package -- something the
test suite does on every run -- quietly apply a developer's personal
configuration. That is how the whole suite ended up tracing to LangSmith: one
import turned ``LANGSMITH_TRACING=true`` on for 17 billable root runs per run.
``REA_MODEL`` and ``REA_PROJECT_ROOT`` leaked by the same route and are worse,
because they reconfigure the graph and the file roots the tests assert against
without changing anything a test can see.

Loading ``.env`` belongs to the entry points -- ``main.py`` and
``streamlit_app.py`` -- and the ordering there is load-bearing rather than
tidy-looking. The three values below are read from the environment **at import
time**, so an entry point has to load ``.env`` before importing this module, not
merely before calling into it. ``main.py`` does that by keeping both the call
and its ``real_estate_agent`` imports inside ``main()``;
``streamlit_app.py`` gets it for free, since it imports no page module until
``page.run()``. ``test_each_entry_point_loads_dotenv_before_the_package`` pins
both, and ``test_config_does_not_load_dotenv_at_import`` pins this file.

The consequence for library use is deliberate: ``from real_estate_agent import
build_agent`` no longer picks up ``.env`` by itself, so a caller embedding the
package supplies the environment the way any other library expects.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

# parents[2] is correct for an editable src-layout checkout. Installed
# non-editable it would resolve into site-packages, silently rooting the agent
# somewhere with no skills/ directory, so allow an explicit override.
PROJECT_ROOT = Path(
    os.getenv("REA_PROJECT_ROOT") or Path(__file__).resolve().parents[2]
).resolve()
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
DRAFTS_DIR = WORKSPACE_DIR / "drafts"
DOCUMENTS_DIR = WORKSPACE_DIR / "documents"
SKILLS_DIR = PROJECT_ROOT / "skills"

# Conversation state, so `--thread <id>` survives the process that created it.
# Inside WORKSPACE_DIR because that is already gitignored — a checkpoint file
# holds the full transcript, which is the last thing to commit by accident.
CHECKPOINT_DB = WORKSPACE_DIR / "checkpoints.db"

# LangChain resolves "provider:model" strings through init_chat_model. The
# prefix is this repo's convention, not a hard requirement — init_chat_model
# infers "anthropic" from any "claude-*" name, so a bare "claude-opus-5" does
# resolve. Keep it anyway: require_api_key() reads the prefix and falls back to
# "anthropic" when there is no ":", which is the right guess for exactly one
# provider. A bare "gpt-4o" makes it demand ANTHROPIC_API_KEY and never check
# OPENAI_API_KEY — measured.
DEFAULT_MODEL = os.getenv("REA_MODEL", "anthropic:claude-opus-5-5")

# Subagents inherit the orchestrator's model unless overridden. Kept separate
# so cost/latency tuning is a config change, not a code change.
SUBAGENT_MODEL = os.getenv("REA_SUBAGENT_MODEL", DEFAULT_MODEL)


def run_config(
    thread_id: str, *, entry_point: Literal["cli", "web"], require_approval: bool
) -> dict[str, Any]:
    """The LangGraph run config both entry points pass to ``agent.stream``.

    ``thread_id`` needs no help to reach LangSmith: LangGraph copies every
    ``configurable`` key into the root run's metadata, which is what groups a
    conversation in the Threads view. What it cannot supply is where a run came
    from and how it was built, and those are the questions a trace gets opened
    to answer — which surface, which models, and whether the approval gate was
    in the stack when a ``save_draft`` went through. Tags and metadata set here
    are inherited by every nested subagent, model and tool run.

    One function so the CLI and the web page cannot drift into tagging the same
    conversation two different ways. The models reported are this module's
    constants, which is what both entry points build with; a library caller
    passing ``model=`` to ``build_agent`` should build its own config.
    """
    try:
        app_version = version("real-estate-agent")
    except PackageNotFoundError:
        app_version = "unknown"
    return {
        "configurable": {"thread_id": thread_id},
        "tags": [entry_point],
        "metadata": {
            "entry_point": entry_point,
            "model": DEFAULT_MODEL,
            "subagent_model": SUBAGENT_MODEL,
            "require_approval": require_approval,
            "app_version": app_version,
        },
    }


def ensure_workspace() -> None:
    """Create the agent's scratch directories if they don't exist yet."""
    for directory in (WORKSPACE_DIR, DRAFTS_DIR, DOCUMENTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


# LangChain provider prefix -> the env var that provider needs.
_PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
}


def require_api_key() -> None:
    """Fail fast with an actionable message instead of a 401 mid-run.

    Derived from the configured model's provider prefix, so overriding
    ``REA_MODEL`` to a non-Anthropic provider does not demand an Anthropic key.
    """
    needed: set[str] = set()
    for spec in (DEFAULT_MODEL, SUBAGENT_MODEL):
        provider = spec.split(":", 1)[0] if ":" in spec else "anthropic"
        variable = _PROVIDER_KEYS.get(provider)
        if variable and not os.getenv(variable):
            needed.add(variable)

    if needed:
        names = ", ".join(sorted(needed))
        raise RuntimeError(
            f"{names} is not set. Copy .env.example to .env and fill it in, "
            "or export the variable in your shell."
        )
