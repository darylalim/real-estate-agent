"""Orchestrator assembly.

``create_deep_agent`` wires TodoList, Filesystem, and SubAgent middleware for
us — this module's job is only to decide the model, the backend, the write
containment rules, and which specialists exist.
"""

from __future__ import annotations

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from real_estate_agent.config import (
    DEFAULT_MODEL,
    PROJECT_ROOT,
    SUBAGENT_MODEL,
    ensure_workspace,
)
from real_estate_agent.providers.base import ListingsProvider
from real_estate_agent.providers.mock import MockListingsProvider
from real_estate_agent.subagents import build_subagents
from real_estate_agent.tools import (
    make_comms_tools,
    make_document_tools,
    make_listing_tools,
    make_market_tools,
)

ORCHESTRATOR_PROMPT = """\
You are a real estate agent assistant coordinating four specialists.

Delegate with the `task` tool rather than doing specialist work yourself:

- `property-search` — finding listings, building and refining a buyer shortlist.
- `market-analyst` — CMAs, valuations, pricing and offer strategy, market conditions.
- `document-reviewer` — leases, purchase agreements, disclosures.
- `client-liaison` — lead qualification, drafting client-facing messages.

Use `write_todos` to plan anything that spans more than one specialist, and work
the list in order. A request like "should we offer on MLS-1022, and draft the
offer letter" is a market-analyst task followed by a client-liaison task — plan
it, then run it.

Handle directly, without delegating: clarifying questions, reading files a
specialist already wrote to `/workspace/`, and synthesising several specialists'
findings into one answer.

Specialists share the `/workspace/` filesystem but not each other's context. If
one needs another's output, say so in the delegation — point at the file path.

Ground every factual claim in a tool result. If you do not have the data, say
what is missing and what you would need, rather than estimating. You are not a
lawyer, a lender, or a licensed appraiser: for legal, financing, or formal
appraisal questions, give what the data supports and recommend the appropriate
professional.
"""


# FilesystemBackend grants real disk access, so containment is explicit.
# Rules are evaluated in order and the FIRST MATCH WINS (unmatched defaults to
# allow), which is why the allows must precede the catch-all write deny.
# Exported so the test suite asserts against these exact rules rather than a
# hand-copied duplicate that cannot fail when this list changes.
WORKSPACE_PERMISSIONS: list[FilesystemPermission] = [
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/**"], mode="allow"),
    FilesystemPermission(operations=["read"], paths=["/skills/**"], mode="allow"),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/.env", "/.env.*", "/.venv/**", "/.git/**"],
        mode="deny",
    ),
    FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
]


def build_agent(
    *,
    provider: ListingsProvider | None = None,
    model: str | None = None,
    subagent_model: str | None = None,
    require_approval: bool = False,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Construct the orchestrator agent.

    Args:
        provider: Listings data source. Defaults to the deterministic mock —
            pass a real ``ListingsProvider`` to take the agent live.
        model: Orchestrator model, as a LangChain ``provider:model`` string.
        subagent_model: Model for the specialists. Defaults to ``model``.
        require_approval: When True, writing a client-facing draft pauses for
            human approval via the interrupt mechanism. Requires a checkpointer.
        checkpointer: LangGraph checkpointer. Defaults to ``InMemorySaver`` so
            that ``thread_id`` conversations work out of the box; swap for a
            durable saver in production.

    Returns:
        A compiled Deep Agent graph.
    """
    ensure_workspace()

    provider = provider or MockListingsProvider()
    model = model or DEFAULT_MODEL
    subagent_model = subagent_model or SUBAGENT_MODEL

    listing_tools = make_listing_tools(provider)
    market_tools = make_market_tools(provider)
    document_tools = make_document_tools()
    comms_tools = make_comms_tools(provider)

    subagents = build_subagents(
        listing_tools=listing_tools,
        market_tools=market_tools,
        document_tools=document_tools,
        comms_tools=comms_tools,
        model=subagent_model,
    )

    interrupt_on: dict[str, bool | InterruptOnConfig] | None = (
        {"save_draft": True} if require_approval else None
    )
    if checkpointer is None:
        checkpointer = InMemorySaver()

    # In 0.7.1 the planning middleware is *not* added automatically — the
    # middleware stack is resolved from a per-`provider:model` harness profile,
    # so `write_todos` may or may not exist depending on the model string.
    # The orchestrator prompt depends on it, so pin it explicitly.
    return create_deep_agent(
        model=model,
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=[TodoListMiddleware()],
        subagents=subagents,
        backend=FilesystemBackend(root_dir=PROJECT_ROOT, virtual_mode=True),
        permissions=WORKSPACE_PERMISSIONS,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        name="real-estate-agent",
    )
