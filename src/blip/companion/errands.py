"""Errands: work Blip does in the background with your connected accounts.

"Set up a meeting with nabihatr21@gmail.com tomorrow at 3" becomes: Blip says "On it", and a
quiet loop does the work with the account tools (check the calendar, create the event, read
it back), then finishes with a result for the notch: what was done, when, and the link.

Mistakes are designed out rather than hoped away:
* the exact date, time, time zone and next two weeks are in every step;
* only addresses the user actually said can be invited without asking (checked in code, against
  the user's own words, not the model's retelling); anything else, and every email or post,
  shows an approval card;
* the calendar tool rejects times in the past and bad addresses, and reads the event back;
* if something essential is missing, the errand stops and says exactly what it needs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from otto.llm.base import LLMProvider, Message, ToolSpec

from blip.connectors import Outgoing, Tool

MAX_STEPS = 7

SYSTEM = """\
You are Blip, doing an errand in the background with the user's connected accounts. Nobody is \
watching, so be careful and exact; a wrong invite or email can't be taken back.

Rules:
- Use only the email addresses, names, dates and times the user gave (see "What the user \
said"). Never invent or guess an address.
- Dates and times: work them out from the Now line and the list of upcoming days. Write \
times as local times like 2026-11-06T15:00, in the user's time zone. "Tomorrow" means the \
day after today in that list.
- Before creating a meeting, look at the calendar around that time (calendar_list) and \
mention a clash in your summary if there is one.
- After you create or send something, the tool's result reads it back. Check it matches the \
task (title, day, date, time, people). Never create it a second time to "fix" it: if it's \
wrong, finish with ok=false and say exactly what's wrong.
- If something essential is missing or ambiguous, don't guess: finish with ok=false and say \
exactly what you need to know.
- When the work is done, call finish once: ok, a short title (e.g. "Meeting set with \
Nabiha"), and a summary of one or two plain sentences with the exact day, date and time.
"""

FINISH = ToolSpec(
    "finish",
    "Report the result of the errand to the user (once, at the end).",
    {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "title": {"type": "string", "description": "3-6 words."},
            "summary": {
                "type": "string",
                "description": "One or two sentences, with exact day, date and time.",
            },
        },
        "required": ["ok", "title", "summary"],
    },
)

LINK = re.compile(r"\b(meet|link): (https://\S+)")
ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class ErrandResult:
    ok: bool
    title: str
    summary: str
    link: str | None = None  # the most useful link (a Google Meet link beats an event link)
    links: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0
    steps: list[str] = field(default_factory=list)


def said_by_user(recipients: list[str], user_text: str) -> bool:
    """True only if every recipient's address appears in what the user actually said."""
    said = {a.lower() for a in ADDRESS.findall(user_text)}
    return bool(recipients) and all(r.lower() in said for r in recipients)


async def run_errand(
    *,
    task: str,
    user_text: str,
    now: str,
    llm: LLMProvider,
    tools: list[Tool],
    approve: Callable[[Outgoing], Awaitable[bool]],
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> ErrandResult:
    by_name = {t.spec.name: t for t in tools}
    steps: list[str] = []
    links: dict[str, str] = {}
    cost = 0.0
    sent = 0  # outgoing things already done (an invite, an email)
    for _ in range(MAX_STEPS):
        done = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) or "(nothing yet)"
        prompt = f"{now}\n\n# Errand\n{task}\n\n# What the user said\n{user_text}\n\n# Steps so far\n{done}"
        try:
            response = await llm.complete(
                system=SYSTEM,
                messages=[Message.user(prompt)],
                tools=[*(t.spec for t in tools), FINISH],
                max_tokens=2000,
            )
        except Exception:
            if sent:
                # It already happened; don't tell the user it failed (they'd retry and duplicate).
                return ErrandResult(
                    ok=True,
                    title="Done",
                    summary="That's done, but I couldn't double-check the details. Have a quick look.",
                    link=links.get("meet") or links.get("link"),
                    links=links,
                    cost_usd=cost,
                    steps=steps,
                )
            raise
        cost += response.usage.cost_usd
        if not response.tool_calls:
            steps.append(f"(no tool call) {response.text[:200]}")
            continue
        for call in response.tool_calls:
            args = dict(call.arguments)
            if call.name == "finish":
                link = links.get("meet") or links.get("link")
                return ErrandResult(
                    ok=bool(args.get("ok")),
                    title=str(args.get("title") or "Done")[:60],
                    summary=str(args.get("summary") or "")[:300],
                    link=link,
                    links=links,
                    cost_usd=cost,
                    steps=steps,
                )
            tool = by_name.get(call.name)
            if tool is None:
                steps.append(f"{call.name}: unknown tool")
                continue
            if progress:
                await progress(_progress_line(call.name))
            try:
                result = await tool.handler(args)
            except Exception as exc:
                steps.append(f"{call.name}({_short(args)}) failed: {str(exc)[:200]}")
                continue
            if isinstance(result, Outgoing):
                # An invite to people the user named themselves goes ahead; anything else asks.
                # Only the first one: a second invite or email always asks, so a "fix it" retry
                # can never quietly send the same thing twice.
                allowed = (
                    not sent
                    and result.kind == "invite"
                    and said_by_user(result.recipients, user_text)
                )
                if not allowed and not await approve(result):
                    steps.append(
                        f"{call.name}: the user said no to '{result.action}'. Don't retry it."
                    )
                    continue
                try:
                    result = await result.run()
                    sent += 1
                except Exception as exc:
                    steps.append(f"{call.name}({_short(args)}) failed: {str(exc)[:200]}")
                    continue
            text = str(result)
            for kind, url in LINK.findall(text):
                links.setdefault(kind, url)
            steps.append(f"{call.name}({_short(args)}) -> {text[:600]}")
    return ErrandResult(
        ok=False,
        title="Couldn't finish",
        summary="I ran out of steps before finishing. Want me to try again?",
        links=links,
        cost_usd=cost,
        steps=steps,
    )


def _short(args: dict[str, Any]) -> str:
    return json.dumps(args, default=str)[:240]


def _progress_line(tool: str) -> str:
    return {
        "calendar_list": "Checking your calendar",
        "calendar_add": "Setting it up",
        "gmail_search": "Looking through your email",
        "gmail_read": "Reading the email",
        "gmail_send": "Writing the email",
        "docs_create": "Creating the doc",
    }.get(tool, "Working on it")
