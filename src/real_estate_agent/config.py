"""Model, path, and environment configuration.

Every path the agent is allowed to touch is derived from ``PROJECT_ROOT`` here,
so the filesystem backend and the document tools share one notion of "inside
the project".
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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

# LangChain resolves "provider:model" strings through init_chat_model, so the
# prefix is required — a bare "claude-opus-5" will not resolve.
DEFAULT_MODEL = os.getenv("REA_MODEL", "anthropic:claude-opus-5")

# Subagents inherit the orchestrator's model unless overridden. Kept separate
# so cost/latency tuning is a config change, not a code change.
SUBAGENT_MODEL = os.getenv("REA_SUBAGENT_MODEL", DEFAULT_MODEL)


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
