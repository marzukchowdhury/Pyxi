"""How early does the spoken line arrive if the chat reply is streamed?

Replays a real recorded turn (its screen text and question) against OpenRouter with streaming,
and prints: time to first token, when the `say` tool's arguments were complete, and the total.
Never prints the API key. usage: python scripts/stream_probe.py <turn_id> [question]
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import httpx
from otto.config import load_dotenv

from wisp.companion.brain import THREE_D_TOOLS
from wisp.companion.prompts import system_prompt, turn_prompt
from wisp.companion.tools import COMPANION_TOOLS

CACHE = "--cache" in sys.argv
SAY_FIRST = (
    "\n\nAlways call `say` FIRST in your reply, before any drawing or other tool, so you start "
    "talking while the drawings are still being made."
    if "--say-first" in sys.argv
    else ""
)
sys.argv = [a for a in sys.argv if not a.startswith("--")]


def main() -> None:
    load_dotenv()
    turn_id = sys.argv[1]
    c = sqlite3.connect(".wisp/wisp.db")
    screen, asked = c.execute(
        "select screen_text, user_text from turns where id=?", (turn_id,)
    ).fetchone()
    question = sys.argv[2] if len(sys.argv) > 2 else asked
    system = system_prompt("Playful, warm and quick.", "(none)")
    user = turn_prompt(
        user_text=question,
        history=[],
        memories="",
        jobs="",
        screen=screen,
        notes=[],
        budget_line="",
    )
    tools = [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in COMPANION_TOOLS
        if t.name not in THREE_D_TOOLS
    ]
    body = {
        "model": "anthropic/claude-haiku-5.5",
        "stream": True,
        "max_tokens": 4000,
        "messages": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system + SAY_FIRST,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
            if CACHE
            else {"role": "system", "content": system + SAY_FIRST},
            {"role": "user", "content": user},
        ],
        "tools": tools,
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    started = time.monotonic()
    first = say_done = None
    calls: dict[int, dict] = {}
    order: list[str] = []
    cost = None
    with httpx.stream(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        json=body,
        headers=headers,
        timeout=120,
    ) as r:
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("usage"):
                cost = chunk["usage"].get("cost")
            for choice in chunk.get("choices", []):
                for tc in choice.get("delta", {}).get("tool_calls", []) or []:
                    if first is None:
                        first = time.monotonic() - started
                    slot = calls.setdefault(tc["index"], {"name": "", "args": ""})
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                        order.append(fn["name"])
                    slot["args"] += fn.get("arguments", "") or ""
                    if slot["name"] == "say" and say_done is None:
                        try:
                            json.loads(slot["args"])
                            say_done = time.monotonic() - started
                        except ValueError:
                            pass
    total = time.monotonic() - started
    print(
        f"first token {first:.2f}s | say complete {say_done if say_done is None else round(say_done, 2)}s | total {total:.2f}s | order {order} | cost {cost}"
    )


main()
