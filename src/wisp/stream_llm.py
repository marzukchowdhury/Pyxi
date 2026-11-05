"""A streaming, prompt-cached chat model for Wisp's conversation (OpenRouter).

Two things make a voice buddy feel quick, and the plain request/response client does neither:

* **Talk before the reply is finished.** The reply carries the spoken line and the drawing
  instructions (a cue card, underlines). Streamed, Wisp can start speaking the moment the
  `say` call is complete, while the card is still arriving, instead of waiting for all of it.
* **Don't re-read the manual every turn.** Wisp's instructions and tools are the same on every
  turn; marked for prompt caching they're read once and reused, which also roughly halves the
  cost of a turn.

Same interface as Otto's providers (``complete`` returns an ``LLMResponse``), plus an optional
``on_call`` callback that fires as soon as each tool call is complete.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from otto.config import OPENROUTER_BASE_URL
from otto.llm.base import Message, ToolSpec
from otto.llm.openai_provider import _to_openai
from otto.types import LLMResponse, ToolCall, Usage

log = logging.getLogger(__name__)

OnCall = Callable[[ToolCall], Awaitable[None]]
OnPartial = Callable[[int, str, str], Awaitable[None]]  # (index, tool name, arguments so far)


class StreamingChat:
    """OpenRouter chat completions, streamed, with the system prompt cached."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.client = openai.AsyncOpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY") or "missing-openrouter-key",
            base_url=OPENROUTER_BASE_URL,
            max_retries=1,
            default_headers={"X-Title": "Wisp"},
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        on_call: OnCall | None = None,
        on_partial: OnPartial | None = None,
    ) -> LLMResponse:
        req: dict[str, Any] = {
            "model": self.model,
            "stream": True,
            "max_tokens": min(max_tokens or 8000, 16_000),
            "messages": [
                # Tools and the system prompt are the stable prefix: cache it.
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                    ],
                },
                *[_to_openai(m) for m in messages],
            ],
            "extra_body": {"usage": {"include": True}},
        }
        if tools:
            req["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            req["tool_choice"] = "auto"

        text = ""
        slots: dict[int, dict[str, Any]] = {}
        fired: set[int] = set()
        usage = Usage()
        stop: str | None = None

        async def fire(i: int) -> None:
            if i in fired or on_call is None:
                return
            call = _parse(slots[i], i)
            if call is not None:
                fired.add(i)
                await on_call(call)

        stream = await self.client.chat.completions.create(**req)
        async for chunk in stream:
            if chunk.usage:
                usage = _usage(chunk.usage)
            for choice in chunk.choices or []:
                stop = choice.finish_reason or stop
                delta = choice.delta
                if delta is None:
                    continue
                text += delta.content or ""
                for tc in delta.tool_calls or []:
                    i = tc.index if tc.index is not None else max(slots, default=0)
                    if tc.index is None and tc.id and slots.get(i, {}).get("id") not in ("", tc.id):
                        i += 1  # a provider without indexes: a new id starts a new call
                    # A new call starting means the ones before it are complete.
                    for j in list(slots):
                        if j < i:
                            await fire(j)
                    slot = slots.setdefault(i, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function is not None:
                        slot["name"] = tc.function.name or slot["name"]
                        slot["args"] += tc.function.arguments or ""
                    if on_partial is not None and slot["name"] and i not in fired:
                        await on_partial(i, slot["name"], slot["args"])
                    # The spoken line is worth starting the moment its arguments parse.
                    if slot["name"] == "say" and i not in fired and _complete_json(slot["args"]):
                        await fire(i)
        for i in sorted(slots):
            await fire(i)
        calls = [c for i in sorted(slots) if (c := _parse(slots[i], i)) is not None]
        return LLMResponse(
            text=text,
            tool_calls=calls,
            usage=usage,
            stop_reason="tool_use" if calls else stop,
            model=self.model,
        )


def _complete_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


def _parse(slot: dict[str, Any], i: int) -> ToolCall | None:
    if not slot["name"]:
        return None
    try:
        args = json.loads(slot["args"] or "{}")
    except ValueError:
        log.warning("tool %s sent unparseable arguments", slot["name"])
        # Flag it rather than drop it, so the brain asks for the call again.
        return ToolCall(
            id=slot["id"] or f"call_{i}", name=slot["name"], arguments={"__unparseable__": True}
        )
    return ToolCall(
        id=slot["id"] or f"call_{i}",
        name=slot["name"],
        arguments=args if isinstance(args, dict) else {},
    )


def _usage(u: Any) -> Usage:
    extra = getattr(u, "model_extra", None) or {}
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    cost = extra.get("cost", getattr(u, "cost", 0.0)) or 0.0
    return Usage(
        input_tokens=max(0, (u.prompt_tokens or 0) - cached),
        output_tokens=u.completion_tokens or 0,
        cache_read_tokens=cached,
        cost_usd=float(cost),
    )
