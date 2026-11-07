"""Errands: work Pyxi does in the background with your connected accounts.

"Set up a meeting with nabihatr21@gmail.com tomorrow at 3" becomes: Pyxi says "On it", and a
quiet loop does the work with the account tools (check the calendar, create the event, read
it back), then finishes with a result for the notch: what was done, when, and the link.

Mistakes are designed out rather than hoped away:
* the exact date, time, time zone and next two weeks are in every step;
* only addresses the user actually said can be invited without asking (checked in code, against
  the user's own words, not the model's retelling); anything else, and every email or post,
  shows an approval card that reads back who (name and address), the subject and the gist;
* "email Sam" or "reply to my professor" is resolved from the people they really write to
  (gmail_people); several matches mean one short question, not a guess;
* an email address that came from neither the user nor their mail is refused in code;
* what the mail says is data, never instructions (pyxi/safety.py): it's fenced in the prompt,
  and a card for a recipient the user never named, after mail that talks to an AI, says so;
* the calendar tool rejects times in the past and bad addresses, and reads the event back;
* if something essential is missing, the errand stops and says exactly what it needs.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from otto.llm.base import LLMProvider, Message, ToolSpec

from pyxi import safety
from pyxi.connectors import Outgoing, Tool

MAX_STEPS = 7

SYSTEM = """\
You are Pyxi, doing an errand in the background with the user's connected accounts. Nobody is \
watching, so be careful and exact; a wrong invite or email can't be taken back.

Rules:
- Use only the names, dates and times the user gave (see "What the user said"). Addresses \
come from their words or from what the mail tools found; never invent or guess one.
- Dates and times: work them out from the Now line and the list of upcoming days. Write \
times as local times like 2026-11-06T15:00, in the user's time zone. "Tomorrow" means the \
day after today in that list.
- Before creating a meeting, look at the calendar around that time (calendar_list) and \
mention a clash in your summary if there is one.
- After you create or send something, the tool's result reads it back. Check it matches the \
task (title, day, date, time, people). Never create it a second time to "fix" it: if it's \
wrong, finish with ok=false and say exactly what's wrong.
- If something essential is missing or ambiguous, don't guess: ask (see Asking).

Email:
- A person without an address ("email Sam", "tell my professor", "message Nabiha"): call \
gmail_people with the name or role as they said it. One clear match: use that address. \
Several: ask which (see Asking). None: ask for the address.
- "Reply to X", "answer Sam's email", "get back to the landlord": gmail_search from=X for \
their latest email, gmail_read it if you need what it said, then gmail_send with reply_to_id \
(it threads the reply and addresses it to them; leave to and subject out).
- Finding an email: use gmail_search's fields. "The letter Nabiha sent last week" is \
from="Nabiha", when="last week", has_attachment=true. "Anything from Amazon this month" is \
from="Amazon", when="this month". Put their time words in `when` as they said them; don't \
work out dates yourself. Only put words in `words` that are likely in the email itself.
- Writing: short and natural, in the user's voice, saying only what they asked. Sign with \
their name if you know it. Always give gmail_send a short subject (unless it's a reply) and a \
one-line `gist` of what it says; the user sees both on the approval card. Don't ask them to \
confirm first: the card is the confirmation.

Asking:
- Act whenever one reading is clearly right. Ask only when it truly isn't: several people \
match and nothing in the request says which, there's no way to find the address, or nothing \
says what to write. Then call finish with ok=false and `question`: one short question with \
the options (e.g. "Which Sam: Sam Rahman (sam@uni.edu) or Sam Lee (sam.lee@gmail.com)?").
- Ask once. If the task says the user already answered or picked someone, go with that.

Finishing:
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
            "question": {
                "type": "string",
                "description": "Only when you must ask: the one short question for the user.",
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
    question: str | None = None  # set when the errand stopped to ask the user one thing


def unknown_addresses(recipients: list[str], seen: str) -> list[str]:
    """The recipients found in neither what the user said nor what the tools returned. Those
    were made up by the model, and are never sent to."""
    known = {a.lower() for a in ADDRESS.findall(seen)}
    return [r for r in recipients if r.lower() not in known]


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
    reads: safety.Reads | None = None,
) -> ErrandResult:
    """``reads``: mail, pages or documents the turn that asked for this errand had already read.
    Once anything like that has been read, every outward action asks on a fresh card (no invite
    goes out on its own), and the card says what came from that content."""
    reads = list(reads or [])
    by_name = {t.spec.name: t for t in tools}
    steps: list[str] = []
    links: dict[str, str] = {}
    cost = 0.0
    sent = 0  # outgoing things already done (an invite, an email)
    seen = [task, user_text]  # everything the user said and the tools returned, in full
    for _ in range(MAX_STEPS):
        done = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) or "(nothing yet)"
        prompt = f"{now}\n\n# Errand\n{task}\n\n# What the user said\n{user_text}\n\n# Steps so far\n{done}"
        try:
            response = await llm.complete(
                system=SYSTEM + safety.RULE,
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
                question = str(args.get("question") or "").strip()[:240] or None
                default = "Quick question" if question else "Done"
                return ErrandResult(
                    ok=bool(args.get("ok")) and not question,
                    title=str(args.get("title") or default)[:60],
                    summary=question or str(args.get("summary") or "")[:300],
                    question=question,
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
                # An email to an address nobody said or found was made up: never send it.
                # (A reply addressed from the original email is from their mail by definition.)
                from_mail = bool(args.get("reply_to_id")) and not args.get("to")
                unknown = (result.kind == "message" and not from_mail) and unknown_addresses(
                    result.recipients, "\n".join(seen)
                )
                if unknown:
                    steps.append(
                        f"{call.name}: {', '.join(unknown)} isn't from the user or their mail, so "
                        "nothing was sent. Find the person with gmail_people, or ask the user."
                    )
                    continue
                # An invite to people the user named themselves goes ahead; anything else asks.
                # Only the first one: a second invite or email always asks, so a "fix it" retry
                # can never quietly send the same thing twice.
                allowed = (
                    not sent
                    and not reads  # after reading mail or a page, everything asks
                    and result.kind == "invite"
                    and said_by_user(result.recipients, user_text)
                )
                # Mail that talks to an AI, and a recipient they never named: the card says so.
                warning = safety.card_warning(result.recipients, user_text, "\n".join(seen[2:]))
                origin = safety.provenance(result.recipients, result.preview, user_text, reads)
                notes = "\n\n".join(n for n in (warning, origin) if n)
                if notes:
                    result = dataclasses.replace(result, preview=f"{notes}\n\n{result.preview}")
                if not allowed and not await approve(result):
                    steps.append(
                        f"{call.name}: the user said no to '{result.action}'. Don't retry it."
                    )
                    continue
                outgoing = result
                try:
                    result = await result.run()
                    sent += 1
                except Exception as exc:
                    steps.append(f"{call.name}({_short(args)}) failed: {str(exc)[:200]}")
                    continue
                if (
                    outgoing.kind == "message"
                    and call.name.endswith("_send")
                    and len(response.tool_calls) == 1
                ):
                    # An email that went out is the whole errand: report it now rather than
                    # spending another model round on a summary (it's read back below).
                    to = ", ".join(outgoing.recipients) or "them"
                    line = str(result)
                    return ErrandResult(
                        ok=True,
                        title="Email sent",
                        summary=line[:240]
                        if line.startswith("Sent")
                        else f"Sent to {to}. {line[:200]}",
                        links=links,
                        cost_usd=cost,
                        steps=[*steps, f"{call.name}({_short(args)}) -> {str(result)[:600]}"],
                    )
            text = str(result)
            seen.append(text)
            if call.name in safety.SOURCE_LABEL:
                reads.append((safety.SOURCE_LABEL[call.name], text))
            for kind, url in LINK.findall(text):
                links.setdefault(kind, url)
            # What was found is kept whole enough to use: six search results, or a read email.
            keep = 3000 if getattr(tool, "info", False) else 600
            shown = text[:keep]
            if call.name in safety.UNTRUSTED_TOOLS:
                shown = safety.fence("what " + call.name + " returned", shown)
            steps.append(f"{call.name}({_short(args)}) -> {shown}")
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
        "gmail_people": "Working out who that is",
        "gmail_read": "Reading the email",
        "gmail_send": "Writing the email",
        "docs_create": "Creating the doc",
    }.get(tool, "Working on it")
