"""Chat with the orchestrator.

The web analogue of ``main.py``. Two things it inherits from that file rather
than reinventing:

* **History renders from the checkpointer, not from ``st.session_state``.** The
  checkpoint *is* the transcript, so pasting a thread id -- including one the
  CLI printed -- shows the conversation. Only the in-flight turn is streamed,
  deduped on the same key ``main._pump`` uses.
* **The approval gate fails closed.** A durable checkpointer lets an interrupt
  outlive the run that raised it, but ``interrupt_on`` is only wired when
  approval is switched on. Opening such a thread with the toggle off would hand
  the graph a pending ``save_draft`` with no middleware left to stop it, so the
  page refuses to send anything instead. Anything that is not an explicit
  approve is treated as a reject.
"""

import uuid
from pathlib import Path

import streamlit as st
from langgraph.types import Command

from real_estate_agent.config import DEFAULT_MODEL, SUBAGENT_MODEL, require_api_key
from ui.agent_session import (
    get_agent,
    message_key,
    read_workspace_file,
    render_message,
    stream_turn,
    thread_snapshot,
    workspace_artifacts,
    workspace_path,
)
from ui.elements import lazy_expander, stable_key

SUGGESTIONS = {
    "Find homes": "Find 3-bed homes in Hilo under $600k and build me a shortlist.",
    "Value a property": "What is MLS-1016 worth? Run a CMA and write it to the workspace.",
    "Read the market": (
        "Is Honolulu a buyer's or a seller's market right now, and how do you know?"
    ),
    "Qualify a lead": (
        "Qualify this lead: Jane Doe, wants Hilo, budget $550k, "
        "pre-approved, buying in 2 months, needs 3 beds. Then draft her a follow-up email."
    ),
}


@st.fragment
def _workspace_browser() -> None:
    """The sidebar's artifact list, isolated from the rest of the page.

    A fragment because everything in here is a *viewer*: picking a different
    file or opening the preview changes nothing the conversation depends on.
    Without one, each of those clicks reruns the whole script — and that means
    `thread_snapshot`, which takes the checkpointer's lock and deserialises
    every message in the thread, plus a re-render of the entire transcript. On a
    long conversation that is the dominant cost of the page, paid to answer a
    question about a file.

    A turn still refreshes this. The `st.rerun()` at the foot of the page is
    app-scoped, and a full app run reruns fragments too — so the invariant that
    the sidebar never lags a turn behind is unaffected.
    """
    artifacts = workspace_artifacts()
    if not artifacts:
        st.caption("Nothing written yet. Specialists write here as they work.")
        return

    # Already relative to the workspace root, and handed straight back to
    # `read_workspace_file` — the page never does path arithmetic of its own.
    names = [str(path) for path in artifacts]
    chosen = st.selectbox("File", names, key="artifact", label_visibility="collapsed")
    try:
        payload, truncated = read_workspace_file(chosen)
    except (OSError, ValueError) as exc:
        st.caption(f"Could not read it: {exc}")
        return

    if truncated:
        st.caption(
            "Too large to serve inline — the whole file is on disk at "
            f"`{workspace_path(chosen)}`."
        )
    else:
        st.download_button(
            "Download",
            payload,
            file_name=Path(chosen).name,
            icon=":material/download:",
            width="stretch",
        )
    # Lazy so that picking a file does not ship its contents to a reader who
    # only wanted a different name in the list. `read_workspace_file` above
    # still runs -- `st.download_button` needs the bytes in hand -- so what
    # this saves is the decode and the payload.
    #
    # Keyed on the *file*, not just given some key. The label is the constant
    # "Preview", and an expander's identity is its parameters, so a single key
    # would leave one file's open state applying to the next one selected:
    # open a 2KB draft, pick a 200KB export, and it ships unasked. That is the
    # rule the widget-state family in CLAUDE.md is about -- a key has to change
    # when the thing it is asking about changes.
    #
    # Unlike the transcript's panels this costs almost nothing to toggle, since
    # the fragment scopes the rerun -- though "almost" rather than "nothing":
    # a fragment rerun re-globs the workspace and re-reads the file.
    preview = lazy_expander(
        "Preview", icon=":material/description:", key=stable_key("preview", chosen)
    )
    if preview.open:
        body = payload.decode("utf-8", errors="replace")
        if truncated:
            # The caption above just said this file is too large to serve
            # inline. Rendering the first _PREVIEW_MAX_BYTES with no marker
            # would contradict it and hand the reader a document that simply
            # stops mid-line. `render_message` marks its cut for this reason.
            body += "\n\n… truncated — read the whole file from the path above."
        with preview:
            st.code(body, language="markdown")


st.title("Chat")
st.caption(
    "The orchestrator holds no domain tools — it plans, delegates to the four "
    "specialists, and synthesises what comes back."
)

# Fail fast with an actionable message rather than a 401 part-way through a run.
try:
    require_api_key()
except RuntimeError as exc:
    st.error(str(exc), icon=":material/key_off:")
    st.stop()

st.session_state.setdefault("thread_id", str(uuid.uuid4()))
st.session_state.setdefault("require_approval", False)
st.session_state.setdefault("last_suggestion", None)

with st.sidebar:
    st.subheader("Conversation")

    # First in this block, and deliberately so. Streamlit discards a keyed
    # widget's value on any run where the widget does not render, and both
    # controls below call `st.rerun()` from inside this `with` — which aborts
    # the run before anything after them renders. With the toggle placed last,
    # clicking "New conversation" or loading a thread silently switched the
    # approval requirement back off, and the next draft would have been written
    # unattended. A safety gate that turns itself off is worse than no gate.
    # `persist_state="session"` is the belt to that braces, so a later reorder
    # cannot quietly reintroduce it.
    st.toggle(
        "Require approval for drafts",
        key="require_approval",
        persist_state="session",
        help=(
            "Pauses before `save_draft` writes anything, so a human decides. "
            "Rebuilds the agent — the interrupt is part of the middleware stack."
        ),
    )

    st.caption("Thread id — pass this to `main.py --thread` to continue in the CLI.")
    st.code(st.session_state.thread_id, language=None, wrap_lines=True)

    if st.button("New conversation", icon=":material/add_comment:", width="stretch"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.last_suggestion = None
        st.rerun()

    with st.expander("Resume a thread", icon=":material/history:"):
        # A form, because a bare text_input commits its value on blur or Enter.
        # Clicking a plain button next to one submits the *previous* value, so
        # typing an id and clicking Load did nothing until you pressed Enter
        # first -- a dead button with no error. A form commits the field and the
        # submit together.
        with st.form("resume", border=False):
            candidate = st.text_input("Thread id", placeholder="8f2c1e04-…")
            loaded = st.form_submit_button("Load", icon=":material/download:")
        if loaded and candidate.strip():
            st.session_state.thread_id = candidate.strip()
            st.session_state.last_suggestion = None
            st.rerun()

    st.divider()
    st.subheader("Workspace")
    # The sidebar has already been written to by everything above, which is what
    # lets a fragment render into it and redraw in place on its own reruns.
    _workspace_browser()

    st.divider()
    st.caption(f"Orchestrator · `{DEFAULT_MODEL}`")
    if SUBAGENT_MODEL != DEFAULT_MODEL:
        st.caption(f"Specialists · `{SUBAGENT_MODEL}`")

agent = get_agent(st.session_state.require_approval)
config = {"configurable": {"thread_id": st.session_state.thread_id}}

# One checkpoint read for both, not one each -- `get_state` deserialises the
# whole message list every call, and this script reruns on every interaction.
history, actions = thread_snapshot(agent, config)

# Nothing here ever re-approves anything: the only two outcomes are "ask now" or
# "do not proceed". Same rule as `main`, which exits 1 in this situation.
if actions and not st.session_state.require_approval:
    st.error(
        f"This thread is paused awaiting approval on {len(actions)} action(s). "
        "Switch on **Require approval for drafts** in the sidebar to answer it — "
        "without it there is no middleware left to enforce the pause.",
        icon=":material/lock:",
    )
    st.stop()

for message in history:
    render_message(message)
seen = {message_key(message) for message in history}

if actions:
    st.warning(
        f"Approval required — {len(actions)} action(s) pending.",
        icon=":material/pause_circle:",
    )
    # Widget keys carry a round number that advances on every submission. The
    # decision control is keyed, and a keyed widget's *stored* value beats the
    # `default=` on every later render -- so with keys indexed by position
    # alone, approving one call left the next interrupt's form already showing
    # "Approve", and a reviewer who read the new arguments and pressed submit
    # approved something they never chose. Same rule as the argument display
    # below, but here it defeats the fail-closed default rather than merely
    # showing stale text. Advancing the round mints keys that have never been
    # seen, so `default="Reject"` applies again; `clear_on_submit` and deleting
    # the keys were both tried first and neither restores the default.
    st.session_state.setdefault("approval_round", 0)
    approval_round = st.session_state.approval_round

    with st.form("approval"):
        for index, action in enumerate(actions):
            st.markdown(f"**{index + 1} of {len(actions)} · `{action.get('name')}`**")
            # Deliberately not widgets. A keyed st.text_area writes its first
            # value into session_state and reuses it from then on, so a second
            # interrupt at the same index re-displayed the *previous* call's
            # arguments while the decision applied to the new one -- a reviewer
            # approving text that was not what would be written. Observed live:
            # the form still showed a placeholder body after the specialist had
            # already redrafted with real figures. st.code holds no state.
            for name, value in (action.get("args") or {}).items():
                rendered = str(value)
                st.caption(name)
                st.code(
                    rendered,
                    language=None,
                    wrap_lines=True,
                    height=220 if len(rendered) > 400 else "content",
                )
            st.segmented_control(
                "Decision",
                ["Reject", "Approve"],
                default="Reject",
                key=f"decision_{approval_round}_{index}",
            )
            st.text_input("Reason (optional)", key=f"reason_{approval_round}_{index}")
        submitted = st.form_submit_button("Submit decisions", icon=":material/gavel:")

    if submitted:
        # Exactly one decision per pending call, or the middleware rejects the
        # whole resume. Anything that is not an explicit "Approve" -- including
        # a deselected control -- is a reject.
        decisions: list[dict[str, object]] = []
        for index in range(len(actions)):
            if st.session_state.get(f"decision_{approval_round}_{index}") == "Approve":
                decisions.append({"type": "approve"})
                continue
            reason = (st.session_state.get(f"reason_{approval_round}_{index}") or "").strip()
            decisions.append({"type": "reject", **({"message": reason} if reason else {})})

        # Read the decisions first, then retire this round so the next form --
        # for the next interrupt -- renders on untouched keys.
        st.session_state.approval_round += 1

        # A mapping, not a bare list: the middleware reads
        # `interrupt(request)["decisions"]`.
        stream_turn(agent, Command(resume={"decisions": decisions}), config, seen)
        st.rerun()

    st.stop()

suggestion = None
if not history:
    suggestion = st.pills(
        "Suggestions", list(SUGGESTIONS), label_visibility="collapsed", key="suggestion"
    )

prompt = st.chat_input(
    "Ask about listings, pricing, a document, or a client…", submit_mode="disable"
)

# Fire a suggestion only when the selection changes, so a rerun that leaves the
# pill selected does not resend it.
if not prompt and suggestion and suggestion != st.session_state.last_suggestion:
    st.session_state.last_suggestion = suggestion
    prompt = SUGGESTIONS[suggestion]

if prompt:
    stream_turn(agent, {"messages": [{"role": "user", "content": prompt}]}, config, seen)
    # Always, not only when an interrupt is pending. The sidebar's workspace
    # list is built near the top of the run, before the turn writes anything --
    # so without this the shortlist, CMA and drafts the specialists just created
    # stay invisible until some later, unrelated interaction, while the answer
    # already on screen refers to them by path. The cost is one repaint of
    # content re-read from the checkpoint, not re-generated.
    st.rerun()
