"""CLI for the real estate agent.

    uv run python main.py "Find 3-bed homes in Austin under $700k"
    uv run python main.py                      # interactive
    uv run python main.py --require-approval   # pause before saving drafts
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any, Iterable

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.types import Command

from real_estate_agent import build_agent
from real_estate_agent.config import require_api_key

_TOOL_RESULT_PREVIEW = 400


def _render(message: BaseMessage) -> None:
    """Print one message in a compact, readable form."""
    if isinstance(message, AIMessage):
        text = message.text() if callable(getattr(message, "text", None)) else message.content
        if isinstance(text, str) and text.strip():
            print(f"\n\033[1m assistant \033[0m {text.strip()}")
        for call in message.tool_calls or []:
            args = ", ".join(f"{k}={v!r}" for k, v in list(call["args"].items())[:4])
            print(f"  \033[2m→ {call['name']}({args})\033[0m")
    elif isinstance(message, ToolMessage):
        body = str(message.content).replace("\n", " ")
        if len(body) > _TOOL_RESULT_PREVIEW:
            body = body[:_TOOL_RESULT_PREVIEW] + f"… (+{len(str(message.content)) - _TOOL_RESULT_PREVIEW} chars)"
        print(f"  \033[2m← {message.name}: {body}\033[0m")


def _pump(agent: Any, payload: Any, config: dict[str, Any], seen: set[str]) -> None:
    """Stream one turn, rendering each message exactly once."""
    for chunk in agent.stream(payload, config=config, stream_mode="values"):
        for message in _messages_of(chunk):
            key = message.id or repr(message)
            if key in seen:
                continue
            seen.add(key)
            _render(message)


def _messages_of(chunk: Any) -> Iterable[BaseMessage]:
    if isinstance(chunk, dict):
        return chunk.get("messages") or []
    return []


def _handle_interrupts(agent: Any, config: dict[str, Any], seen: set[str]) -> None:
    """If the graph paused for approval, ask and resume until it runs clean."""
    while True:
        state = agent.get_state(config)
        interrupts = getattr(state, "interrupts", None) or []
        if not interrupts:
            return

        print("\n\033[33m⏸  Approval required\033[0m")
        for interrupt in interrupts:
            print(f"   {interrupt.value}")

        answer = input("   Approve? [y/N] ").strip().lower()
        if answer in {"y", "yes"}:
            decisions: list[dict[str, Any]] = [{"type": "approve"}]
        else:
            reason = input("   Reason (optional): ").strip()
            decisions = [{"type": "reject", **({"message": reason} if reason else {})}]

        _pump(agent, Command(resume=decisions), config, seen)


def _turn(agent: Any, text: str, config: dict[str, Any], seen: set[str]) -> None:
    _pump(agent, {"messages": [{"role": "user", "content": text}]}, config, seen)
    _handle_interrupts(agent, config, seen)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real estate agent on Deep Agents.")
    parser.add_argument("prompt", nargs="*", help="Prompt. Omit for interactive mode.")
    parser.add_argument("--thread", default=None, help="Thread id, to continue a conversation.")
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="Pause for human approval before a client-facing draft is written.",
    )
    args = parser.parse_args()

    try:
        require_api_key()
    except RuntimeError as exc:
        print(f"\033[31m{exc}\033[0m", file=sys.stderr)
        return 1

    agent = build_agent(require_approval=args.require_approval)
    thread_id = args.thread or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    seen: set[str] = set()

    if args.prompt:
        _turn(agent, " ".join(args.prompt), config, seen)
        print(f"\n\033[2mthread: {thread_id}\033[0m")
        return 0

    print("Real estate agent. Ctrl-D or 'exit' to quit.")
    print(f"\033[2mthread: {thread_id}\033[0m")
    while True:
        try:
            text = input("\n\033[1myou\033[0m ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in {"exit", "quit"}:
            return 0
        if not text:
            continue
        _turn(agent, text, config, seen)


if __name__ == "__main__":
    raise SystemExit(main())
