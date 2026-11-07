"""Email requests Pyxi has to understand: who "Sam" or "my professor" is, which email "the letter
Nabiha sent last week" means, and an approval card that reads back exactly what it understood.

Everything runs against a fake mailbox (no network, nothing is ever really sent).
"""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime, timedelta
from email import message_from_bytes
from typing import Any
from zoneinfo import ZoneInfo

from pyxi.companion.errands import run_errand, unknown_addresses
from pyxi.connectors import Outgoing
from pyxi.connectors.google import GoogleConnector, build_query, date_range
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script

TZ = "America/Vancouver"
ME = "me@example.com"


def ago(days: float) -> datetime:
    return datetime.now(ZoneInfo(TZ)) - timedelta(days=days)


class FakeMailbox:
    """A small Gmail that understands the search syntax Pyxi uses (from:, to:, in:sent,
    has:attachment, filename:, after:/before:, {a b} groups and plain words)."""

    def __init__(self, mails: list[dict[str, Any]]) -> None:
        self.mails = sorted(mails, key=lambda m: m["date"], reverse=True)
        for i, m in enumerate(self.mails):
            m.setdefault("id", f"m{i}")
            m.setdefault("to", ME)
            m.setdefault("files", [])
            m.setdefault("body", "")
        self.queries: list[str] = []
        self.sent: list[dict[str, Any]] = []

    @staticmethod
    def _tokens(q: str) -> list[str]:
        return re.findall(r'\{[^}]*\}|\w+:"[^"]*"|"[^"]*"|\S+', q)

    def _match(self, m: dict[str, Any], q: str) -> bool:
        text = f"{m['subject']} {m['body']}".lower()
        for tok in self._tokens(q):
            low = tok.lower()
            if low.startswith("{"):
                if not any(w in text or w in m["from"].lower() for w in low[1:-1].split()):
                    return False
                continue
            field, _, value = low.partition(":") if ":" in low else ("", "", low)
            value = value.strip('"')
            if field == "from" and value not in m["from"].lower():
                return False
            if field == "to" and value not in m["to"].lower():
                return False
            if field == "in" and value == "sent" and not m["from"].lower().endswith(f"<{ME}>"):
                return False
            if field == "has" and value == "attachment" and not m["files"]:
                return False
            if field == "filename" and not any(value in f.lower() for f in m["files"]):
                return False
            if field in ("after", "before"):
                when = datetime.fromtimestamp(int(value)).astimezone() if value.isdigit() else None
                if when is None:
                    when = datetime.strptime(value, "%Y/%m/%d").astimezone()
                if field == "after" and m["date"] < when:
                    return False
                if field == "before" and m["date"] >= when:
                    return False
            if field == "" and value not in text:
                return False
        return True

    def _message(self, m: dict[str, Any]) -> dict[str, Any]:
        headers = [
            {"name": "From", "value": m["from"]},
            {"name": "To", "value": m["to"]},
            {"name": "Subject", "value": m["subject"]},
            {"name": "Date", "value": m["date"].strftime("%a, %d %b %Y %H:%M:%S %z")},
            {"name": "Message-ID", "value": f"<{m['id']}@mail>"},
        ]
        parts = [
            {
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(m["body"].encode()).decode()},
            }
        ]
        parts += [
            {
                "filename": f,
                "mimeType": "application/pdf",
                "body": {"attachmentId": f, "size": 2048},
            }
            for f in m["files"]
        ]
        return {
            "id": m["id"],
            "threadId": f"t-{m['id']}",
            "internalDate": str(int(m["date"].timestamp() * 1000)),
            "snippet": m["body"][:80],
            "payload": {"headers": headers, "mimeType": "multipart/mixed", "parts": parts},
        }

    async def __call__(self, method: str, url: str, **kw: Any) -> Any:
        if url.endswith("/settings/timezone"):
            return {"value": TZ}
        if method == "GET" and url.endswith("/messages"):
            q, n = kw["params"]["q"], kw["params"]["maxResults"]
            self.queries.append(q)
            return {"messages": [{"id": m["id"]} for m in self.mails if self._match(m, q)][:n]}
        if method == "GET" and "/messages/" in url:
            mid = url.rsplit("/", 1)[1]
            return self._message(next(m for m in self.mails if m["id"] == mid))
        if method == "POST" and url.endswith("/messages/send"):
            self.sent.append(kw["json"])
            return {"id": "s1"}
        raise AssertionError(f"unexpected {method} {url}")


def mailbox() -> FakeMailbox:
    return FakeMailbox(
        [
            # Two Sams: the user writes to Sam Rahman all the time, Sam Lee only wrote once.
            {
                "from": f"Me <{ME}>",
                "to": "Sam Rahman <sam.rahman@uni.edu>",
                "subject": "Notes",
                "date": ago(2),
            },
            {
                "from": f"Me <{ME}>",
                "to": "Sam Rahman <sam.rahman@uni.edu>",
                "subject": "Lunch?",
                "date": ago(9),
            },
            {
                "from": "Sam Rahman <sam.rahman@uni.edu>",
                "subject": "Re: Lunch?",
                "body": "Sure, 1pm.",
                "date": ago(8),
            },
            {
                "from": "Sam Lee <sam.lee@gmail.com>",
                "subject": "Hello from the conference",
                "date": ago(40),
            },
            # Two Alexes the user writes to equally: that one needs a question.
            {
                "from": f"Me <{ME}>",
                "to": "Alex Kim <alex.kim@work.com>",
                "subject": "Report",
                "date": ago(3),
            },
            {
                "from": f"Me <{ME}>",
                "to": "Alex Moreno <alex@moreno.net>",
                "subject": "Trip",
                "date": ago(4),
            },
            # The professor is only ever "Dr. Chen" in the mail.
            {
                "from": "Wei Chen <w.chen@uni.edu>",
                "subject": "Office hours moved",
                "body": "Dear class, Dr. Chen's office hours move to Thursday. Professor Chen",
                "date": ago(5),
            },
            {
                "from": f"Me <{ME}>",
                "to": "Wei Chen <w.chen@uni.edu>",
                "subject": "Question about the essay",
                "body": "Hi Professor Chen,",
                "date": ago(6),
            },
            # Nabiha's letter came 9 days ago, so "last week" can be off by a few days.
            {
                "from": "Nabiha Tasnim <nabihatr21@gmail.com>",
                "subject": "Recommendation",
                "body": "Here it is.",
                "files": ["LOR.pdf"],
                "date": ago(9),
            },
            {
                "from": "Amazon.ca <shipment-tracking@amazon.ca>",
                "subject": "Your order has shipped",
                "date": ago(1),
            },
            {
                "from": "Amazon.ca <shipment-tracking@amazon.ca>",
                "subject": "Your order from last year",
                "date": ago(400),
            },
        ]
    )


def connector(tmp_path, mail: FakeMailbox) -> GoogleConnector:
    g = GoogleConnector(tmp_path / "connectors", tmp_path / "downloads")
    g.email = ME
    g._api = mail  # type: ignore[method-assign]
    return g


# ---------------------------------------------------------------- time words


def test_everyday_time_words_become_exact_bounds():
    now = datetime(2026, 11, 7, 15, 30, tzinfo=ZoneInfo(TZ))  # a Saturday
    day = lambda d, m=11, y=2026: datetime(y, m, d, tzinfo=ZoneInfo(TZ))  # noqa: E731
    assert date_range("today", now) == (day(7), day(8))
    assert date_range("yesterday", now) == (day(6), day(7))
    assert date_range("last week", now) == (day(26, 10), day(2))  # Mon 26 Oct to Mon 2 Nov
    assert date_range("this week", now) == (day(2), day(8))
    assert date_range("this month", now) == (day(1), day(8))
    assert date_range("last month", now) == (day(1, 10), day(1))
    assert date_range("in October", now) == (day(1, 10), day(1))
    assert date_range("December", now) == (day(1, 12, 2025), day(1, 1, 2026))  # the last one
    assert date_range("the past 3 days", now) == (day(5), day(8))
    assert date_range("last two weeks", now) == (day(24, 10), day(8))
    assert date_range("since September", now) == (day(1, 9), day(8))
    assert date_range("on Monday", now) == (day(2), day(3))
    assert date_range("Saturday", now) == (day(7), day(8))
    assert date_range("last Saturday", now) == (day(31, 10), day(1))
    assert date_range("when it rained", now) is None


def test_plain_fields_make_a_careful_gmail_query():
    now = datetime(2026, 11, 7, 15, 30, tzinfo=ZoneInfo(TZ))
    q, note = build_query(
        {"from": "Nabiha Tasnim", "when": "last week", "has_attachment": True}, now
    )
    start, end = date_range("last week", now)
    assert q == (
        f'from:"Nabiha Tasnim" has:attachment after:{int(start.timestamp())} '
        f"before:{int(end.timestamp())}"
    )
    assert not note
    q, _ = build_query({"from": "Amazon or eBay", "filename": ".pdf", "after": "2026-10-01"}, now)
    assert q == "from:Amazon from:eBay has:attachment filename:pdf after:2026/10/01"
    q, note = build_query({"query": "label:work", "when": "around the holidays"}, now)
    assert q == "label:work" and "couldn't read the time" in note


# ---------------------------------------------------------------- searching


async def test_the_letter_nabiha_sent_last_week_is_found_even_if_it_was_nine_days_ago(tmp_path):
    mail = mailbox()
    g = connector(tmp_path, mail)
    out = await g.gmail_search(
        {"from": "Nabiha", "when": "past 7 days", "words": "letter", "has_attachment": True}
    )
    # "letter" isn't in the email and it's 9 days old: the search loosens and says so.
    assert "Recommendation" in out and "LOR.pdf" in out
    assert "from any date" in out and len(mail.queries) == 3


async def test_anything_from_amazon_this_month(tmp_path):
    g = connector(tmp_path, mailbox())
    out = await g.gmail_search({"from": "Amazon", "when": "this month"})
    if ago(1).month == datetime.now(ZoneInfo(TZ)).month:
        assert "Your order has shipped" in out
    assert "last year" not in out  # 400 days ago stays out


# ---------------------------------------------------------------- people


async def test_email_sam_picks_the_sam_they_actually_write_to(tmp_path):
    g = connector(tmp_path, mailbox())
    for said in ("Sam", "sam", "my friend Sam"):  # speech often arrives in lower case
        out = await g.gmail_people({"name": said})
        assert out.startswith("Clear match") and "Sam Rahman (sam.rahman@uni.edu)" in out, said
        assert "Sam Lee" in out  # the other one is still listed, below


async def test_two_people_written_to_equally_need_one_question(tmp_path):
    g = connector(tmp_path, mailbox())
    out = await g.gmail_people({"name": "alex"})
    assert out.startswith("Several people") and "one short question" in out
    assert "alex.kim@work.com" in out and "alex@moreno.net" in out


async def test_my_professor_is_found_by_role(tmp_path):
    g = connector(tmp_path, mailbox())
    out = await g.gmail_people({"name": "my professor"})
    assert out.startswith("Clear match") and "Wei Chen (w.chen@uni.edu)" in out
    assert ME not in out  # never the user themselves


async def test_nobody_by_that_name_means_asking_for_the_address(tmp_path):
    g = connector(tmp_path, mailbox())
    out = await g.gmail_people({"name": "Priya"})
    assert "no one like 'Priya'" in out and "Ask the user for the address" in out


# ---------------------------------------------------------------- the approval card


async def test_the_card_reads_back_who_subject_and_gist(tmp_path):
    mail = mailbox()
    g = connector(tmp_path, mail)
    await g.gmail_people({"name": "Sam"})  # learns Sam's full name from the mail
    out = await g.gmail_send(
        {
            "to": "sam.rahman@uni.edu",
            "subject": "Running late",
            "gist": "Running about 10 minutes late to lunch.",
            "body": "Hi Sam,\n\nRunning about 10 minutes late, sorry!\n\nTay",
        }
    )
    assert isinstance(out, Outgoing) and not mail.sent  # nothing goes out before approval
    assert out.action == "Send an email to Sam Rahman (sam.rahman@uni.edu)"
    assert out.preview.startswith(
        "To: Sam Rahman (sam.rahman@uni.edu)\nSubject: Running late\n"
        "Gist: Running about 10 minutes late to lunch.\n\nHi Sam,"
    )
    assert out.recipients == ["sam.rahman@uni.edu"]
    assert await out.run() == 'Sent to Sam Rahman (sam.rahman@uni.edu): "Running late".'
    [sent] = mail.sent
    email = message_from_bytes(base64.urlsafe_b64decode(sent["raw"] + "=="))
    assert email["To"] == '"Sam Rahman" <sam.rahman@uni.edu>'


async def test_a_reply_card_says_what_it_replies_to(tmp_path):
    mail = mailbox()
    g = connector(tmp_path, mail)
    lunch = next(m for m in mail.mails if m["subject"] == "Re: Lunch?")
    out = await g.gmail_send({"reply_to_id": lunch["id"], "body": "1pm works. See you then!"})
    assert out.action == "Send an email to Sam Rahman (sam.rahman@uni.edu)"
    assert "Subject: Re: Lunch?" in out.preview and "Replying to: Re: Lunch?" in out.preview
    assert "Gist: 1pm works." in out.preview  # no gist given: the first sentence


async def test_send_refuses_what_it_cant_read_back(tmp_path):
    g = connector(tmp_path, mailbox())
    assert "who should it go to" in await g.gmail_send({"subject": "x", "body": "hi"})
    assert "aren't email addresses" in await g.gmail_send(
        {"to": "Sam", "subject": "x", "body": "hi"}
    )
    assert "short subject" in await g.gmail_send({"to": "sam@uni.edu", "body": "hi"})


# ---------------------------------------------------------------- errands


async def _errand(tmp_path, said: str, task: str, *steps: Any, approve: bool = True):
    mail = mailbox()
    g = connector(tmp_path, mail)
    cards: list[Outgoing] = []

    async def ok(out: Outgoing) -> bool:
        cards.append(out)
        return approve

    llm = Script(*steps)
    result = await run_errand(
        task=task, user_text=said, now="Now: Saturday", llm=llm, tools=g.tools(), approve=ok
    )
    return result, cards, mail, llm


async def test_email_sam_that_im_late_end_to_end(tmp_path):
    result, [card], mail, llm = await _errand(
        tmp_path,
        "email sam that I'm running 10 minutes late",
        "Email Sam that the user is running 10 minutes late.",
        [("gmail_people", {"name": "sam"})],
        [
            (
                "gmail_send",
                {
                    "to": "Sam Rahman <sam.rahman@uni.edu>",
                    "subject": "Running late",
                    "gist": "Running 10 minutes late.",
                    "body": "Hi Sam, running 10 minutes late, sorry!",
                },
            )
        ],
    )
    assert "To: Sam Rahman (sam.rahman@uni.edu)" in card.preview
    assert "Gist: Running 10 minutes late." in card.preview
    assert result.ok and result.title == "Email sent" and len(mail.sent) == 1
    assert result.summary == 'Sent to Sam Rahman (sam.rahman@uni.edu): "Running late".'
    assert "Clear match" in llm.prompts[1]  # the lookup reached the model in full


async def test_a_made_up_address_is_never_sent_to(tmp_path):
    result, cards, mail, llm = await _errand(
        tmp_path,
        "email Priya the slides",
        "Email Priya the slides.",
        [("gmail_send", {"to": "priya@gmail.com", "subject": "Slides", "body": "Here you go."})],
        [
            (
                "finish",
                {
                    "ok": False,
                    "title": "Need Priya's address",
                    "question": "What's Priya's email address?",
                },
            )
        ],
    )
    assert not cards and not mail.sent  # not even shown: nobody said or found that address
    assert "isn't from the user or their mail" in llm.prompts[1]
    assert result.question == "What's Priya's email address?" and not result.ok


async def test_several_matches_ask_one_short_question(tmp_path):
    question = "Which Alex: Alex Kim (alex.kim@work.com) or Alex Moreno (alex@moreno.net)?"
    result, cards, mail, _ = await _errand(
        tmp_path,
        "send alex the report",
        "Email Alex the report.",
        [("gmail_people", {"name": "alex"})],
        [("finish", {"ok": True, "title": "Which Alex?", "summary": "", "question": question})],
    )
    assert not cards and not mail.sent
    assert not result.ok and result.title == "Which Alex?" and result.summary == question


async def test_reply_to_my_professor(tmp_path):
    mail = mailbox()
    chen = next(m for m in mail.mails if m["subject"] == "Office hours moved")
    result, [card], mail, _ = await _errand(
        tmp_path,
        "reply to my professor and say thanks, Thursday works",
        "Reply to the user's professor: thanks, Thursday works.",
        [("gmail_people", {"name": "my professor"})],
        [("gmail_search", {"from": "w.chen@uni.edu", "max": 1})],
        [
            (
                "gmail_send",
                {
                    "reply_to_id": chen["id"],
                    "gist": "Thanks, Thursday works.",
                    "body": "Thank you, Thursday works for me.",
                },
            )
        ],
    )
    assert card.action == "Send an email to Wei Chen (w.chen@uni.edu)"
    assert "Replying to: Office hours moved" in card.preview
    assert result.ok and len(mail.sent) == 1


async def test_declining_the_card_sends_nothing(tmp_path):
    result, [_card], mail, llm = await _errand(
        tmp_path,
        "email sam.rahman@uni.edu: see you at 1",
        "Email sam.rahman@uni.edu: see you at 1.",
        [("gmail_send", {"to": "sam.rahman@uni.edu", "subject": "1pm", "body": "See you at 1!"})],
        [
            (
                "finish",
                {"ok": False, "title": "Not sent", "summary": "You said no, so it wasn't sent."},
            )
        ],
        approve=False,
    )
    assert not mail.sent and not result.ok and "said no" in llm.prompts[1]


def test_unknown_addresses():
    seen = "email sam\nClear match for 'sam': Sam Rahman (sam.rahman@uni.edu)"
    assert unknown_addresses(["Sam.Rahman@uni.edu"], seen) == []
    assert unknown_addresses(["sam@gmail.com"], seen) == ["sam@gmail.com"]


async def test_the_question_reaches_the_notch(settings):
    """Through the brain: the conversation starts the errand without asking for an address,
    and the errand's one question is what the user is told."""
    question = "Which Alex: Alex Kim (alex.kim@work.com) or Alex Moreno (alex@moreno.net)?"
    llm = Script(
        [("errand", {"task": "Email Alex the report."}), ("say", {"text": "On it."})],
        [("gmail_people", {"name": "alex"})],
        [("finish", {"ok": False, "title": "Which Alex?", "summary": "", "question": question})],
    )
    brain, rec = make_brain(settings, llm=llm)
    g = brain.connectors.google
    g.token_path.parent.mkdir(parents=True, exist_ok=True)
    g.token_path.write_text("{}", encoding="utf-8")
    g.email = ME
    g._api = mailbox()  # type: ignore[method-assign]
    await brain.turn("send alex the report")
    for _ in range(100):
        if rec.of("job_ready"):
            break
        await asyncio.sleep(0.02)
    [ready] = rec.of("job_ready")
    assert not ready["ok"] and ready["title"] == "Which Alex?" and ready["body"] == question
    assert not rec.of("confirm")
    assert any(question in said for _, said in brain.history)  # so the next reply can answer it
