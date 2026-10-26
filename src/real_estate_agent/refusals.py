"""Safety-classifier refusals: retry them server-side, and say so when none recovers.

Opus 5.5 and Sonnet 5.5 can end a turn with ``stop_reason: "refusal"`` -- HTTP
200, empty content, a ``stop_details.category``. Unhandled, a refused specialist
hands the orchestrator an empty string and the run carries on without it.

Two halves, deliberately on different objects:

- ``FallbackChatAnthropic`` asks the API to re-run a declined request on the
  model Anthropic recommends for that refusal category (``fallbacks:
  "default"``). It lives on the *model* because every graph that holds the model
  needs its payload fix -- including deepagents' built-in ``general-purpose``
  subagent, which runs on the orchestrator's model but inherits none of our
  middleware.
- ``RefusalNoticeMiddleware`` turns a refusal nothing recovered into text, so
  the CLI and the chat page show it and the orchestrator learns why a specialist
  came back empty.

langchain-anthropic 1.7.6 has no ``fallbacks`` field; ``model_kwargs`` carries
it, and any ``betas`` routes the call through ``client.beta.messages``, which
accepts it. Measured offline against a mock transport: the body carries
``fallbacks: "default"`` and the ``anthropic-beta`` header names the beta.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage

FALLBACK_BETA = "server-side-fallback-2026-07-01"
"""Gates the ``"default"`` form. The array form takes ``-2026-06-01``, and either
header paired with the other form is a 400 -- don't "update" one to the other."""

SERVER_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
"""Models whose ``"default"`` fallback does something. Haiku 5.5 has none, and a
fallback list on it is a 400. Sonnet 5.5 retries only ``cyber`` and
``frontier_llm`` declines, on the Claude API only -- which ``anthropic:`` is."""

REFUSAL_NOTICE = (
    "Declined: the model's safety classifier stopped this response (category: "
    "{category}), and no fallback model completed it. Nothing was produced for "
    "this step. Rephrase the request, or continue without it."
)


def wants_server_fallback(spec: str) -> bool:
    """True for an Anthropic ``provider:model`` spec named in ``SERVER_FALLBACK_MODELS``.

    A bare name counts as Anthropic, as ``config.provider_of`` reads it.
    """
    provider, _, name = spec.rpartition(":")
    return provider in ("", "anthropic") and name in SERVER_FALLBACK_MODELS


class FallbackChatAnthropic(ChatAnthropic):
    """``ChatAnthropic`` that sends ``fallbacks: "default"`` and can replay its own output.

    The replay is the part that needs a subclass. langchain stores a response's
    ``fallback`` block with pydantic's ``model_dump()``, which writes the field
    name ``from_`` rather than the wire alias ``from``, and sends the block back
    verbatim on the next call -- so the turn after any fallback would be a
    malformed request. The API treats the block as an audit marker that may be
    kept or dropped, so it is dropped here, on every path that builds a payload.
    """

    def __init__(self, **kwargs: Any) -> None:
        betas = [*(kwargs.pop("betas", None) or ()), FALLBACK_BETA]
        model_kwargs = {**(kwargs.pop("model_kwargs", None) or {}), "fallbacks": "default"}
        super().__init__(betas=betas, model_kwargs=model_kwargs, **kwargs)

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        for message in payload.get("messages", []):
            content = message.get("content")
            if isinstance(content, list):
                message["content"] = [
                    block
                    for block in content
                    if not (isinstance(block, dict) and block.get("type") == "fallback")
                ]
        return payload


def _noticed(message: AIMessage) -> AIMessage:
    if message.response_metadata.get("stop_reason") != "refusal":
        return message
    details = message.response_metadata.get("stop_details") or {}
    # A refusal mid-output may leave a partial and even tool calls behind;
    # neither is something to act on.
    return message.model_copy(
        update={
            "content": REFUSAL_NOTICE.format(category=details.get("category") or "not given"),
            "tool_calls": [],
            "invalid_tool_calls": [],
        }
    )


def _with_notice(response: ModelResponse) -> ModelResponse:
    return ModelResponse(
        result=[_noticed(m) if isinstance(m, AIMessage) else m for m in response.result],
        structured_response=response.structured_response,
    )


class RefusalNoticeMiddleware(AgentMiddleware):
    """Replace a refused, empty reply with ``REFUSAL_NOTICE``.

    Runs on the orchestrator and on each specialist. A specialist's last message
    is what the ``task`` tool hands back, so the notice is how the orchestrator
    hears that a step was declined rather than silently empty.
    """

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return _with_notice(handler(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return _with_notice(await handler(request))
