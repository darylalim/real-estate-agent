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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def require_api_key() -> None:
    """Fail fast with an actionable message instead of a 401 mid-run."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in, "
            "or export the variable in your shell."
        )
