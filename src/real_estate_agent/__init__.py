"""Real estate agent built on Deep Agents.

``build_agent`` is imported lazily so that touching a provider or a dataclass
doesn't drag in the whole LangChain stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from real_estate_agent.agent import build_agent

__all__ = ["build_agent"]


def __getattr__(name: str) -> Any:
    if name == "build_agent":
        from real_estate_agent.agent import build_agent

        return build_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
