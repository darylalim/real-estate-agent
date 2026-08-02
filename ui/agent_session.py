"""Streamlit-side wiring for the orchestrator.

``main.py`` opens its checkpointer with ``SqliteSaver.from_conn_string`` inside a
``with`` block, which closes the connection on exit. That shape does not survive
here: a Streamlit script reruns top to bottom on every interaction, so the saver
has to outlive the run. ``st.cache_resource`` holds one for the server's
lifetime instead, and the connection is built the same way
``from_conn_string`` builds its own -- see ``get_agent``.

The rendering helpers mirror ``main.py``'s on purpose. ``stream_mode="values"``
re-emits the entire message list on every chunk, so anything that renders a
stream has to dedupe; ``message_key`` is the same key ``main._message_key``
uses, so a thread started in the CLI renders correctly here and vice versa.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import streamlit as st
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from real_estate_agent import build_agent
from real_estate_agent.config import CHECKPOINT_DB, WORKSPACE_DIR, ensure_workspace

# Tool payloads are JSON aimed at a model, not a reader. Show enough to tell
# what came back, then stop.
_TOOL_RESULT_PREVIEW = 4000
_TOOL_ARG_PREVIEW = 160

# Readable artifacts the agent produces. Excludes the checkpoint database by
# construction: it is binary, large, and holds every other thread's transcript.
_READABLE_SUFFIXES = frozenset({".md", ".markdown", ".eml", ".txt", ".csv", ".json"})


@st.cache_resource(show_spinner="Starting the agent…")
def get_agent(require_approval: bool) -> Any:
    """Build the orchestrator, cached for the server's lifetime.

    Keyed on ``require_approval`` because that flag decides whether the
    ``save_draft`` interrupt is wired into the middleware stack at all --
    toggling it has to produce a different graph, not the cached one.

    ``check_same_thread=False`` matches what ``SqliteSaver.from_conn_string``
    does internally: Streamlit serves each session from its own thread, so the
    connection is reached from more than the one that opened it.
    ``ensure_workspace()`` runs first because ``sqlite3.connect`` will not
    create the parent directory -- the same ordering constraint ``main.py``
    documents.
    """
    ensure_workspace()
    connection = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    return build_agent(require_approval=require_approval, checkpointer=SqliteSaver(connection))


def message_key(message: BaseMessage) -> str:
    """Dedupe key for one message.

    Must match ``main._message_key`` exactly, or a thread shared between the CLI
    and this app dedupes differently in each.
    """
    return message.id or repr(message)


def _text_of(message: BaseMessage) -> str:
    """The message's text.

    ``.text`` is a ``TextAccessor``: str-like but still callable. Stringify it
    rather than calling it -- calling raises a deprecation warning.
    """
    accessor = getattr(message, "text", None)
    return str(accessor) if accessor is not None else str(message.content)


def _format_args(args: dict[str, Any]) -> str:
    """One-line preview of a tool call's arguments."""
    parts = []
    for key, value in list(args.items())[:4]:
        rendered = " ".join(str(value).split())
        if len(rendered) > _TOOL_ARG_PREVIEW:
            rendered = rendered[:_TOOL_ARG_PREVIEW] + "…"
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


def render_message(message: BaseMessage) -> None:
    """Draw one message. Silent for anything with nothing to show."""
    if isinstance(message, HumanMessage):
        with st.chat_message("user"):
            st.markdown(_text_of(message))
        return

    if isinstance(message, AIMessage):
        text = _text_of(message).strip()
        calls = message.tool_calls or []
        if not text and not calls:
            return
        with st.chat_message("assistant"):
            if text:
                st.markdown(text)
            for call in calls:
                st.caption(f"→ **{call['name']}**  ·  {_format_args(call['args'])}")
        return

    if isinstance(message, ToolMessage):
        body = str(message.content)
        clipped = body[:_TOOL_RESULT_PREVIEW]
        if len(body) > _TOOL_RESULT_PREVIEW:
            clipped += f"\n… (+{len(body) - _TOOL_RESULT_PREVIEW} chars)"
        with st.expander(f"{message.name} · {len(body):,} chars", icon=":material/database:"):
            st.code(clipped, language="json")


def stored_messages(agent: Any, config: dict[str, Any]) -> list[BaseMessage]:
    """Every message the checkpointer holds for this thread.

    The checkpoint is the transcript, so the page renders history from here
    rather than from ``st.session_state``. That is what makes pasting a thread
    id -- including one created by the CLI -- show the conversation.
    """
    values = getattr(agent.get_state(config), "values", None)
    if not isinstance(values, dict):
        return []
    return list(values.get("messages") or [])


def pending_actions(agent: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every action this thread is paused on, in order.

    Non-empty means a previous run asked for approval and never got an answer.
    ``HumanInTheLoopMiddleware`` validates that the number of decisions equals
    the number of hanging tool calls, and one interrupt can carry several -- so
    the flattening matters, not just the count.
    """
    actions: list[dict[str, Any]] = []
    for interrupt in list(getattr(agent.get_state(config), "interrupts", None) or []):
        value = getattr(interrupt, "value", None)
        requests = value.get("action_requests") if isinstance(value, dict) else None
        actions.extend(requests or [{"name": "(unknown action)", "args": {}}])
    return actions


def stream_turn(agent: Any, payload: Any, config: dict[str, Any], seen: set[str]) -> None:
    """Stream one turn, rendering each message exactly once."""
    for chunk in agent.stream(payload, config=config, stream_mode="values"):
        if not isinstance(chunk, dict):
            continue
        for message in chunk.get("messages") or []:
            key = message_key(message)
            if key in seen:
                continue
            seen.add(key)
            render_message(message)


def workspace_artifacts() -> list[Path]:
    """Readable files the agent has written, newest first."""
    if not WORKSPACE_DIR.exists():
        return []
    files = [
        path
        for path in WORKSPACE_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _READABLE_SUFFIXES
        and not path.name.startswith(".")
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
