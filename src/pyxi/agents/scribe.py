"""Scribe, meetings: a recording in, notes, decisions, action items and a follow-up email out.

* **Transcribed here.** The recording (any audio or video file FFmpeg reads: .m4a, .mp3, .wav,
  .mp4, .webm...) goes through the same local faster-whisper Pyxi uses for push-to-talk
  (:mod:`pyxi.voice.stt`), with timestamps. Nothing is uploaded for that.
* **Notes from one small model call.** The transcript (split into parts only when a meeting is
  long) becomes a title, a short summary, the decisions, the action items (who, what, by when)
  and a follow-up email. Nothing in the notes may be made up: an action without an owner
  says so.
* **Saved beside nothing of theirs.** ``Documents\\Pyxi\\Meetings\\<name> notes.md`` and the
  full transcript next to it. The recording itself is never changed.
* **The follow-up is only a draft.** The brain shows it on an approval card
  (``companion/specialists.py``); it is sent only if they press Allow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi import safety

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

log = logging.getLogger(__name__)

AUDIO = {
    ".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".oga", ".opus", ".mkv", ".mov", ".aac",
    ".flac", ".wma", ".avi",
}  # fmt: skip
PART_CHARS = 24000  # about 6k tokens: one call for most meetings

SYSTEM = """You turn a meeting transcript into notes. Reply with JSON only:
{"title": "short name of the meeting", "summary": ["2-4 short sentences"],
 "decisions": ["what was decided"], "actions": [{"who": "name or empty", "what": "the task",
 "when": "due date or empty"}], "follow_up": {"subject": "...", "body": "..."}}
Rules: only what was really said. No guesses: an action with no clear owner gets who="".
Use the speakers' own names when they say them. The follow-up email is from the user to the
other people in the meeting: thanks in one line, the decisions, the action items with owners
and dates, short and friendly. Plain sentences, no em dashes, no markdown in the email."""

MERGE = """These are notes from consecutive parts of one meeting. Merge them into one set of
notes in exactly the same JSON shape, dropping repeats. Only what is in the parts."""


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def line(self) -> str:
        s = int(self.start)
        return f"[{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}] {self.text.strip()}"


@dataclass
class Notes:
    title: str
    summary: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    subject: str = ""
    body: str = ""

    def action_line(self, a: dict[str, str]) -> str:
        who = a.get("who") or "No owner yet"
        when = str(a.get("when") or "").strip()
        if re.match(r"(?:By|Before|This|Next|End|Tomorrow|Today|Tonight|Later|Early|Mid)\b", when):
            when = when[0].lower() + when[1:]  # "(by this week)", not "(by This week)"
        if when:  # the model's own "by Friday" or "before the launch": no "by by"
            when = f" ({when})" if re.match(r"(?:by|before)\b", when) else f" (by {when})"
        return f"{who}: {a.get('what', '').rstrip('.')}{when}"

    def markdown(self, source: str, minutes: int) -> str:
        out = [f"# {self.title}", "", f"From {source}, {minutes} min.", "", "## Summary", ""]
        out += [f"{s}" for s in self.summary] or ["(nothing to summarise)"]
        out += ["", "## Decisions", ""] + (
            [f"- {d}" for d in self.decisions] or ["- None recorded"]
        )
        out += ["", "## Action items", ""]
        out += [f"- [ ] {self.action_line(a)}" for a in self.actions] or ["- None recorded"]
        if self.body:
            out += [
                "",
                "## Follow-up email (draft, not sent)",
                "",
                f"Subject: {self.subject}",
                "",
                self.body,
            ]
        return "\n".join(out) + "\n"


def clean(text: str) -> str:
    """No em or en dashes in what they read."""
    return re.sub(r"\s*[—–]\s*", ", ", str(text)).strip()


def parse_notes(raw: str) -> Notes | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    fu = data.get("follow_up") if isinstance(data.get("follow_up"), dict) else {}

    def strings(v: Any) -> list[str]:
        return [clean(x) for x in v if str(x).strip()] if isinstance(v, list) else []

    actions = [
        {k: clean(a.get(k) or "") for k in ("who", "what", "when")}
        for a in data.get("actions") or []
        if isinstance(a, dict) and str(a.get("what") or "").strip()
    ]
    return Notes(
        title=clean(data.get("title") or "Meeting notes")[:80],
        summary=strings(data.get("summary")),
        decisions=strings(data.get("decisions")),
        actions=actions,
        subject=clean(fu.get("subject") or ""),
        body=clean(fu.get("body") or "").replace(", ,", ","),
    )


def parts(lines: list[str], size: int = PART_CHARS) -> list[str]:
    out, cur, n = [], [], 0
    for line in lines:
        if n + len(line) > size and cur:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def unique(folder: Path, name: str) -> Path:
    path, n = folder / name, 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def out_dir() -> Path:
    return Path.home() / "Documents" / "Pyxi" / "Meetings"


# ------------------------------------------------------------------------------ transcribing
async def transcribe(stt: Any, path: Path, language: str, progress: Any = None) -> list[Segment]:
    """Timestamped segments, from the warm faster-whisper model. ``progress(done_s, total_s)``.
    The model stays loaded until the whole recording is done (Pyxi lets idle models go)."""
    hold = getattr(stt, "hold", None)
    if hold is None:
        return await _transcribe(await stt.warm_up(language), stt, path, language, progress)
    async with hold(language) as model:
        return await _transcribe(model, stt, path, language, progress)


async def _transcribe(
    model: Any, stt: Any, path: Path, language: str, progress: Any
) -> list[Segment]:
    from pyxi.voice.stt import decode_audio

    name = stt.model_for(language)
    try:  # read from the file itself: a two-hour video never sits in memory twice
        audio = await asyncio.to_thread(decode_audio, path)
    except Exception:  # damaged, empty, or a video with no sound: never PyAV's own words
        raise RuntimeError(
            f"I couldn't read the sound in {path.name}. It may be damaged, or have no audio."
        ) from None
    total = len(audio) / 16000
    loop = asyncio.get_running_loop()

    def run() -> list[Segment]:
        lang = "en" if name.endswith(".en") else (None if language == "auto" else language)
        segments, _ = model.transcribe(audio, language=lang, vad_filter=True, beam_size=1)
        out = []
        for s in segments:
            out.append(Segment(float(s.start), float(s.end), str(s.text)))
            if progress:
                asyncio.run_coroutine_threadsafe(progress(s.end, total), loop)
        return out

    return await asyncio.to_thread(run)


# ------------------------------------------------------------------------------ the job
async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    from otto.llm.base import Message

    path = Path(job.files[0]) if job.files else None
    if path is None or not path.is_file():
        raise RuntimeError("I couldn't find that recording.")
    if path.suffix.lower() not in AUDIO:
        raise RuntimeError(f"{path.name} isn't an audio or video file I can listen to.")
    stt = getattr(manager, "stt", None)
    if stt is None:
        from pyxi.voice.stt import Transcriber

        stt = manager.stt = Transcriber(manager.settings.voice)
    await manager._status(job, f"Listening to {path.name}")
    last = {"pct": -1}

    async def progress(done: float, total: float) -> None:
        pct = int(100 * done / total) if total else 0
        if pct >= last["pct"] + 10:
            last["pct"] = pct
            await manager._status(job, f"Transcribing, {pct}%")

    segments = await transcribe(stt, path, str(job.params.get("language") or "en"), progress)
    if not segments:
        job.status, job.summary = "failed", f"I couldn't hear any speech in {path.name}."
        job.data = {"title": "No speech in that recording", "body": job.summary}
        return
    minutes = max(1, round(segments[-1].end / 60))
    lines = [s.line() for s in segments]
    # The transcript is saved now, before the notes are asked for: when that call failed
    # (the model busy, a reply that wasn't notes) an hour of listening was thrown away.
    folder = Path(job.params.get("folder") or out_dir())
    folder.mkdir(parents=True, exist_ok=True)
    script_path = unique(folder, f"{path.stem} transcript.txt")
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    job.outputs = [str(script_path)]
    await manager._status(job, "Writing the notes")
    llm = manager.llm_factory(agent)
    cost = 0.0

    async def ask(system: str, text: str) -> Notes | None:
        nonlocal cost
        resp = await llm.complete(
            system=system + safety.READ_ONLY_RULE,
            messages=[Message.user(safety.fence("the transcript", text))],
            max_tokens=2500,
        )
        cost += resp.usage.cost_usd
        return parse_notes(resp.text)

    context = f"Meeting recording: {path.name}, {minutes} minutes."
    if job.params.get("about"):
        context += f" The user says it's about: {job.params['about']}."
    chunks = parts(lines)
    notes, problem = None, "The notes didn't come out right."
    try:
        if len(chunks) == 1:
            notes = await ask(SYSTEM, f"{context}\n\nTranscript:\n{chunks[0]}")
        else:
            found = [
                await ask(SYSTEM, f"{context} Part {i + 1} of {len(chunks)}.\n\nTranscript:\n{c}")
                for i, c in enumerate(chunks)
            ]
            merged = json.dumps([n.__dict__ for n in found if n], ensure_ascii=False)
            notes = await ask(SYSTEM + "\n\n" + MERGE, merged[: PART_CHARS * 2])
    except Exception as exc:  # the transcript is kept, and they're told why there are no notes
        from pyxi.agents.jobs import describe_failure

        log.exception("scribe: the notes for %s failed", path.name)
        problem = describe_failure(exc)
    job.cost_usd = cost
    if notes is None:
        from pyxi import made

        made.record(manager.settings.data_dir, job.outputs)  # so the app will open it
        job.status = "failed"
        job.summary = (
            f"{problem.rstrip('.')}. The transcript of {path.name} is saved, so the "
            f"listening isn't lost: {script_path}. Ask again for the notes in a moment."
        )
        job.data = {
            "title": "Transcript saved, no notes yet",
            "body": f"{problem.rstrip('.')}. The transcript is saved as {script_path.name}. "
            "Ask again for the notes in a moment.",
            "actions": ["open", "folder"],
            "outputs": job.outputs,
        }
        return
    note_path = unique(folder, f"{path.stem} notes.md")
    note_path.write_text(notes.markdown(path.name, minutes), encoding="utf-8")
    job.outputs = [str(note_path), str(script_path)]
    n_d, n_a = len(notes.decisions), len(notes.actions)
    body = (
        f"{minutes} min. {n_d} decision{'s' if n_d != 1 else ''}, "
        f"{n_a} action item{'s' if n_a != 1 else ''}."
    )
    if notes.body:
        body += " The follow-up email is a draft: nothing is sent unless you allow it."
    sections = [{"heading": "Summary", "lines": notes.summary[:4]}]
    if notes.decisions:
        sections.append({"heading": "Decisions", "lines": notes.decisions[:8]})
    if notes.actions:
        sections.append(
            {"heading": "Action items", "lines": [notes.action_line(a) for a in notes.actions[:8]]}
        )
    job.summary = (
        f"Notes for {notes.title} ({minutes} min), saved to {note_path}. "
        + " ".join(notes.summary)
        + (
            " Decisions: " + "; ".join(d.rstrip(".") for d in notes.decisions) + "."
            if notes.decisions
            else ""
        )
        + (
            " Actions: " + "; ".join(notes.action_line(a) for a in notes.actions) + "."
            if notes.actions
            else ""
        )
    )
    job.data = {
        "title": f"Notes: {notes.title}"[:70],
        "body": body,
        "changes": [notes.action_line(a) for a in notes.actions[:8]] or notes.decisions[:8],
        "card": {"title": notes.title, "sections": sections},
        "actions": ["open", "folder"],
        "outputs": job.outputs,
        "follow_up": {"subject": notes.subject, "body": notes.body} if notes.body else None,
    }
    job.status = "done"
