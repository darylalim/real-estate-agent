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
from ui.provider import get_provider

# Tool payloads are JSON aimed at a model, not a reader. Show enough to tell
# what came back, then stop.
_TOOL_RESULT_PREVIEW = 4000
_TOOL_ARG_PREVIEW = 160

# Readable artifacts the agent produces. Excludes the checkpoint database by
# construction: it is binary, large, and holds every other thread's transcript.
_READABLE_SUFFIXES = frozenset({".md", ".markdown", ".eml", ".txt", ".csv", ".json"})

# The sidebar preview is re-sent to the browser on every rerun, including reruns
# that have nothing to do with the agent. Drafts are a few KB; an agent-written
# export is under no such obligation.
_PREVIEW_MAX_BYTES = 200_000


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

    ``provider`` is passed explicitly rather than left to ``build_agent``'s
    default. That default builds its own ``MockListingsProvider``, so the chat
    agent and the market dashboard were reading two separate instances — which
    the deterministic mock makes indistinguishable, and a real feed would not:
    the analyst and the dashboard would be on different snapshots, and taking
    the agent live would need two edits instead of the one the README promises.
    """
    ensure_workspace()
    connection = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    return build_agent(
        provider=get_provider(),
        require_approval=require_approval,
        checkpointer=SqliteSaver(connection),
    )


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


def thread_snapshot(
    agent: Any, config: dict[str, Any]
) -> tuple[list[BaseMessage], list[dict[str, Any]]]:
    """``(messages, pending_actions)`` from a single checkpoint read.

    ``agent.get_state`` takes the saver's lock and deserialises the whole
    message list every call, and a Streamlit script reruns for *every*
    interaction — including ones with nothing to do with the agent, like
    changing the file selectbox. Reading the state three times per run tripled
    the dominant cost of a long thread for no benefit.
    """
    state = agent.get_state(config)

    values = getattr(state, "values", None)
    messages = list(values.get("messages") or []) if isinstance(values, dict) else []

    # HumanInTheLoopMiddleware validates that the number of decisions equals the
    # number of hanging tool calls, and one interrupt can carry several -- so
    # the flattening matters, not just the count.
    actions: list[dict[str, Any]] = []
    for interrupt in list(getattr(state, "interrupts", None) or []):
        value = getattr(interrupt, "value", None)
        requests = value.get("action_requests") if isinstance(value, dict) else None
        actions.extend(requests or [{"name": "(unknown action)", "args": {}}])

    return messages, actions


def stored_messages(agent: Any, config: dict[str, Any]) -> list[BaseMessage]:
    """Every message the checkpointer holds for this thread.

    The checkpoint is the transcript, so the page renders history from here
    rather than from ``st.session_state``. That is what makes pasting a thread
    id -- including one created by the CLI -- show the conversation.
    """
    return thread_snapshot(agent, config)[0]


def pending_actions(agent: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every action this thread is paused on, in order.

    Non-empty means a previous run asked for approval and never got an answer.
    """
    return thread_snapshot(agent, config)[1]


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
    """Readable files the agent has written, newest first, **relative** to the
    workspace root.

    Relative on purpose. The caller displays these and hands one back to
    ``read_workspace_file``; if it resolved them against its own import of
    ``WORKSPACE_DIR`` instead, the two roots could disagree — under a
    monkeypatch, a ``REA_PROJECT_ROOT`` override, or a symlinked workspace only
    one side resolves — and ``relative_to`` would raise straight out of the
    sidebar, taking the approval toggle down with it. One module owns the root.
    """
    if not WORKSPACE_DIR.exists():
        return []
    entries: list[tuple[float, Path]] = []
    for path in WORKSPACE_DIR.rglob("*"):
        if path.name.startswith(".") or path.suffix.lower() not in _READABLE_SUFFIXES:
            continue
        try:
            if not path.is_file():
                continue
            stamp = path.stat().st_mtime
        except OSError:
            # Globbed a moment ago and gone now. A specialist writing while the
            # sidebar renders is ordinary here; skipping the file beats raising
            # out of the sidebar and blanking the page.
            continue
        entries.append((stamp, path.relative_to(WORKSPACE_DIR)))
    return [path for _, path in sorted(entries, key=lambda item: item[0], reverse=True)]


def workspace_path(relative: Path | str) -> Path:
    """The absolute path of one artifact, for showing a human where it landed."""
    return WORKSPACE_DIR / relative


def read_workspace_file(relative: Path | str) -> tuple[bytes, bool]:
    """Return ``(data, truncated)`` for one artifact.

    Capped for the reason ``render_message`` caps tool output: the preview is
    re-sent to the browser on every rerun, and nothing stops a specialist
    writing a multi-megabyte export. The containment check is defence in depth —
    the name comes from a selectbox this module populated, but ``documents.py``
    resolves before reading for the same reason and this is the same class of
    path.
    """
    path = (WORKSPACE_DIR / relative).resolve()
    if not path.is_relative_to(WORKSPACE_DIR.resolve()):
        raise ValueError(f"Refusing to read {str(relative)!r}: outside the workspace.")
    with path.open("rb") as handle:
        data = handle.read(_PREVIEW_MAX_BYTES + 1)
    if len(data) > _PREVIEW_MAX_BYTES:
        return data[:_PREVIEW_MAX_BYTES], True
    return data, False
