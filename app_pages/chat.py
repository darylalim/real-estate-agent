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

import streamlit as st
from langgraph.types import Command

from real_estate_agent.config import (
    DEFAULT_MODEL,
    SUBAGENT_MODEL,
    WORKSPACE_DIR,
    require_api_key,
)
from ui.agent_session import (
    get_agent,
    message_key,
    pending_actions,
    render_message,
    stored_messages,
    stream_turn,
    workspace_artifacts,
)

SUGGESTIONS = {
    "Find homes": "Find 3-bed homes in Round Rock under $600k and build me a shortlist.",
    "Value a property": "What is MLS-1022 worth? Run a CMA and write it to the workspace.",
    "Read the market": "Is Austin a buyer's or a seller's market right now, and how do you know?",
    "Qualify a lead": (
        "Qualify this lead: Jane Doe, wants Round Rock, budget $550k, "
        "pre-approved, buying in 2 months, needs 3 beds. Then draft her a follow-up email."
    ),
}

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
    st.caption("Thread id — pass this to `main.py --thread` to continue in the CLI.")
    st.code(st.session_state.thread_id, language=None, wrap_lines=True)

    if st.button("New conversation", icon=":material/add_comment:", width="stretch"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.last_suggestion = None
        st.rerun()

    with st.expander("Resume a thread", icon=":material/history:"):
        candidate = st.text_input("Thread id", key="thread_input", placeholder="8f2c1e04-…")
        if st.button("Load", icon=":material/download:") and candidate.strip():
            st.session_state.thread_id = candidate.strip()
            st.rerun()

    st.toggle(
        "Require approval for drafts",
        key="require_approval",
        help=(
            "Pauses before `save_draft` writes anything, so a human decides. "
            "Rebuilds the agent — the interrupt is part of the middleware stack."
        ),
    )

    st.divider()
    st.subheader("Workspace")
    artifacts = workspace_artifacts()
    if not artifacts:
        st.caption("Nothing written yet. Specialists write here as they work.")
    else:
        names = [str(path.relative_to(WORKSPACE_DIR)) for path in artifacts]
        chosen = st.selectbox(
            "File", names, key="artifact", label_visibility="collapsed"
        )
        selected = WORKSPACE_DIR / chosen
        try:
            payload = selected.read_bytes()
        except OSError as exc:
            st.caption(f"Could not read it: {exc}")
        else:
            st.download_button(
                "Download",
                payload,
                file_name=selected.name,
                icon=":material/download:",
                width="stretch",
            )
            with st.expander("Preview", icon=":material/description:"):
                st.code(payload.decode("utf-8", errors="replace"), language="markdown")

    st.divider()
    st.caption(f"Orchestrator · `{DEFAULT_MODEL}`")
    if SUBAGENT_MODEL != DEFAULT_MODEL:
        st.caption(f"Specialists · `{SUBAGENT_MODEL}`")

agent = get_agent(st.session_state.require_approval)
config = {"configurable": {"thread_id": st.session_state.thread_id}}

actions = pending_actions(agent, config)

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

history = stored_messages(agent, config)
for message in history:
    render_message(message)
seen = {message_key(message) for message in history}

if actions:
    st.warning(
        f"Approval required — {len(actions)} action(s) pending.",
        icon=":material/pause_circle:",
    )
    with st.form("approval"):
        for index, action in enumerate(actions):
            st.markdown(f"**{index + 1} of {len(actions)} · `{action.get('name')}`**")
            for name, value in (action.get("args") or {}).items():
                st.text_area(
                    name,
                    value=str(value),
                    disabled=True,
                    key=f"arg_{index}_{name}",
                )
            st.segmented_control(
                "Decision",
                ["Reject", "Approve"],
                default="Reject",
                key=f"decision_{index}",
            )
            st.text_input("Reason (optional)", key=f"reason_{index}")
        submitted = st.form_submit_button("Submit decisions", icon=":material/gavel:")

    if submitted:
        # Exactly one decision per pending call, or the middleware rejects the
        # whole resume. Anything that is not an explicit "Approve" -- including
        # a deselected control -- is a reject.
        decisions: list[dict[str, object]] = []
        for index in range(len(actions)):
            if st.session_state.get(f"decision_{index}") == "Approve":
                decisions.append({"type": "approve"})
                continue
            reason = (st.session_state.get(f"reason_{index}") or "").strip()
            decisions.append({"type": "reject", **({"message": reason} if reason else {})})

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
    # Only rerun when the turn parked on an interrupt, because that is the one
    # case where new UI has to appear. Otherwise the streamed output stands and
    # the next interaction re-renders it from the checkpoint.
    if pending_actions(agent, config):
        st.rerun()
