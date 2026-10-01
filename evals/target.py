"""Run the agent on one dataset example and record what it said and did.

The output is shaped for ``evals.evaluators``: the final answer for the fact
and rubric checks, and every tool call -- the specialists' included -- for the
trajectory checks.

**Capturing the specialists' calls needs ``subgraphs=True``.** Measured on
deepagents 0.7.21 with a scripted model in the real graph: the checkpointed
state, which is what ``stream_mode="values"`` replays and what the CLI renders,
holds the orchestrator's messages only. A specialist's ``search_listings`` call
never appears in it -- the orchestrator sees just the ``task`` result. With
``subgraphs=True`` each specialist's messages stream under a ``tools:<uuid>``
namespace, and every ``AIMessage`` carries the producing agent's name, so
attribution needs no bookkeeping of which namespace belongs to which ``task``.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from real_estate_agent.config import WORKSPACE_DIR, ensure_workspace, run_config

# The `name=` that `build_agent` passes to `create_deep_agent`; specialists'
# messages carry their own subagent names instead.
ORCHESTRATOR = "real-estate-agent"


def _messages(update: object) -> Iterable[BaseMessage]:
    """The messages in one node's update, or nothing for non-message nodes."""
    if not isinstance(update, dict):
        return ()
    messages = update.get("messages", ())
    # A reducer can hand back a wrapper rather than the list itself.
    messages = getattr(messages, "value", messages)
    if not isinstance(messages, list):
        return ()
    return [message for message in messages if isinstance(message, BaseMessage)]


def capture(agent: Any, query: str, config: dict[str, Any]) -> dict[str, Any]:
    """Stream one turn and return ``{"answer", "delegations", "tool_calls"}``.

    ``tool_calls`` is in call order across every agent, each entry
    ``{"agent", "name", "args", "status"}``. ``status`` is the matching
    ``ToolMessage``'s -- ``"error"`` for a permission denial -- or None if no
    result came back.
    """
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    answer = ""
    stream = agent.stream(
        {"messages": [{"role": "user", "content": query}]},
        config=config,
        stream_mode="updates",
        subgraphs=True,
    )
    for namespace, chunk in stream:
        for update in (chunk or {}).values():
            for message in _messages(update):
                if isinstance(message, AIMessage):
                    for call in message.tool_calls:
                        record = {
                            "agent": message.name or ORCHESTRATOR,
                            "name": call["name"],
                            "args": call["args"],
                            "status": None,
                        }
                        calls.append(record)
                        if call_id := call.get("id"):
                            by_id[call_id] = record
                    # The last root-level message with text is the reply; a
                    # specialist's text is what it reported to the orchestrator.
                    if not namespace and message.text:
                        answer = message.text
                elif isinstance(message, ToolMessage):
                    record = by_id.get(message.tool_call_id)
                    if record is not None:
                        record["status"] = message.status

    delegations = [
        call["args"].get("subagent_type")
        for call in calls
        if call["name"] == "task" and call["agent"] == ORCHESTRATOR
    ]
    return {"answer": answer, "delegations": delegations, "tool_calls": calls}


def make_target(agent: Any) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The function ``langsmith.evaluate`` calls once per example.

    Each example starts from an empty workspace. The specialists read their own
    files back -- property-search re-reads ``/workspace/shortlist.md`` before
    editing it -- so a shared workspace leaks one example's answer into the
    next, and the score then depends on run order.

    Emptying a workspace is destructive, so this refuses outright unless the
    configured one lives under the system temp directory -- which it does only
    when ``evals.run_experiments`` has pointed ``REA_PROJECT_ROOT`` at a root it
    created. The check is made here, at construction, not inside the loop: a
    real ``workspace/`` holds drafts and every thread's checkpoint.
    """
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not WORKSPACE_DIR.resolve().is_relative_to(temp_root):
        raise RuntimeError(
            f"Refusing to evaluate against {WORKSPACE_DIR}: each example empties the "
            "workspace, and this one is not under the temp directory. Run "
            "`python -m evals.run_experiments`, which sets REA_PROJECT_ROOT first."
        )

    def run(inputs: dict[str, Any]) -> dict[str, Any]:
        shutil.rmtree(WORKSPACE_DIR, ignore_errors=True)
        ensure_workspace()
        config = run_config(
            f"eval-{uuid.uuid4()}", entry_point="eval", require_approval=False
        )
        return capture(agent, inputs["query"], config)

    return run
