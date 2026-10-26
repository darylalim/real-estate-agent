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
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from real_estate_agent.config import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    PROJECT_ROOT,
    ROLE_EFFORTS,
    ROLE_MODELS,
    SUBAGENT_ROLES,
    ensure_workspace,
    provider_of,
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

When an answer includes a valuation, say in one plain sentence whether the list
price is above, within, or below the value the comparable sales support. The
figures alone are not the verdict; the buyer acts on the verdict.

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
    # First, because the allow below would otherwise match it. The checkpoint
    # store lives inside /workspace/ (it is gitignored there), which is also the
    # one subtree the agent can write -- so without this the model can truncate
    # its own conversation history mid-session, and `read_file` on it returns
    # every *other* thread's full transcript. Same reasoning as the .env deny:
    # gitignored is not the same as out of reach. Any SQLite file anywhere
    # under /workspace/, not only that name: a copy of the store holds the same
    # transcripts, and the 2026-10-25 upgrade backup in workspace/backup-*/ was
    # readable and writable under the name-only rule. `**` also matches no
    # directory, so this covers the store itself. Exact suffixes -- the db and
    # SQLite's -wal, -shm and -journal sidecars, as damaging to clobber -- so a
    # file like notes.db.md stays usable.
    FilesystemPermission(
        operations=["read", "write"],
        paths=[
            "/workspace/**/*.db",
            "/workspace/**/*.db-wal",
            "/workspace/**/*.db-shm",
            "/workspace/**/*.db-journal",
        ],
        mode="deny",
    ),
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/**"], mode="allow"),
    FilesystemPermission(operations=["read"], paths=["/skills/**"], mode="allow"),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/.env", "/.env.*", "/.venv/**", "/.git/**"],
        mode="deny",
    ),
    FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
]


def chat_model(spec: str, effort: str | None) -> str | BaseChatModel:
    """``spec`` with ``effort`` applied, or ``spec`` unchanged when there is none.

    No effort keeps the plain string, so the default path is exactly what it
    was before efforts existed. A spec that is already a model (the test
    suite's scripted fakes) is returned as it is: there is nothing to apply an
    effort to.

    On Anthropic models whose profile lists ``xhigh``, langchain-anthropic pairs
    an effort with ``thinking: {"type": "adaptive", "display": "summarized"}``.
    The no-effort baseline omits ``thinking``, which the API runs as adaptive
    with ``display: "omitted"``, so left alone an effort would also switch on
    summarised reasoning -- two changes where one was asked for. Plain
    ``adaptive`` is that omitted default, stated explicitly.
    ``test_an_effort_changes_the_effort_and_nothing_else`` checks the payload.
    """
    if effort is None or not isinstance(spec, str):
        return spec
    model = init_chat_model(spec, reasoning_effort=effort)
    levels = (getattr(model, "profile", None) or {}).get("reasoning_effort_levels") or ()
    if provider_of(spec) == "anthropic" and "xhigh" in levels:
        model = init_chat_model(spec, reasoning_effort=effort, thinking={"type": "adaptive"})
    return model


def build_agent(
    *,
    provider: ListingsProvider | None = None,
    model: str | None = None,
    subagent_model: str | None = None,
    effort: str | None = None,
    subagent_effort: str | None = None,
    require_approval: bool = False,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Construct the orchestrator agent.

    Args:
        provider: Listings data source. Defaults to the deterministic mock,
            which is what tests want. The entry points pass
            ``providers.default_provider()`` instead, so taking the agent live
            is an edit there, not here.
        model: Orchestrator model, as a LangChain ``provider:model`` string.
        subagent_model: Model for every specialist. Explicit arguments always
            win over the environment, per-role variables included. When
            omitted, each specialist takes its ``REA_MODEL_<ROLE>``, then
            ``REA_SUBAGENT_MODEL``, then ``REA_MODEL`` -- the environment's
            orchestrator model, not this call's ``model``.
        effort: Orchestrator effort (``low`` .. ``max``). Defaults to
            ``REA_EFFORT``; unset sends none, so the API default applies.
        subagent_effort: Effort for every specialist, with the same precedence
            as ``subagent_model``: when omitted, ``REA_EFFORT_<ROLE>``, then
            ``REA_SUBAGENT_EFFORT``, then ``REA_EFFORT``.
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
    effort = effort or DEFAULT_EFFORT
    # Read at call time, not bound as defaults, so tests can patch them.
    role_models = dict.fromkeys(SUBAGENT_ROLES, subagent_model) if subagent_model else ROLE_MODELS
    role_efforts = (
        dict.fromkeys(SUBAGENT_ROLES, subagent_effort) if subagent_effort else ROLE_EFFORTS
    )

    listing_tools = make_listing_tools(provider)
    market_tools = make_market_tools(provider)
    document_tools = make_document_tools()
    comms_tools = make_comms_tools(provider)

    subagents = build_subagents(
        listing_tools=listing_tools,
        market_tools=market_tools,
        document_tools=document_tools,
        comms_tools=comms_tools,
        models={
            role: chat_model(spec, role_efforts.get(role)) for role, spec in role_models.items()
        },
    )

    interrupt_on: dict[str, bool | InterruptOnConfig] | None = (
        {"save_draft": True} if require_approval else None
    )
    if checkpointer is None:
        checkpointer = InMemorySaver()

    # The planning middleware is *not* added automatically — verified on 0.7.1
    # and again on 0.7.8, 0.7.21 and 0.7.32. The middleware stack is resolved from a
    # per-`provider:model` harness profile, so `write_todos` may or may not
    # exist depending on the model string. The orchestrator prompt depends on
    # it, so pin it explicitly.
    return create_deep_agent(
        model=chat_model(model, effort),
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=[TodoListMiddleware()],
        subagents=subagents,
        backend=FilesystemBackend(root_dir=PROJECT_ROOT, virtual_mode=True),
        permissions=WORKSPACE_PERMISSIONS,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        name="real-estate-agent",
    )
