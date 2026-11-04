"""Connected accounts and document fixing, end to end with fake Google APIs (no network).

The scenario from the product brief: a friend emails a recommendation letter; Wisp finds it,
downloads it, Quill fixes the grammar into a copy beside the original, and the reply with the
fixed file goes out only after the user presses Allow.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
from typing import Any

from docx import Document
from otto.types import LLMResponse, ToolCall, Usage

from test_brain_and_server import make_brain
from wisp.connectors import Outgoing
from wisp.docs import fix_document


def _letter() -> bytes:
    doc = Document()
    doc.add_heading("Letter of Recommendation", level=1)
    doc.add_paragraph("To whom it may concern,")
    doc.add_paragraph("Nabiha have been a excellent student in my class.")
    doc.add_paragraph("I recomend her without reservation.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


EDITS = json.dumps(
    {
        "edits": [
            {"i": 2, "text": "Nabiha has been an excellent student in my class."},
            {"i": 3, "text": "I recommend her without reservation."},
        ],
        "changes": ["'have' -> 'has'", "'recomend' -> 'recommend'"],
    }
)


class Script:
    """Plays back model replies in order: tool calls (lists) or raw text (strings)."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete(self, *, system, messages, tools=None, **_):
        part = messages[-1].parts[0]
        self.prompts.append(getattr(part, "text", ""))
        reply = self.replies.pop(0) if self.replies else [("say", {"text": "Okay."})]
        if isinstance(reply, str):
            return LLMResponse(text=reply, usage=Usage())
        return LLMResponse(
            tool_calls=[ToolCall(id=str(i), name=n, arguments=a) for i, (n, a) in enumerate(reply)],
            usage=Usage(),
            stop_reason="tool_use",
        )


async def test_fixing_a_word_document_keeps_styles_and_the_original(tmp_path):
    src = tmp_path / "LOR.docx"
    src.write_bytes(_letter())
    result = await fix_document(src, "Fix grammar and spelling.", Script(EDITS))
    assert result.path.name == "LOR (fixed by Wisp).docx" and result.changed == 2
    fixed = Document(str(result.path))
    assert fixed.paragraphs[0].style.name == "Heading 1"
    assert fixed.paragraphs[2].text == "Nabiha has been an excellent student in my class."
    assert Document(str(src)).paragraphs[2].text.startswith("Nabiha have")  # untouched


class FakeGmail:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.drafts: list[dict[str, Any]] = []
        self.letter = base64.urlsafe_b64encode(_letter()).decode()

    async def __call__(self, method: str, url: str, **kw: Any) -> Any:
        headers = [
            {"name": "From", "value": "Sam Rahman <sam@example.com>"},
            {"name": "Subject", "value": "Your recommendation letter"},
            {"name": "Message-ID", "value": "<abc@mail>"},
        ]
        msg = {
            "id": "m1",
            "threadId": "t1",
            "snippet": "Here's the letter, sorry for any typos!",
            "payload": {
                "headers": headers,
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(b"Here it is!").decode()},
                    },
                    {
                        "filename": "LOR.docx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "body": {"attachmentId": "a1", "size": 9000},
                    },
                ],
            },
        }
        if method == "GET" and url.endswith("/messages"):
            return {"messages": [{"id": "m1"}]}
        if method == "GET" and "/attachments/" in url:
            return {"data": self.letter}
        if method == "GET" and url.endswith("/messages/m1"):
            return msg
        if method == "POST" and url.endswith("/drafts"):
            self.drafts.append(kw["json"])
            return {"id": "d1"}
        if method == "POST" and url.endswith("/drafts/send"):
            self.sent.append(kw["json"])
            return {}
        raise AssertionError(f"unexpected {method} {url}")


async def test_the_recommendation_letter_errand(settings, tmp_path):
    downloads = tmp_path / "Documents" / "Wisp"
    saved = downloads / "LOR.docx"
    fixed = downloads / "LOR (fixed by Wisp).docx"
    llm = Script(
        [("gmail_search", {"query": "from:sam has:attachment recommendation"})],
        [("gmail_download", {"id": "m1"})],
        [
            (
                "delegate",
                {"agent": "Quill", "task": "Fix the grammar and spelling.", "files": [str(saved)]},
            ),
            ("say", {"text": "Found it. Quill's on it."}),
        ],
        EDITS,  # Quill's edit pass
        '{"edits": [], "notes": ["The closing sentence appears twice."]}',  # the review pass
        [
            (
                "gmail_send",
                {
                    "reply_to_id": "m1",
                    "body": "Thanks Sam! Here's the corrected version.",
                    "attachments": [str(fixed)],
                },
            ),
            ("say", {"text": "Ready for you to check."}),
        ],
    )
    brain, rec = make_brain(settings, llm=llm)
    google = brain.connectors.google
    google.token_path.parent.mkdir(parents=True, exist_ok=True)
    google.token_path.write_text("{}", encoding="utf-8")  # "connected"
    google.downloads = downloads
    gmail = FakeGmail()
    google._api = gmail  # type: ignore[method-assign]

    await brain.turn("my friend Sam emailed my recommendation letter, can you fix it?")
    assert (
        "Sam Rahman" in llm.prompts[1] and "LOR.docx" in llm.prompts[1]
    )  # search results came back
    assert saved.exists()
    await brain.jobs.wait_all()
    assert fixed.exists()
    assert Document(str(fixed)).paragraphs[3].text == "I recommend her without reservation."
    assert not [s for s in rec.of("say") if s["agent"] == "Quill"]  # quiet: no voice
    [ready] = rec.of("job_ready")
    assert ready["title"] == "Your document is fixed" and ready["files"] == [str(fixed)]
    assert ready["changes"] == ["'have' -> 'has'", "'recomend' -> 'recommend'"]
    assert ready["notes"] == ["The closing sentence appears twice."]
    assert "send_back" in ready["actions"]

    await brain.turn("yes, send it back to him")
    await asyncio.sleep(0.05)
    [ask] = rec.of("confirm")
    assert (
        ask["action"] == "Send an email to sam@example.com"
        and "Attached: LOR (fixed by Wisp).docx" in ask["reason"]
    )
    assert gmail.drafts and not gmail.sent  # drafted, not sent
    brain.hub.resolve(ask["request_id"], True)
    for _ in range(50):
        if gmail.sent:
            break
        await asyncio.sleep(0.02)
    assert gmail.sent == [{"id": "d1"}]
    assert gmail.drafts[0]["message"]["threadId"] == "t1"  # threaded as a reply


async def test_declining_means_nothing_is_sent(settings):
    brain, rec = make_brain(settings)
    sent: list[str] = []

    async def run() -> str:
        sent.append("x")
        return "Sent."

    task = asyncio.ensure_future(
        brain._approve(Outgoing(action="Post on LinkedIn", preview="hi", run=run))
    )
    await asyncio.sleep(0.02)
    brain.hub.resolve(rec.of("confirm")[0]["request_id"], False)
    await task
    assert not sent and rec.of("say")[-1]["text"] == "Okay, I won't send it."


def test_unconnected_services_add_no_tools(settings, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    brain, _ = make_brain(settings)
    assert brain.connectors.tools() == []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    assert {t.spec.name for t in brain.connectors.tools()} == {
        "discord_channels",
        "discord_read",
        "discord_send",
    }
    assert not Path(settings.data_dir / "connectors" / "google.json").exists()
