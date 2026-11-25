"""Meeting notes with no bot in the call.

Pyxi hears the call the way they do: what the PC plays ("Others") and their own microphone
("You"), through :mod:`pyxi.listen.live`. Nothing joins the meeting.

* **Who spoke.** Two voices only: "You" is the microphone, "Others" is everyone the PC plays,
  together. A name is used only when someone says it. Without headphones the microphone hears
  the speakers too: a "You" line that repeats what "Others" just said is dropped.
* **Long meetings.** Every ten minutes or so of talk, that part is turned into notes while
  the meeting goes on (one small model call). The end only merges those notes, so no step
  ever reads the whole transcript, and an hour costs a few cents.
* **What is kept.** The transcript and the notes, as PDFs in ``Documents\\Pyxi\\Meetings``.
  No sound is ever saved. If the notes can't be written (no internet, Stop pressed while they
  were being written) the transcript is still saved, with the notes made so far.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pyxi import safety
from pyxi.agents.scribe import Notes, clean, parse_notes, unique
from pyxi.listen.live import MIC, Heard

log = logging.getLogger(__name__)

PART_CHARS = 12000  # about ten minutes of talk, 3k tokens: one small call
MERGE_CHARS = 20000  # the most part-notes one merging call reads
YOU, OTHERS = "You", "Others"

_WHO = (
    "The transcript has two speaker labels only. 'You' is the user (their own microphone). "
    "'Others' is everyone else on the call, mixed together: it can be several people. Never "
    "guess which person said a thing. Use a person's name only when it is said in the "
    "transcript; an action whose owner was not named gets who=\"\" (or \"You\" when the "
    "user took it on themselves). Only what was really said, no guesses. Plain sentences, no "
    "em dashes, no markdown. 'due' is the day as a date whenever a day or date was named for "
    "the task ('by Friday', 'before Wednesday', 'on the 5th': work it out from today's "
    "date); empty when none was."
)

PART = (
    "You turn one part of a live meeting's transcript into notes. Reply with JSON only:\n"
    '{"summary": ["1-3 short sentences"], "decisions": ["what was decided"], "actions": '
    '[{"who": "name or empty", "what": "the task", "when": "as it was said, or empty", '
    '"due": "YYYY-MM-DD when a day was named, else empty"}], "questions": ["a question '
    'raised and left open"]}\n' + _WHO
)

FINAL = (
    "You write the notes of a meeting. Reply with JSON only:\n"
    '{"title": "short name of the meeting", "summary": ["2-4 short sentences"], '
    '"decisions": ["what was decided"], "actions": [{"who": "name or empty", "what": "the '
    'task", "when": "as it was said, or empty", "due": "YYYY-MM-DD when a day was named, '
    'else empty"}], "questions": ["a question raised and left open"], "follow_up": '
    '{"subject": "...", "body": "..."}}\n' + _WHO + " The follow-up email is from the user to "
    "the other people in the meeting: thanks in one line, the decisions, the action items "
    "with owners and dates, the open questions, short and friendly."
)

MERGE = (
    "\n\nYou are given notes of consecutive parts of one meeting, not its transcript. Merge "
    "them into one set of notes in the JSON shape above, dropping repeats. A question that a "
    "later part answered is no longer open. Only what is in the parts."
)


@dataclass
class Line:
    at: float  # Unix time it was said
    who: str
    text: str

    def stamp(self, start: float) -> str:
        s = max(0, int(self.at - start))
        return f"[{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}]"

    def render(self, start: float) -> str:
        return f"{self.stamp(start)} {self.who}: {self.text}"


@dataclass
class Result:
    ok: bool
    title: str
    body: str
    notes: Notes | None = None
    files: list[str] = field(default_factory=list)
    minutes: int = 0
    cost_usd: float = 0.0
    partial: bool = False  # the notes are from the parts made so far (stopped, or no model)


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def same_words(a: str, b: str) -> bool:
    """Is ``a`` (heard by the microphone) what the speakers just played (``b``)?"""
    wa, wb = _words(a), _words(b)
    if len(wa) < 3 or not wb:
        return False
    if difflib.SequenceMatcher(None, wa, wb).ratio() >= 0.7:
        return True
    # The microphone often catches only part of it: most of its words, in order, inside b.
    joined = " ".join(wb)
    hits = sum(1 for i in range(len(wa) - 2) if " ".join(wa[i : i + 3]) in joined)
    return hits >= max(1, (len(wa) - 2) * 0.6)


def merge_locally(parts: list[Notes], title: str = "Meeting notes") -> Notes:
    """The parts' notes put end to end, repeats dropped: for when no model can merge them."""

    def once(items: list[str]) -> list[str]:
        seen, out = set(), []
        for item in items:
            key = " ".join(_words(item))
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        return out

    actions, seen = [], set()
    for p in parts:
        for a in p.actions:
            key = " ".join(_words(a.get("what", "")))
            if key and key not in seen:
                seen.add(key)
                actions.append(a)
    return Notes(
        title=next((p.title for p in parts if p.title and p.title != "Meeting notes"), title),
        summary=once([s for p in parts for s in p.summary]),
        decisions=once([d for p in parts for d in p.decisions]),
        actions=actions,
        questions=once([q for p in parts for q in p.questions]),
    )


def due_date(action: dict[str, str], today: datetime | None = None) -> datetime | None:
    """The day an action item is due, when the notes named one that hasn't passed."""
    raw = str(action.get("due") or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None
    try:
        day = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 9, 0)
    except ValueError:
        return None
    now = today or datetime.now()
    return day if day.date() >= now.date() else None


def notes_markdown(notes: Notes, started: float, minutes: int, partial: bool = False) -> str:
    when = datetime.fromtimestamp(started)
    text = notes.markdown(f"a call on {when:%A} {when.day} {when:%B %Y}, {when:%H:%M}", minutes)
    text = text.replace("- [ ] ", "- ")  # a PDF has no boxes to tick
    if partial:
        text += (
            "\nThese notes are not complete: they were stopped before they were finished. "
            "The full transcript is saved beside them.\n"
        )
    return text


def transcript_markdown(lines: list[Line], started: float, title: str) -> str:
    when = datetime.fromtimestamp(started)
    out = [
        f"# {title}: transcript",
        "",
        f"{when:%A} {when.day} {when:%B %Y}, from {when:%H:%M}. 'You' is your microphone; "
        "'Others' is everyone the PC played, together. Written by a speech model on this PC: "
        "names and unusual words can be wrong.",
        "",
    ]
    out += [f"{line.stamp(started)} **{line.who}:** {line.text}  " for line in lines]
    return "\n".join(out) + "\n"


def write_pdf(markdown: str, path: Path) -> None:
    """The same PDF a teammate's draft becomes (Pixel's own renderer)."""
    from pyxi.agents import pixel_render

    pixel_render.render(pixel_render.markdown_blocks(markdown), path, title=path.stem)


def safe_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    return " ".join(name.split())[:60].strip(" .") or "Meeting"


class Meeting:
    """One meeting being noted. ``hear`` takes the words; ``finish`` writes the notes."""

    def __init__(
        self,
        llm: Any,
        *,
        about: str = "",
        folder: Path | None = None,
        now: float | None = None,
        part_chars: int = PART_CHARS,
    ) -> None:
        self.llm = llm
        self.about = about.strip()
        self.folder = folder
        self.started = now if now is not None else time.time()
        self.part_chars = part_chars
        self.lines: list[Line] = []
        self.parts: list[Notes] = []
        self.cost = 0.0
        self.problem = ""  # why a part's notes failed, in plain words
        self._noted = 0  # lines[:_noted] are already in a part's notes
        self._noting: asyncio.Task[None] | None = None
        self._failed_parts = 0
        self.incomplete = False  # the end of the meeting is missing from the notes

    # ------------------------------------------------------------------ hearing
    def hear(self, heard: Heard) -> Line | None:
        """A finished stretch of speech. Returns the transcript line it became, if any."""
        if not heard.final or not heard.text.strip():
            return None
        who = YOU if heard.source == MIC else OTHERS
        text = clean(heard.text)
        near = [ln for ln in self.lines[-8:] if abs(ln.at - heard.at) < 8.0 and ln.who != who]
        if who == YOU and any(same_words(text, ln.text) for ln in near):
            return None  # the microphone heard the speakers
        busy = self._noting is not None and not self._noting.done()
        if who == OTHERS and not busy:  # ...and the other way round: the echo came first
            for ln in near:
                if same_words(ln.text, text) and ln in self.lines[self._noted :]:
                    self.lines.remove(ln)
        last = self.lines[-1] if self.lines else None
        if (
            last is not None
            and last.who == who
            and heard.at - last.at < 20.0
            and len(last.text) + len(text) < 400
            and len(self.lines) - 1 >= self._noted
        ):
            last.text = f"{last.text} {text}"  # one speaker going on: one paragraph
            line = last
        else:
            line = Line(heard.at, who, text)
            self.lines.append(line)
        self._maybe_note()
        return line

    @property
    def minutes(self) -> int:
        end = self.lines[-1].at if self.lines else self.started
        return max(1, round((end - self.started) / 60))

    def _waiting(self) -> list[str]:
        return [ln.render(self.started) for ln in self.lines[self._noted :]]

    def _maybe_note(self) -> None:
        if self._noting is not None and not self._noting.done():
            return
        if sum(len(x) + 1 for x in self._waiting()) < self.part_chars:
            return
        try:
            self._noting = asyncio.get_running_loop().create_task(self._note_part())
        except RuntimeError:  # no loop (a plain test): the end writes the notes
            pass

    # ------------------------------------------------------------------ the model
    def _context(self) -> str:
        when = datetime.fromtimestamp(self.started)
        text = f"A live meeting on {when:%A} {when.day} {when:%B %Y} (today), from {when:%H:%M}."
        if self.about:
            text += f" The user says it's about: {self.about}."
        return text

    async def _ask(self, system: str, source: str, text: str, max_tokens: int) -> Notes | None:
        from otto.llm.base import Message

        resp = await self.llm.complete(
            system=system + safety.READ_ONLY_RULE,
            messages=[Message.user(f"{self._context()}\n\n" + safety.fence(source, text))],
            max_tokens=max_tokens,
        )
        self.cost += float(getattr(resp.usage, "cost_usd", 0.0) or 0.0)
        return parse_notes(resp.text)

    async def _note_part(self, whole: bool = False) -> None:
        """Turn the lines not yet noted into one part's notes."""
        upto = len(self.lines)
        # The line still being added to stays for the next part (not at the end: ``whole``).
        if not whole and upto - self._noted > 1:
            upto -= 1
        text = "\n".join(ln.render(self.started) for ln in self.lines[self._noted : upto])
        if not text.strip():
            return
        n = len(self.parts) + 1
        try:
            notes = await self._ask(PART, "the transcript", f"Part {n}.\n{text}", 1200)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from pyxi.agents.jobs import describe_failure

            log.warning("meeting: part %d's notes failed: %s", n, exc)
            self.problem = describe_failure(exc)
            self._failed_parts += 1
            if self._failed_parts < 3:
                return  # those lines wait for the next try (the end, at the latest)
            notes = None  # it keeps failing: stop asking, the transcript has it all
        if notes is not None:
            self.parts.append(notes)
        self._noted = upto

    async def finish(self, *, model: bool = True) -> Result:
        """The meeting is over: write the notes and save the files. Cancelling this (Stop
        pressed while the notes are written) still saves the transcript, with the notes of
        the parts already made; ``model`` False does the same without asking a model at all."""
        if not self.lines:
            return Result(
                False,
                "I didn't hear anyone speak",
                "Nothing was said that I could hear, so there are no notes and nothing was "
                "saved. If the call was playing, check it was playing on this PC's speakers "
                "or headphones.",
            )
        notes: Notes | None = None
        partial = False
        try:
            if not model:
                if self._noting is not None and not self._noting.done():
                    self._noting.cancel()
                raise asyncio.CancelledError
            if self._noting is not None and not self._noting.done():
                await self._noting
            notes = await self._final_notes()
        except asyncio.CancelledError:
            partial = True
            self.problem = self.problem or "Stopped before the notes were finished"
        except Exception as exc:
            from pyxi.agents.jobs import describe_failure

            log.exception("meeting: the notes failed")
            self.problem = describe_failure(exc)
        if notes is None:
            partial = True
            notes = merge_locally(self.parts) if self.parts else None
        partial = partial or self.incomplete
        return await asyncio.shield(asyncio.to_thread(self._save, notes, partial))

    async def _final_notes(self) -> Notes | None:
        waiting = "\n".join(self._waiting())
        if not self.parts:
            # A short meeting: one call reads it all (it is under one part long, or a part's
            # notes failed and this is the second try).
            if len(waiting) > self.part_chars * 2:
                await self._drain()
            else:
                return await self._ask(FINAL, "the transcript", waiting, 2500)
        elif waiting.strip():
            await self._note_part(whole=True)
            self.incomplete = self._noted < len(self.lines)  # the last part's notes failed
        if not self.parts:
            return None
        return await self._merge(self.parts)

    async def _drain(self) -> None:
        """Parts were never noted (the model was unreachable during the meeting): note them
        now, a part at a time, so no call reads more than a part."""
        while self._noted < len(self.lines):
            before, start = self._noted, self._noted
            size = 0
            upto = start
            while upto < len(self.lines) and size < self.part_chars:
                size += len(self.lines[upto].render(self.started)) + 1
                upto += 1
            text = "\n".join(ln.render(self.started) for ln in self.lines[start:upto])
            notes = await self._ask(PART, "the transcript", f"Part {len(self.parts) + 1}.\n{text}", 1200)
            if notes is not None:
                self.parts.append(notes)
            self._noted = upto
            if self._noted == before:
                break

    async def _merge(self, parts: list[Notes]) -> Notes | None:
        def dump(ps: list[Notes]) -> str:
            return json.dumps(
                [
                    {k: v for k, v in p.__dict__.items() if v and k not in ("subject", "body", "title")}
                    for p in ps
                ],
                ensure_ascii=False,
            )

        # Far more parts than one call should read (a meeting of many hours): halve them first.
        while len(dump(parts)) > MERGE_CHARS and len(parts) > 2:
            half: list[Notes] = []
            for i in range(0, len(parts), 4):
                group = parts[i : i + 4]
                merged = await self._ask(PART + MERGE, "the parts' notes", dump(group), 1500)
                half.append(merged or merge_locally(group))
            parts = half
        return await self._ask(FINAL + MERGE, "the parts' notes", dump(parts), 2500)

    # ------------------------------------------------------------------ the files
    def _save(self, notes: Notes | None, partial: bool) -> Result:
        folder = self.folder or (Path.home() / "Documents" / "Pyxi" / "Meetings")
        folder.mkdir(parents=True, exist_ok=True)
        when = datetime.fromtimestamp(self.started)
        title = (notes.title if notes and notes.title else "") or self.about or "Meeting"
        if title == "Meeting notes":
            title = self.about or "Meeting"
        base = f"{safe_name(title)} {when:%Y-%m-%d %H.%M}"
        files: list[str] = []
        script = unique(folder, f"{base} transcript.pdf")
        write_pdf(transcript_markdown(self.lines, self.started, title), script)
        minutes = self.minutes
        if notes is None:
            why = (self.problem or "The notes didn't come out right").rstrip(".")
            return Result(
                False,
                "Transcript saved, no notes yet",
                f"{why}. The transcript of the meeting ({minutes} min) is saved as "
                f"{script.name}, so nothing that was said is lost.",
                files=[str(script)],
                minutes=minutes,
                cost_usd=self.cost,
                partial=True,
            )
        notes.title = title
        note_path = unique(folder, f"{base} notes.pdf")
        write_pdf(notes_markdown(notes, self.started, minutes, partial), note_path)
        files = [str(note_path), str(script)]
        n_d, n_a, n_q = len(notes.decisions), len(notes.actions), len(notes.questions)
        body = (
            f"{minutes} min. {n_d} decision{'s' if n_d != 1 else ''}, "
            f"{n_a} action item{'s' if n_a != 1 else ''}, "
            f"{n_q} open question{'s' if n_q != 1 else ''}."
        )
        if partial:
            why = (self.problem or "They were stopped early").rstrip(".")
            body += f" {why}, so these notes cover only part of it; the transcript has it all."
        return Result(
            True,
            f"Notes: {title}"[:70],
            body,
            notes=notes,
            files=files,
            minutes=minutes,
            cost_usd=self.cost,
            partial=partial,
        )


def card(notes: Notes) -> dict[str, Any]:
    """The notes card: summary, decisions, action items (with owners), open questions."""
    sections = [{"heading": "Summary", "lines": notes.summary[:4] or ["Nothing to summarise."]}]
    if notes.decisions:
        sections.append({"heading": "Decisions", "lines": notes.decisions[:8]})
    if notes.actions:
        sections.append(
            {"heading": "Action items", "lines": [notes.action_line(a) for a in notes.actions[:8]]}
        )
    if notes.questions:
        sections.append({"heading": "Open questions", "lines": notes.questions[:8]})
    return {"title": notes.title, "sections": sections}
