"""Pyxi's brain: one LangGraph run per conversation turn.

    START -> perceive -> think -> act --+--> finish -> END
                           ^            |
                           +------------+   (an info tool like recall_screen / look_closer
                                             asked for another round, budget permitting)

* **perceive** grabs the screen (or uses the capture taken the moment push-to-talk began, so
  Pyxi sees what you saw when you started speaking), records it in spatial memory and
  renders a set-of-marks image.
* **think** is one model call with the persona, conversation, team status, memories, the
  screen (text + image) and any lookup results.
* **act** runs tools. Drawings and 3D scenes go to the overlay immediately; speech streams
  sentence by sentence with inline markers synced to the audio; jobs start in the background.
* **finish** makes sure something was said, logs the turn and its cost.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import io
import json
import logging
import random
import re
import secrets
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from otto.llm.base import ImagePart, LLMProvider, Message, TextPart
from otto.storage import MemoryStore, TrajectoryStore
from otto.types import ToolCall, Usage
from PIL import Image
from pydantic import ValidationError

from pyxi import booking, hands, media, safety, tidy, weather, web, windows
from pyxi.agents import AgentProfile, AgentRegistry, Job, JobManager
from pyxi.agents.jobs import Handoff
from pyxi import alarms
from pyxi.companion import filework, instant, paperwork, pricecheck, prompts, specialists
from pyxi.companion.actions import app_target, in_account, open_app, safe_url
from pyxi.companion.aim import Survey, confirm_target, survey
from pyxi.companion.annotations import (
    MARKER,
    Annotation,
    from_marker,
    resolve_point,
    resolve_rect,
    split_markers,
)
from pyxi.companion.errands import run_errand
from pyxi.companion.filework import FILE_TOOLS
from pyxi.companion.layout import Placed
from pyxi.companion.notes import (
    BOARD_SIZES,
    PLATE_SIZE,
    Guide,
    build_card,
    card_size,
    picture_rect,
    place_stage,
    target_rect,
)
from pyxi.companion.operator import Operator, Outcome
from pyxi.companion.paperwork import PAPER_TOOLS
from pyxi.companion.tools import BUILD_BRIEF, COMPANION_TOOLS, INFO_TOOLS
from pyxi.companion.turns import TurnStore
from pyxi.config import PyxiSettings
from pyxi.connectors import Connectors, Outgoing
from pyxi.hub import Hub
from pyxi.llm import make_llm
from pyxi.logscrub import hide_secrets
from pyxi.mailwatch import MailWatchStore
from pyxi.memory import SpatialMemory
from pyxi.palace import ActivityLog, causal_scene, parse_when, snapshot_scene, strip_when
from pyxi.partial_json import complete_items, first_chunk, partial_string, picture_head
from pyxi.routines import RoutineStore
from pyxi.scenes import SceneSpec, build_exploded_scene, get_scene, parse_scene
from pyxi.scenes import library as model_library
from pyxi.scenes.builder import build_model, explain_library_model
from pyxi.scenes.object_gallery import OBJECT_GALLERY
from pyxi.scenes.objects import ObjectEdit, ObjectScene, apply_edit, object_summary
from pyxi.scenes.sketch import Sketchboard, steps_from_speech
from pyxi.scenes.spec import SceneAnchor
from pyxi.screen import ScreenContext, ScreenReader, uia
from pyxi.stream_llm import StreamingChat
from pyxi.timecontext import now_block
from pyxi.voice import Transcriber, make_tts, split_sentences
from pyxi.voice.studio import LANGUAGES, VoiceStudio
from pyxi.voice.tts import FallbackTTS
from pyxi.walkthrough import build_walkthrough

log = logging.getLogger(__name__)
# A note marker trailing a sentence ('...test. [note:1] Then') belongs to that sentence.
LEADING_NOTES = re.compile(r"^\s*(?:\[note:\d+\]\s*)+", re.IGNORECASE)
State = Literal["idle", "listening", "transcribing", "looking", "thinking", "speaking"]


# A site named in a word ("put Gmail on the right") and where it lives, when it isn't <word>.com.
_SITE_WORDS = {
    "gmail": "https://mail.google.com",
    "email": "https://mail.google.com",
    "google sheets": "https://sheets.google.com",
    "sheets": "https://sheets.google.com",
    "google docs": "https://docs.google.com",
    "docs": "https://docs.google.com",
    "calendar": "https://calendar.google.com",
    "youtube music": "https://music.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "chatgpt": "https://chatgpt.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
}


class TurnState(TypedDict, total=False):
    user_text: str
    turn_id: str
    round: int
    notes: list[str]
    needs_more: bool
    stop: str | None


class Brain:
    def __init__(
        self,
        settings: PyxiSettings,
        hub: Hub,
        *,
        llm: LLMProvider | None = None,
        otto_llm: LLMProvider | None = None,
        tts: FallbackTTS | None = None,
        screen: ScreenReader | None = None,
    ) -> None:

        self.settings = settings
        self.hub = hub
        self.llm = llm or make_chat_llm(settings)
        self.tts = tts or make_tts(
            settings.voice, settings.models_dir, settings.data_dir / "voice.json"
        )
        self.stt = Transcriber(settings.voice)
        self.screen = screen or ScreenReader(settings.screen)
        if hasattr(self.screen, "set_private_apps"):  # plus the apps they added in Settings
            from pyxi.screen import private_apps

            self.screen.set_private_apps(private_apps.load(settings.data_dir))
        db = settings.db_path
        self.registry = AgentRegistry(db)
        thumbs = settings.palace_dir if settings.screen.keep_screenshots else None
        self.spatial = SpatialMemory(
            db, retention_hours=settings.screen.retention_hours, thumbs_dir=thumbs
        )
        self.activity = ActivityLog(db)
        from pyxi import memory_guard

        memory_guard.install()  # no passwords, card numbers, IDs or keys, for anyone (Scout too)
        self.memory = MemoryStore(db)
        self.turns = TurnStore(db, settings.turns_dir)
        otto_dir = settings.data_dir / "otto"
        self.otto_store = TrajectoryStore(otto_dir / "otto.db", otto_dir / "runs")
        self.jobs = JobManager(
            settings,
            self.registry,
            hub,
            llm_factory=lambda agent: self.llm,
            scene_maker=self._scene_for_job,
            otto_llm=otto_llm,
            on_done=self._job_done,
        )
        # What Pyxi did on their behalf, with undo (pyxi/audit.py): the panel's Activity tab.
        from pyxi.audit import AuditLog

        self.audit = AuditLog(
            settings.data_dir / "audit.jsonl", settings.screen.turn_retention_days
        )
        self.jobs.audit = self.audit
        self.history: list[tuple[str, str]] = []
        self.state: State = "idle"
        self._lock = asyncio.Lock()
        self._prefetch: asyncio.Task[ScreenContext] | None = None
        self._speech: asyncio.Task[None] | None = None
        self._speech_gen = 0
        self._bg: set[asyncio.Task[Any]] = set()
        self._graph = self._build_graph()
        # per-turn scratch
        self._ctx: ScreenContext | None = None
        self._images: list[bytes] = []
        self._usage = Usage()
        self._said: list[str] = []
        self._recent_acks: list[str] = []  # the last few acknowledgements (vary_ack)
        self._calls: list[dict[str, Any]] = []
        self._scene_retry = False
        self._turn_card = None  # the note card drawn this turn, if any
        self._survey: asyncio.Task | None = None
        self._watcher: asyncio.Future[None] | None = None
        self._job_llm: LLMProvider | None = None
        self._user_text = ""  # the user's own words this turn (errands check addresses against it)
        self._visual_turn = False  # a 3D model or scene was shown this turn
        self._talking_until = 0.0  # monotonic time the queued speech finishes playing
        self._round_say = ""  # this round's spoken reply
        self._say_in_board = False
        self._visual_retry = False
        self._noted: set[int] = set()
        self._narrated_card = ""
        self._narrated_items: set[int] = set()
        self._turn_gen = 0
        self._turn_id = ""
        self._synthetic = False
        self._pending_say = ""
        self._promise_nudged = False
        self._refused = False
        self._cut_short = False
        # What a turn they talked over left behind (its words, and what it had done), for the
        # turn their new words start: nothing they said is dropped.
        self._dropped: dict[str, Any] | None = None
        self._words_gen = -1  # the speech gen at which they last pressed talk
        self._stopped_turn = ""  # the turn the stop button ended: its words are not carried on
        self._placed = Placed()  # what's on screen now (cards, stages), so new things keep clear
        self.overlay_in_capture = (
            False  # the desktop app says whether screen shares see the overlay
        )
        self._object: ObjectScene | None = None  # the 3D model on screen, so follow-ups can edit it
        self._guide: Guide | None = None  # a click Pyxi is waiting for
        self._operator: Operator | None = None  # Pyxi using the mouse and keyboard
        self.connectors = Connectors(settings.data_dir, Path.home() / "Documents" / "Pyxi")
        self.routines = RoutineStore(settings.data_dir / "routines.json")
        self.mailwatch = MailWatchStore(settings.data_dir / "mailwatch.json")

    # ================================================================== entry points
    async def set_state(self, state: State) -> None:
        self.state = state
        await self.hub.emit("state", state=state)

    def begin_listening(self) -> None:
        """Push-to-talk pressed: capture the screen *now*, while the user starts speaking."""
        self.interrupt()
        self._words_gen = self._speech_gen  # more words are coming: a running turn is talked over
        self._prefetch = asyncio.create_task(self._capture())
        self._spawn(self.set_state("listening"))
        self.warm_voice(listening=True)

    def look_ahead(self) -> None:
        """They started typing to Pyxi: capture the screen now, so the answer starts sooner."""
        if self._prefetch is None and not self._lock.locked():
            self._prefetch = asyncio.create_task(self._capture())
        self.warm_voice()

    def warm_voice(self, *, listening: bool = False) -> None:
        """Load the models a question needs while it is still being asked: the ears while they
        speak, the voice before the first sentence of the answer. Idle, Pyxi lets them go."""
        try:
            if listening:
                studio = self.studio
                self.stt.warm(studio.prefs.language if studio else "en")
            warm = getattr(self.tts, "warm", None)
            if warm:
                warm()
        except Exception as exc:  # never in the way of the question itself
            log.debug("warm-up failed: %s", exc)

    def release_idle(self, idle_s: float) -> list[str]:
        """Let go of speech, voice and OCR models nobody has used for ``idle_s`` seconds.
        Never in the middle of a turn. Returns what was let go."""
        if idle_s <= 0 or self.state != "idle" or self._lock.locked():
            return []
        gone = [f"ears {n}" for n in self.stt.release_idle(idle_s)]
        release = getattr(self.tts, "release_idle", None)
        if release:
            gone += [f"voice {n}" for n in release(idle_s)]
        ocr = sys.modules.get("pyxi.agents.pixel_ocr")
        if ocr is not None and ocr.release_idle(idle_s):
            gone.append("ocr")
        if gone:
            import gc

            gc.collect()
            log.info("idle: let go of %s", ", ".join(gone))
        return gone

    async def hear(self, audio: bytes) -> None:
        await self.set_state("transcribing")
        try:
            studio = self.studio
            text, took, spoken = await self.stt.transcribe(
                audio, studio.prefs.language if studio else "en"
            )
            if studio:
                studio.detected_language = spoken
        except Exception:
            log.exception("transcription failed")
            await self.hub.emit("error", message="I couldn't make out the audio. Try again?")
            self._prefetch = None
            await self.set_state("idle")
            return
        from pyxi import wakename

        text = wakename.normalize(text)  # "Your pixxit split my screen" -> "Pyxi, split my screen"
        await self.hub.emit("transcript", text=text, seconds=round(took, 2))
        if not text.strip():
            self._prefetch = None  # the next question looks at the screen afresh
            await self.say_line("Sorry, I didn't catch that.")
            await self.set_state("idle")
            return
        await self.turn(text)

    async def _set_alarm(self, alarm: alarms.Alarm) -> None:
        """'Set an alarm for 7am': one of Pyxi's routines, set at once, rung by Pyxi itself."""
        now = datetime.now()
        r = self.routines.add(
            alarm.label or f"Alarm {alarms.spoken_time(alarm.at)}",
            time=alarm.at.strftime("%H:%M"),
            on=alarm.at.date().isoformat(),
            kind="alarm",
            message=alarm.label,
            now=now,
        )
        self._calls.append(
            {"round": 1, "tool": "alarm", "arguments": {"at": alarm.at.isoformat(), "id": r.id}, "instant": True}
        )
        await self.hub.emit("routines", routines=self.routines.public())
        self._speak(f"{alarm.line(now)} Keep the PC awake and I'll ring.")

    def _ditto_watching(self) -> bool:
        return any(j.role == "watching" and j.status == "running" for j in self.jobs.jobs.values())

    def _list_alarms(self) -> None:
        """'What alarms do I have?': read straight from the routines and running timers."""
        now = datetime.now()
        rows = []
        for r in sorted((r for r in self.routines.items if r.kind == "alarm" and r.enabled), key=lambda r: r.next_run):
            at = datetime.fromtimestamp(r.next_run)
            day = "today" if at.date() == now.date() else "tomorrow" if at.date() == (now + timedelta(days=1)).date() else f"{at:%A}"
            names = {"mon": "Mondays", "tue": "Tuesdays", "wed": "Wednesdays", "thu": "Thursdays", "fri": "Fridays", "sat": "Saturdays", "sun": "Sundays"}
            days = ", ".join(names.get(d[:3].lower(), d) for d in r.days)
            repeat = "" if r.on else f", {days}" if days != "every day" else ", every day"
            rows.append(f"{alarms.spoken_time(at)} {day}" + (f" ({r.message})" if r.message else "") + repeat)
        timers = [j.task for j in self.jobs.jobs.values() if j.role == "reminder" and j.status == "running"]
        self._calls.append({"round": 1, "tool": "list_alarms", "arguments": {}, "instant": True})
        if not rows and not timers:
            self._speak("No alarms or timers set.")
            return
        parts = []
        if rows:
            parts.append(("Your alarm: " if len(rows) == 1 else f"{len(rows)} alarms: ") + "; ".join(rows) + ".")
        if timers:
            parts.append(("One timer running: " if len(timers) == 1 else f"{len(timers)} timers running: ") + "; ".join(timers) + ".")
        self._speak(" ".join(parts))

    async def _cancel_alarm(self, which: str) -> None:
        """'Cancel my 6:30 alarm': that alarm goes at once (with no am/pm, either 6:30)."""
        if ":" in which and len(which.split(":")[0]) == 2:
            times = {which}
        else:
            h, m = (int(x) for x in which.split(":"))
            times = {f"{h % 24:02d}:{m:02d}", f"{(h + 12) % 24:02d}:{m:02d}"}
        gone = [r for r in self.routines.items if r.kind == "alarm" and r.time in times]
        for r in gone:
            self.routines.remove(r.id)
        self._calls.append({"round": 1, "tool": "cancel_alarm", "arguments": {"time": which}, "instant": True})
        await self.hub.emit("routines", routines=self.routines.public())
        when = alarms.spoken_time(datetime.strptime(sorted(times)[0], "%H:%M"))
        if not gone:
            left = [r for r in self.routines.items if r.kind == "alarm"]
            others = ", ".join(sorted({alarms.spoken_time(datetime.strptime(r.time or "00:00", "%H:%M")) for r in left}))
            self._speak(f"There's no {when} alarm." + (f" You've got {others}." if others else " You've no alarms set."))
            return
        self._speak(f"Done, your {when} alarm is cancelled." if len(gone) == 1 else f"Done, I cancelled {len(gone)} alarms at {when}.")

    async def _set_timer(self, timer: alarms.Timer) -> None:
        """'Set a timer for 10 minutes': shows in the task tray, rings when it's up, and Stop
        everything cancels it."""
        span = alarms.spoken_span(timer.seconds)
        job = await self.jobs.track("Pyxi", f"Timer, {span}" + (f": {timer.label}" if timer.label else ""), role="reminder")

        async def wait_and_ring() -> None:
            await asyncio.sleep(timer.seconds)
            await self.jobs.settle(job, ok=True, summary=f"Timer done: {span}.")
            await self.ring(timer.label or f"{span} timer", alarm=False, reminder=timer.reminder)

        self.jobs._tasks[job.id] = asyncio.create_task(wait_and_ring())
        self._calls.append(
            {"round": 1, "tool": "timer", "arguments": {"seconds": timer.seconds}, "instant": True}
        )
        self._speak(timer.line())

    async def ring(self, label: str, *, alarm: bool, reminder: bool = False) -> None:
        """Time's up: a chime that repeats until it's dismissed (a click, talking, or the dismiss
        hotkey), a card, and a spoken line."""
        now = datetime.now()
        what = "Alarm" if alarm else "Timer"
        line = (
            f"It's {alarms.spoken_time(now)}." + (f" {label[:1].upper()}{label[1:]}." if label else " Time to get up.")
            if alarm
            else f"Reminder: {label[:1].upper()}{label[1:]}." if reminder else f"Time's up: {label}."
        )
        await self.hub.emit("alarm", title=what, label=label, at=now.isoformat())
        await self._notify("", ok=True, title=f"{what}, {alarms.spoken_time(now)}", body=line)
        self._speak(line, record=False)

    async def dictate(self, audio: bytes) -> str:
        """Hold Ctrl + Windows and talk: the words are typed where the cursor is, in whatever app
        has focus. Speech-to-text only (on this PC), no model, no screen: it works while Pyxi is
        paused too. Returns what was typed ("" for nothing)."""
        from pyxi import dictation

        await self.set_state("transcribing")
        try:
            studio = self.studio
            heard, took, _ = await self.stt.transcribe(
                audio, studio.prefs.language if studio else "en"
            )
        except Exception:
            log.exception("dictation transcription failed")
            await self.hub.emit("error", message="I couldn't make out that dictation. Try again?")
            await self.set_state("idle")
            return ""
        text = dictation.clean(heard)
        if text:
            # The desktop pastes it into the focused field and puts the clipboard back.
            await self.hub.emit("action", kind="insert", value=text)
        await self.hub.emit("dictated", text=text, seconds=round(took, 2))
        await self.set_state("idle")
        return text

    async def _within_limits(self, kind: str, next_action: str) -> bool:
        """At an hourly limit (pyxi/limits.py), stop and ask before doing more."""
        from pyxi.limits import Limits

        reached = Limits(self.settings.data_dir, self.settings.limits).reached(kind)
        if not reached:
            return True
        return bool(
            await self.hub.request(
                "confirm",
                timeout=900,
                job_id="",
                agent="Pyxi",
                action="Keep going past the hourly limit?",
                reason=f"{reached}\n\nNext: {next_action}",
            )
        )

    async def _count_sent(self, out: Outgoing) -> None:
        """Something went out: counted for the hourly limits, and kept in the activity log."""
        from pyxi.limits import Limits

        Limits(self.settings.data_dir, self.settings.limits).record(out.channel)
        self.audit.add("sent", out.action, agent=out.agent, detail=out.payload or out.preview)

    async def _approve(self, out: Outgoing) -> None:
        """Show exactly what would go out; act only on Allow."""
        if not await self._within_limits(out.channel, out.action):
            await self.say_line("Okay, I'll stop there for now.")
            return
        ok = await self.hub.request(
            "confirm",
            timeout=900,
            job_id="",
            agent=out.agent,
            action=out.action,
            reason=out.preview,
        )
        if not ok:
            await self.say_line("Okay, I won't send it.")
            return
        try:
            line = await out.run()
            await self._count_sent(out)
        except Exception as exc:
            log.exception("approved action failed")
            await self.say_line(f"That didn't go through. {friendly_error(exc)}")
            return
        await self.say_line(line if not line.startswith(("CREATED", "SENT")) else "Done.")

    async def _tidy(self, args: dict[str, Any]) -> str | None:
        """Look, then plan, then one Allow card, then move (with an undo log)."""
        log_dir = self.settings.data_dir / "tidy"
        if args.get("undo"):
            return f"tidy_folder: {await asyncio.to_thread(tidy.undo_last, log_dir)} Tell them."
        name = str(args.get("folder") or "desktop")
        folder = tidy.known_folder(name) or (Path(name) if Path(name).is_dir() else None)
        if folder is None:
            return f"tidy_folder: I don't know a folder called {name!r}; try desktop or downloads."
        groups = [g for g in args.get("groups") or [] if isinstance(g, dict)]
        if not groups:
            listing = await asyncio.to_thread(tidy.listing, folder)
            return (
                f"tidy_folder: {listing}\nNow call tidy_folder again with groups. Say nothing in this "
                "reply: they've heard 'On it' and the card will show the plan."
            )
        plan = await asyncio.to_thread(tidy.make_plan, folder, groups)
        if not plan.moves:
            return "tidy_folder: nothing in that plan matched real files; list the folder again."
        self._spawn(self._tidy_approved(plan, log_dir))
        return (
            f"Waiting for them to approve: {plan.summary()} Tell them it's ready on the card; don't "
            "say it's done."
        )

    async def _tidy_approved(self, plan: tidy.Plan, log_dir: Path) -> None:
        ok = await self.hub.request(
            "confirm",
            timeout=600,
            job_id="",
            agent="Pyxi",
            action=plan.summary(),
            reason=f"In {plan.folder.name}. Files are only moved, never deleted or overwritten. "
            "Say 'undo that' and I'll put everything back.",
        )
        if not ok:
            await self.say_line("No problem, I left it as it is.")
            return
        moved, failed, tidy_log = await asyncio.to_thread(tidy.apply, plan, log_dir)
        folders = len({into for _, into in plan.moves})
        self.audit.add(
            "moved",
            f"Moved {moved} files into {folders} folders in {plan.folder.name}",
            detail=str(plan.folder),
            undo={"type": "tidy", "log": str(tidy_log)},
        )
        tail = f" {len(failed)} were open somewhere, so I left those." if failed else ""
        self.history = [
            *self.history[-11:],
            (f"(tidied {plan.folder})", f"(moved {moved} files into {folders} folders)"),
        ]
        await self.say_line(
            f"Done. {moved} files are in {folders} folders now.{tail} Say undo if you want it back."
        )

    def _hands_always(self) -> bool:
        """Their "Always allow" for the mouse and keyboard, while it lasts (pyxi/grants.py)."""
        from pyxi import grants

        return grants.active(self.settings.data_dir, "hands")

    async def _use_computer(self, args: dict[str, Any]) -> str | None:
        """Do a task with the mouse and keyboard, in the background, after they allow it."""
        task = str(args.get("task") or "").strip()[:600]
        if not task:
            return "use_computer: say what to do."
        if not hands.available():
            return "use_computer: this only works on Windows."
        if self._operator is not None:
            return (
                "use_computer: you're already using the mouse for another task; finish that first."
            )
        window = self._ctx.window.hwnd if self._ctx and self._ctx.window.hwnd else None
        read = sorted({label for label, _ in getattr(self, "_turn_reads", [])})
        self._spawn(self._drive(task, window, bool(args.get("come_back")), after_reading=read))
        return None

    async def _drive(
        self,
        task: str,
        window: int | None,
        come_back: bool = False,
        after_reading: list[str] | None = None,
    ) -> None:
        # After reading mail, a page or a document, "Always allow" doesn't apply: the task may
        # have come from that content, so it asks on a fresh card.
        if after_reading or not self._hands_always():
            fresh = (
                f"This asks again because Pyxi just read {', '.join(after_reading)}.\n"
                if after_reading
                else ""
            )
            ok = await self.hub.request(
                "confirm",
                timeout=300,
                job_id="",
                agent="Pyxi",
                action=f"Use your mouse and keyboard to: {task}",
                reason=fresh + "Move the mouse or press Esc to stop me. Anything that sends, buys, "
                "posts or deletes asks you again.",
                **({} if after_reading else {"always": "Always allow"}),
            )
            if ok == "always":
                from pyxi import grants

                grants.grant(self.settings.data_dir, "hands")  # for 30 days; Settings revokes it
                await self.hub.emit("grants", grants=grants.public(self.settings.data_dir))
            elif not ok:
                await self.say_line("Okay, hands off.")
                return
        job = await self.jobs.track("Pyxi", task)

        async def report(doing: str, step: int) -> None:
            log.info("hands step %d: %s", step, doing)
            self.audit.add("clicked", doing, agent="Pyxi (mouse and keyboard)", detail=task)
            await self.hub.emit("driving", doing=doing)  # rides beside the cursor
            await self.hub.emit(
                "job_event", job_id=job.id, agent="Pyxi", event="status", data={"message": doing}
            )

        async def confirm(action: str, reason: str) -> bool:
            return bool(
                await self.hub.request(
                    "confirm",
                    timeout=300,
                    job_id=job.id,
                    agent="Pyxi",
                    action=action,
                    reason=reason,
                )
            )

        if getattr(self, "_hands_llm", None) is None:
            model = self.settings.model_hands
            self._hands_llm = (
                StreamingChat(model) if model and not self.settings.local_mode else self.llm
            )
        self._operator = Operator(
            self._hands_llm,
            lambda: self.screen.capture(pixels=True, follow_mouse=False),
            confirm,
            report,
            image_width=self.settings.screen.model_image_width,
            strong_llm=(
                StreamingChat(self.settings.model_hands_strong)
                if self.settings.model_hands_strong and not self.settings.local_mode
                else None
            ),
        )
        try:
            out = await self._operator.run(task, window, come_back)
        except Exception as exc:
            log.exception("use_computer failed")
            out = Outcome(False, f"I hit a snag doing that. {friendly_error(exc)}")
        finally:
            usage, self._operator = (self._operator.usage if self._operator else None), None
            await self.hub.emit("driving", doing=None)
        cost = usage.cost_usd if usage else 0.0
        self.turns.add_spend("hands", cost)
        await self.jobs.settle(job, ok=out.ok, summary=out.summary, cost=cost)
        self.history = [
            *self.history[-11:],
            (f"(you used the mouse and keyboard to: {task[:200]})", f"({out.summary})"),
        ]
        log.info(
            "use_computer: %s in %d steps ($%.4f)",
            "ok" if out.ok else "stopped",
            len(out.steps),
            cost,
        )
        # One short line out loud; the whole story is in the tray and the notification.
        await self.say_line(split_sentences(out.summary)[0][:140] if out.summary else "Done.")

    def _screens_note(self) -> str:
        """With two or three monitors, what's on each, so 'my left screen' means something and
        Pyxi never says it can only see one screen. Worked out once a turn (it's cheap)."""
        if getattr(self, "_screens_text", None) is None:
            try:
                self._screens_text = (
                    windows.screens_summary(uia.cursor_pos(), getattr(self, "_shown_screen", None))
                    if windows.available()
                    else ""
                )
            except Exception:
                log.warning("couldn't list the screens", exc_info=True)
                self._screens_text = ""
        note = self._screens_text or ""
        return f"\n\n# All their screens\n{note}\n{_MULTI_SCREEN_NOTE}" if note else ""

    async def _arrange_windows(self, args: dict[str, Any]) -> str | None:
        """Lay windows out (two, three or four panes), or snap, maximize, minimize or restore the
        window they're in."""
        if not windows.available():
            return "arrange_windows: this only works on Windows."
        ctx = self._ctx
        hwnd = ctx.window.hwnd if ctx and ctx.window.hwnd else ctypes_foreground()
        if not hwnd:
            return "arrange_windows: I couldn't tell which window you mean."
        layout = str(args.get("layout") or "split")
        panes = [str(p).strip() for p in args.get("panes") or [] if str(p).strip()]
        on = windows.screen_named(str(args.get("screen") or "")) if args.get("screen") else None
        if args.get("screen") and on is None:
            names = ", ".join(s["name"] for s in windows.screens()) or "one"
            return f"arrange_windows: there's no {args['screen']!r} screen (they have: {names})."
        if on is not None and (layout != "panes" or len(panes) <= 1):
            # "Put YouTube on my right screen": that window (or the one they're in), moved there
            # and made full-screen (or snapped, if they said left or right half).
            win = hwnd
            if panes and panes[0].lower() not in ("current", "this", "this window", "here"):
                win = windows.window_like(panes[0], windows.open_windows(minimized=True), set()) or 0
                if not win:
                    return f"arrange_windows: {panes[0]!r} isn't open. Open it first, or say so."
            windows.place(win, *on["work"])
            windows.snap(win, layout if layout in ("left", "right", "top", "bottom") else "maximize")
            windows.focus(win)
            return None
        if layout == "split":
            target = str(args.get("open") or "").strip()
            if not target:
                return "arrange_windows: say what should go on the other half."
            panes = (
                [target, "current"] if args.get("current_side") == "right" else ["current", target]
            )
        elif layout != "panes":
            windows.snap(hwnd, layout)
            return None
        if len(panes) < 2:
            return "arrange_windows: give at least two panes."
        panes = panes[:6]
        rects = windows.pane_rects(
            on["work"] if on else windows.work_area(hwnd), len(panes), str(args.get("arrangement") or "auto")
        )
        open_now = windows.open_windows()
        used: set[int] = {hwnd}
        placed: list[tuple[int, tuple[int, int, int, int]]] = []
        missing: list[str] = []
        for item, rect in zip(panes, rects, strict=True):
            if item.lower() in ("current", "this", "this window", "here"):
                win: int | None = hwnd
            else:
                win = windows.window_like(item, open_now, used)
                if win is None:
                    word = item.lower()
                    site = _SITE_WORDS.get(word) or (
                        f"https://{word}.com" if re.fullmatch(r"[a-z0-9-]{2,30}", word) else item
                    )
                    url, app = (
                        (None, app_target(item)) if app_target(item) else (safe_url(site), None)
                    )
                    if url:
                        win = await windows.open_new_window(url, like=hwnd, is_url=True)
                    elif app:
                        win = await windows.open_new_window(app, is_url=False)
            if win is None:
                missing.append(item)
                continue
            used.add(win)
            windows.place(win, *rect)
            placed.append((win, rect))
        await asyncio.sleep(0.8)
        for win, rect in placed:  # browsers sometimes resize a new window a moment after opening
            windows.place(win, *rect)
        # Every pane in front. The window they were in only gets the focus back if it's one of
        # the panes: focusing it otherwise put a full-screen New Tab on top of both panes.
        for win, _ in placed:
            windows.focus(win)
        if any(win == hwnd for win, _ in placed):
            windows.focus(hwnd)
        if missing:
            return (
                f"arrange_windows: placed {len(placed)} of {len(panes)}; couldn't open "
                f"{', '.join(map(repr, missing))}. Tell them which, briefly."
            )
        return None

    async def _book(self, args: dict[str, Any]) -> str | None:
        """Hand a booking to Scout, after they agree to which of their details it may use."""
        what = str(args.get("what") or "").strip()
        if not what:
            return "book: what should I book?"
        profile = booking.load(self.settings.data_dir)
        if not booking.ready(profile):
            return (
                "book: I don't have their booking details yet. Ask them to add their name and a "
                "phone or email in Settings, Booking details (it stays on this PC), then try again."
            )
        where = str(args.get("where") or "").strip()
        url = str(args.get("url") or "").strip()
        place = f" at {where}" if where else ""
        if not await self._within_limits("booking", f"Book {what}{place}"):
            return "book: they stopped at the hourly limit for bookings, so nothing was booked. Say so, briefly."
        allowed = await self.hub.request(
            "confirm",
            agent="Pyxi",
            action=f"Book {what}{place}",
            reason=(
                f"Scout opens a browser you can watch and uses {booking.shared_labels(profile)} on "
                "that site. It asks you before the final booking click, and never pays: at the "
                "payment page it hands the window to you."
                + (f"\nWebsite: {url}" if url.startswith(("http://", "https://")) else "")
            ),
        )
        if not allowed:
            return "book: they said no, so nothing was booked. Say that's fine, briefly."
        from pyxi.limits import Limits

        Limits(self.settings.data_dir, self.settings.limits).record("booking")
        self.audit.add(
            "booked",
            f"Started booking {what}{place} (Scout asks before the final click)",
            detail=url,
        )
        await self.jobs.start(
            "scout",
            booking.brief(args, profile, await self._now()),
            start_url=url if url.startswith(("http://", "https://")) else None,
            kind="booking",
        )
        return None

    async def _play_music(self, args: dict[str, Any]) -> str | None:
        """Play a song behind the window they're in, and set the volume. The volume is instant;
        the song starts in the background (a few seconds), so the turn and the next request
        don't wait for it, and the notch shows when it's playing."""
        query = str(args.get("query") or "").strip()
        if not query:
            return "play_music: what should I play?"
        if not windows.available():
            return "play_music: this only works on Windows."
        ctx = self._ctx
        here = ctx.window.hwnd if ctx and ctx.window.hwnd else ctypes_foreground()
        if args.get("volume"):
            log.info(
                "play_music: %s", await asyncio.to_thread(media.set_volume, str(args["volume"]))
            )
        job = await self.jobs.track("Pyxi", f"Playing {query}")

        async def go() -> None:
            try:
                app = str(args.get("app") or "youtube_music")
                played = None
                if app == "spotify":
                    played = await media.play_spotify(query, back_to=here)
                if played is None:  # no Spotify here: the song still plays, in Pyxi's player
                    played = await media.play(query, music=app != "youtube", back_to=here)
            except Exception as exc:  # never leave the notch saying it's still starting
                log.warning("play_music failed", exc_info=True)
                await self.jobs.settle(job, ok=False, summary=f"Couldn't play that: {exc}")
                return
            log.info("play_music: %r -> %s (%s)", query, played.title, played.ok or played.note)
            if played.ok:
                await self.jobs.settle(job, ok=True, summary=f"Playing {played.title}")
            else:
                why = (
                    played.note
                    if not played.title
                    else f"{played.title} is open, but {played.note[0].lower()}{played.note[1:]} Press play on it."
                )
                await self.jobs.settle(job, ok=False, summary=why)

        self._spawn(go())
        return None

    async def _quick_action(self, action: str, value: str) -> str | None:
        if not value.strip():
            return f"{action}: nothing to do."
        if action == "clear":
            # "Close the 3D model", "close that": everything Pyxi has on screen goes, at once.
            await self.hub.emit("clear")
            self._object = None
            self._placed.put("card", None, 0)
            return None
        if action == "screen_playpause":
            # "Play the song on my middle screen": that screen's top window to the front, then its
            # own play key (K on YouTube, Space elsewhere). A second, not half a minute of mouse.
            if not windows.available():
                return "screen_playpause: this only works on Windows."
            where = windows.screen_named(value)
            if where is None:
                return f"screen_playpause: there's no {value!r} screen."
            top = next(
                (
                    (h, t)
                    for h, t, _ in windows.open_windows()
                    if not windows._NOT_CONTENT.match(t)
                    and (r := windows.rect_of(h))
                    and r[2] > 80
                    and windows.screen_of(r, [where]) is not None
                ),
                None,
            )
            if top is None:
                return f"screen_playpause: nothing is open on the {where['name']} screen."
            from pyxi import hands as keys  # (not "hands": that name is used elsewhere here)

            windows.focus(top[0])
            await asyncio.sleep(0.25)
            key = "k" if "youtube" in top[1].lower() else "space"
            try:
                await asyncio.to_thread(lambda: keys.Hands().press(key))
            except keys.Interrupted:
                return "screen_playpause: they moved the mouse just then, so I didn't press play."
            return None
        if action == "volume":
            if not windows.available():
                return "volume: this only works on Windows."
            note = await asyncio.to_thread(media.set_volume, value)
            return None if note.startswith("Volume") else note
        if action == "media":
            key = value.strip().lower().replace(" ", "").replace("_", "")
            if key not in (
                "playpause",
                "play",
                "pause",
                "next",
                "previous",
                "volumeup",
                "volumedown",
                "mute",
            ):
                return (
                    f"media: I can play/pause, skip, go back, or change the volume, not {value!r}."
                )
            if not hands.available():
                return "media: this only works on Windows."
            presses = 5 if key.startswith("volume") else 1  # one volume step is barely audible
            for _ in range(presses):
                await asyncio.to_thread(hands.Hands().press, key)
            return None
        if action == "open_url":
            url = safe_url(value)
            if url is None:
                return "open_url: that isn't a web address."
            g = self.connectors.google
            url = in_account(url, g._saved_email() if g.connected() else None)
            opened = getattr(self, "_opened_urls", set())
            if url in opened:  # a looping model once made six new docs for one "new doc"
                return "open_url: that's already open from this request. Don't open it again; answer or stop."
            opened.add(url)
            self._opened_urls = opened
            await self.hub.emit("action", kind="open_url", value=url)
            return None
        if action == "open_app":
            note = await open_app(value)
            return None if note.startswith("Opened") else note
        if action == "copy":
            await self.hub.emit("action", kind="copy", value=value)
            return None
        if action == "insert":
            # Type into the app they meant: the one the request names ("in Notepad"), else the
            # one Pyxi was looking at. The paste goes to whatever has the focus, so that window
            # is brought to the front first (live, a packing list "in Notepad" went into the
            # calculator, which had the focus).
            if not await self._front_for_paste():
                await self.hub.emit("action", kind="copy", value=value)
                return "insert: Windows wouldn't bring that window to the front, so it was copied instead. Tell them to paste it (ctrl+v)."
            await self.hub.emit("action", kind="insert", value=value)
            return None
        return f"Unknown quick action {action}."

    def _paste_target(self) -> int | None:
        """The window a paste is meant for: the one the request names ("in Notepad"), else the
        one Pyxi was looking at."""
        return self._named_window(self._user_text or "", None) or (
            self._ctx.window.hwnd if self._ctx and self._ctx.window.hwnd else None
        )

    async def _front_for_paste(self) -> bool:
        """Bring that window to the front, since a paste goes to whatever has the focus.
        False when Windows won't hand the focus over."""
        target = self._paste_target()
        if target and windows.available():
            windows.focus(target)
            await asyncio.sleep(0.2)
            return windows.foreground() == target
        return True

    async def _paste_table(self, args: dict[str, Any]) -> str | None:
        """A whole table into whatever is open in front of them, at the cursor or selected
        cell, in one paste: any app, any browser tab, any account. (Seen in a competitor's demo:
        a sample table landed in Excel on the web in under a second, where Pyxi's only route
        was a Google connection that couldn't reach the file.)"""
        from pyxi.companion.paste import PasteError, looks_like_a_sheet, table_clip

        target = self._paste_target()
        title = ""
        if target and windows.available():
            title = next((t for h, t, _ in windows.open_windows() if h == target), "")
        if not title and self._ctx:
            title = self._ctx.window.title or ""
        try:
            clip = table_clip(args.get("rows"), bordered=not looks_like_a_sheet(title))
        except PasteError as exc:
            return f"paste_table: {exc}"
        if not await self._front_for_paste():
            await self.hub.emit("action", kind="copy", value=clip["text"])
            return (
                "paste_table: Windows wouldn't bring that window to the front, so the table was "
                "copied instead. Tell them to click where it should go and press ctrl+v."
            )
        await self.hub.emit("action", kind="insert_table", value=json.dumps(clip))
        return None

    async def _capture(self, follow_mouse: bool = True, hwnd: int | None = None) -> ScreenContext:
        """Take Pyxi's screenshot. If the overlay is visible to captures (so a Discord stream can
        see it), blank it for that instant so Pyxi never reads its own drawings. ``hwnd``: look
        at that window (and its screen) rather than the one under the mouse."""

        def grab() -> Any:
            if hwnd:
                return self.screen.capture(hwnd=hwnd)
            return (
                self.screen.capture() if follow_mouse else self.screen.capture(follow_mouse=False)
            )

        if not self.overlay_in_capture:
            return await grab()
        await self.hub.emit("capture_hide")
        await asyncio.sleep(0.09)  # one or two frames for the overlay to go clear
        try:
            return await grab()
        finally:
            await self.hub.emit("capture_show")

    def _screen_named_in(self, text: str) -> int | None:
        """The top window on the screen a request names ("my right screen", "the second
        monitor"), or None. Only with more than one screen, and only for looking questions."""
        if not windows.available() or not re.search(r"(?i)\b(screen|monitor|display)\b", text):
            return None
        m = re.search(
            r"(?i)\b(left|middle|centre|center|right|main|primary|first|second|third|other)\s+(?:screen|monitor|display)\b",
            text,
        )
        if not m or re.search(r"(?i)\b(put|move|split|arrange|open|play|pause)\b", text):
            return None
        where = windows.screen_named(m.group(1))
        if where is None or len(windows.screens()) < 2:
            return None
        self._named_screen = where["name"]
        return next(
            (
                h
                for h, t, _ in windows.open_windows()
                if not windows._NOT_CONTENT.match(t)
                and (r := windows.rect_of(h))
                and r[2] > 80
                and windows.screen_of(r, [where]) is not None
            ),
            None,
        )

    def _named_window(self, text: str, ctx: ScreenContext | None) -> int | None:
        """The window the request names ("...in Notepad"), when it isn't the one Pyxi is already
        looking at. With two monitors the mouse is often on the other screen (a chat, a video),
        and guessing at what's there instead points at the wrong app entirely."""
        if not text or not getattr(self.screen, "reads_windows", False) or not windows.available():
            return None
        try:
            hwnd = windows.named_window(text, windows.open_windows(minimized=True))
        except Exception:
            log.warning("couldn't list windows", exc_info=True)
            return None
        if hwnd and ctx is not None and ctx.window.hwnd == hwnd:
            return None
        return hwnd

    def _watch_window(self) -> None:
        """Drawings point at exact pixels; once the user switches window or tab they'd point at
        the wrong things, so clear them when the foreground window changes."""
        if self._ctx is None:
            return
        hwnd = self._ctx.window.hwnd
        by_handle = getattr(self.screen, "window_title", None)
        # Watch the window the drawings are on (it may not be the focused one), else focus.
        if hwnd and by_handle:

            def title_of() -> str | None:
                return by_handle(hwnd)
        else:
            title_of = getattr(self.screen, "foreground_title", None)
        if title_of is None:
            return
        if self._watcher and not self._watcher.done():
            self._watcher.cancel()
        drawn_on = self._ctx.window.title
        wx, wy, ww, wh = self._ctx.window.rect
        centre = (wx + ww // 2, wy + wh // 2)
        on_top = getattr(self.screen, "window_at_point", None)

        async def watch() -> None:
            covered = 0
            for _ in range(600):  # up to ~6 minutes
                await asyncio.sleep(0.6)
                # Alt-tab to another maximised app: same title, but the drawings now sit on top
                # of someone else's window. Two checks in a row, so a passing popup doesn't count.
                if hwnd and on_top and ww > 0:
                    # Mid-guide, what matters is the spot the arrow points at, not the middle of
                    # the window (a small dialog parked over the middle doesn't hide the step).
                    g = self._guide
                    spot = (g.rect[0] + g.rect[2] // 2, g.rect[1] + g.rect[3] // 2) if g else centre
                    top = on_top(*spot)
                    # The app's own menu or dialog opening over it isn't another window.
                    theirs = top and top != hwnd and not windows.same_app(top, hwnd)
                    covered = covered + 1 if theirs else 0
                    if covered >= 2:
                        log.info("clearing drawings: another window now covers %r", drawn_on)
                        await self.hub.emit("clear")
                        self._placed.put("card", None, 0)
                        return
                now = title_of()
                if now is None:
                    if hwnd and by_handle:  # the window closed: its drawings go too
                        await self.hub.emit("clear")
                        self._placed.put("card", None, 0)
                    return
                if same_page(now, drawn_on) or now in ("Pyxi", "Pyxi overlay", ""):
                    continue
                if self._guide:
                    # Mid-guide, a new page is the next step, not the end: look again.
                    self._spawn(self.guide_clicked("moved"))
                    return
                log.info("clearing drawings: the window changed (%r -> %r)", drawn_on, now)
                await self.hub.emit("clear")
                self._placed.put("card", None, 0)
                return

        self._watcher = asyncio.ensure_future(watch())

    async def _now(self) -> str:
        tz = None
        if self.connectors.google.connected():
            try:
                tz = await self.connectors.google.time_zone()
            except Exception:
                tz = None
        return now_block(tz)

    def _narrate_card(self) -> None:
        """Talk through the cue card: each point's own sentence, its mark drawing as it's said.
        Pyxi speaks its opener first (streamed, for speed), before the card even exists, so the
        points are narrated here, after it, instead of relying on markers in the opener."""
        card = self._turn_card
        if card is None or self._narrated_card == card.id:
            return
        self._narrated_card = card.id
        rest = [
            it for it in card.items if it.n not in self._noted and it.n not in self._narrated_items
        ]
        lines = []
        for it in rest:
            line = (it.say or it.text).strip()
            lines.append(f"[note:{it.n}] {line if line[-1:] in '.!?' else line + '.'}")
        if card.closing:
            lines.append(card.closing)  # land it, then offer what's next
        if lines:
            self._speak(" ".join(lines))

    def _start_survey(self, args: dict[str, Any]) -> None:
        """Explaining a picture: start finding its parts now, while the opener is spoken."""
        if self._survey is not None or not self.settings.model_aim or self.settings.local_mode:
            return
        if args.get("bill"):  # a bill's tags pin to its quoted lines: nothing to look for
            return
        ctx = self._ctx
        pic = picture_rect(ctx, args)
        if ctx is None or pic is None or pic[2] < 40 or pic[3] < 40:
            return
        pad = int(min(pic[2], pic[3]) * 0.04)
        crop = ctx.crop(pic, pad)
        if crop is None:
            return
        origin = (max(ctx.origin[0], pic[0] - pad), max(ctx.origin[1], pic[1] - pad))
        if getattr(self, "_aim_llm", None) is None:
            self._aim_llm = StreamingChat(self.settings.model_aim)
        names = [
            str(it.get("part"))
            for it in args.get("items", [])
            if isinstance(it, dict) and it.get("part")
        ]

        async def run() -> Survey:
            found, usage = await survey(self._aim_llm, crop, origin, self._user_text, names)
            if usage is not None:
                self._usage = self._usage + usage
            log.info("picture survey found %s", sorted(found.points))
            return found

        self._survey = asyncio.create_task(run())

    async def _survey_result(self) -> Survey | None:
        if self._survey is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(self._survey), 10)
        except Exception:
            return None

    async def _show_card_so_far(
        self, title: str, items: list[dict[str, Any]], head: dict[str, Any] | None = None
    ) -> None:
        """Show the points that have arrived so far, and say the new ones."""
        args = {**(head or {}), "title": title, "items": items}
        found = await self._survey_result() if args.get("style") == "on_picture" else None
        card = build_card(self._ctx, args, self._placed.others("card"), found, self._turn_card)
        if not card.items:
            return
        first = self._turn_card is None
        if not first:
            card.id, card.place = (
                self._turn_card.id,
                self._turn_card.place,
            )  # stays put as it fills in
        self._turn_card = card
        await self.hub.emit("notes", card=card.model_dump(mode="json"))
        if first:
            self._watch_window()
        for it in card.items:
            if it.n in self._noted or it.n in self._narrated_items:
                continue
            self._narrated_items.add(it.n)
            line = (it.say or it.text).strip()
            self._speak(f"[note:{it.n}] {line if line[-1:] in '.!?' else line + '.'}")

    def _builder_on(self) -> bool:
        return bool(self.settings.model_3d) and not self.settings.local_mode

    def _companion_tools(self) -> list:
        """The tools for this turn. 3D is only on the table when they asked for it: a normal
        "explain this" gets underlines, circles and cue cards on the real screen, which is what
        they want and much faster (3D takes seconds to build, and its schemas are big)."""
        tools = list(COMPANION_TOOLS)
        if not wants_3d(self._user_text, has_model=self._object is not None):
            tools = [t for t in tools if t.name not in THREE_D_TOOLS]
        if self._builder_on():
            tools = [BUILD_BRIEF if t.name == "build_3d_model" else t for t in tools]
        return tools

    def _builder_llm(self) -> LLMProvider:
        """Streamed, so the model can be on screen assembling itself while the rest arrives (a
        detailed model is a minute of JSON), and its long instructions are prompt-cached."""
        if getattr(self, "_model_llm", None) is None:
            self._model_llm = StreamingChat(
                self.settings.model_3d or self.settings.model,
                reasoning={"effort": self.settings.effort_3d},
            )
        return self._model_llm

    async def _build_from_brief(self, args: dict[str, Any]) -> None:
        """Build the model with the 3D builder, then show it; the notch says it's being made."""
        subject = str(args.get("subject") or "the model")[:80]
        if self.turns.spent_today() >= self.settings.budget.daily_cost_usd:
            await self.say_line(
                "I'd love to build that, but today's budget is used up.", record=False
            )
            return
        brief = f"{subject}. {args.get('show', '')}".strip()
        if args.get("beats"):
            brief += "\nBeats: " + "; ".join(str(b) for b in args["beats"][:6])
        ctx = self._ctx
        context = ""
        if ctx is not None:
            context = f"{ctx.window.title}\n" + "\n".join(t.text for t in ctx.text[:40])
        real = model_library.get(str(args.get("library") or ""))
        if real is not None and await self._explain_on_library_model(real, subject, brief, context):
            return
        job = await self.jobs.track("Pyxi", f"Building {subject} in 3D", role="model")
        await self.hub.emit(
            "job_event",
            job_id=job.id,
            agent="Pyxi",
            event="status",
            data={"message": f"is building {subject} in 3D"},
        )
        shown_partial = False

        async def assembling(partial: ObjectScene) -> None:
            # The model so far, silent and still: parts appear as they arrive.
            nonlocal shown_partial
            if self._object is not None and self._object.id != partial.id and shown_partial:
                return  # something else took the stage meanwhile
            await self.show_object(
                partial, restart=False, background=True, keep_place=shown_partial
            )
            shown_partial = True

        try:
            result = await build_model(self._builder_llm(), brief, context, on_parts=assembling)
        except Exception:
            log.exception("3D build failed")
            await self.jobs.settle(job, ok=False, summary="The model wouldn't come together.")
            await self.say_line(
                "Hmm, that model wouldn't come together. Want me to try again?", record=False
            )
            return
        self.turns.add_spend("model_3d", result.cost_usd)
        await self.jobs.settle(
            job, ok=result.model is not None, summary=subject, cost=result.cost_usd
        )
        if result.model is None:
            await self.say_line(
                "Hmm, that model wouldn't come together. Want me to try again?", record=False
            )
            return
        # Let Pyxi finish its opening line first: the model's narration must never talk over it
        # (they can play from different screens).
        if self._speech:
            await asyncio.gather(self._speech, return_exceptions=True)
        wait = self._talking_until - time.monotonic()
        if wait > 0:
            await asyncio.sleep(min(wait, 12.0))
        await self.show_object(result.model, restart=True, background=True)

    async def _explain_on_library_model(
        self, real: model_library.LibraryModel, subject: str, brief: str, context: str
    ) -> bool:
        """A real model from the library: on screen as soon as its file is here, then the
        explanation (labels, motions, beats) written on top of it. False if the file can't be
        had, so the caller builds one instead."""
        fetch = asyncio.to_thread(
            model_library.ensure, self.settings.library_dir, real, self.settings.library_url
        )
        at_rest = real.scene()
        explain = asyncio.ensure_future(
            explain_library_model(self._builder_llm(), at_rest, real.summary(), brief, context)
        )
        if await fetch is None:
            explain.cancel()
            return False
        job = await self.jobs.track("Pyxi", f"Explaining {subject} in 3D", role="model")
        # The real thing, straight away, still and silent while its explanation is written
        # (paused: with nothing to tell it would count as finished and tidy itself away).
        await self.show_object(
            at_rest.model_copy(update={"auto_play": False}), restart=False, background=True
        )
        try:
            result = await explain
        except Exception:
            log.exception("library explanation failed")
            await self.jobs.settle(job, ok=False, summary="The explanation wouldn't come together.")
            return True
        self.turns.add_spend("model_3d", result.cost_usd)
        await self.jobs.settle(
            job, ok=result.model is not None, summary=subject, cost=result.cost_usd
        )
        if result.model is None:
            return True  # the model stays on screen; Pyxi's own reply is the explanation
        model_library.snap_markers(result.model, self.settings.library_dir)
        if self._object is not None and self._object.id != at_rest.id:
            return True  # something else took the stage meanwhile
        if self._speech:
            await asyncio.gather(self._speech, return_exceptions=True)
        wait = self._talking_until - time.monotonic()
        if wait > 0:
            await asyncio.sleep(min(wait, 12.0))
        await self.show_object(result.model, restart=True, background=True, keep_place=True)
        return True

    def _errand_llm(self) -> LLMProvider:
        """Errands can use a stronger model than conversation (PYXI_JOB_MODEL), if set."""
        if self.settings.job_model and self.settings.job_model != self.settings.brain_model:
            if self._job_llm is None:
                from pyxi.llm import make_llm

                self._job_llm = make_llm(self.settings, model=self.settings.job_model)
            return self._job_llm
        return self.llm

    async def _run_errand(
        self, task: str, user_text: str, reads: safety.Reads | None = None
    ) -> None:
        """Do it quietly, then tell the user once, in the notch."""
        job = await self.jobs.track("Pyxi", task)

        async def progress(line: str) -> None:
            await self.hub.emit(
                "job_event", job_id=job.id, agent="Pyxi", event="status", data={"message": line}
            )

        async def approve(out: Outgoing) -> bool:
            ok = await self.hub.request(
                "confirm",
                timeout=900,
                job_id=job.id,
                agent="Pyxi",
                action=out.action,
                reason=out.preview,
            )
            return bool(ok)

        try:
            result = await run_errand(
                task=task,
                user_text=user_text,
                now=await self._now(),
                llm=self._errand_llm(),
                tools=self.connectors.tools(),
                approve=approve,
                progress=progress,
                reads=reads,
                gate=lambda out: self._within_limits(out.channel, out.action),
                on_sent=self._count_sent,
            )
        except Exception as exc:
            log.exception("errand failed")
            await self.jobs.settle(job, ok=False, summary=str(exc)[:200])
            await self._notify(job.id, ok=False, title="Couldn't finish that", body=str(exc)[:200])
            return
        self.turns.add_spend("errand", result.cost_usd)
        await self.jobs.settle(
            job,
            ok=result.ok,
            summary=result.summary,
            data={"links": result.links},
            cost=result.cost_usd,
        )
        self.history = [
            *self.history[-11:],
            (
                f"(errand finished: {task[:200]})",
                f"(notified the user: {result.title}. {result.summary} {result.link or ''})",
            ),
        ]
        await self._notify(
            job.id, ok=result.ok, title=result.title, body=result.summary, link=result.link
        )

    # ------------------------------------------------------------------ money in their mail
    async def _scan(self, what: str, query: str, n: int) -> tuple[Any, list[dict[str, Any]]]:
        job = await self.jobs.track("Penny", what)
        await self.hub.emit(
            "job_event", job_id=job.id, agent="Penny", event="status", data={"message": what}
        )
        return job, await self.connectors.google.gmail_fetch(query, n)

    async def _watch_subscriptions(self) -> None:
        """Find every recurring charge in their receipts; one card with the monthly total."""
        from pyxi.mailwatch import find_subscriptions, subscriptions_query, subscriptions_report

        job = None
        try:
            job, mails = await self._scan(
                "Looking for subscriptions in your receipts", subscriptions_query(), 80
            )
            subs = find_subscriptions(mails, datetime.now().astimezone())
        except Exception as exc:
            log.exception("subscription scan failed")
            if job is not None:
                await self.jobs.settle(job, ok=False, summary=friendly_error(exc))
            await self._notify(
                job.id if job else "subs",
                ok=False,
                title="Couldn't read your receipts",
                body=friendly_error(exc),
            )
            return
        self.mailwatch.set_subscriptions(subs)
        title, body, lines = subscriptions_report(subs)
        await self.jobs.settle(job, ok=True, summary=f"{title}. " + "; ".join(lines))
        self.history = [
            *self.history[-11:],
            (
                "(subscription check)",
                f"(notified the user: {title}. {body} " + "; ".join(lines) + ")",
            ),
        ]
        await self._notify(job.id, ok=True, title=title, body=body, changes=lines)

    async def _cancel_subscription(self, service: str) -> None:
        """Open the cancel page, after an approval card. Pyxi never presses Cancel itself."""
        from pyxi.mailwatch import match_subscription

        sub = match_subscription(self.mailwatch.subscriptions(), service)
        job = await self.jobs.track("Penny", f"Cancel page for {service}")
        if sub is None:
            msg = (
                f"I didn't find {service} in your receipts. "
                "Ask me to look for your subscriptions first."
            )
            await self.jobs.settle(job, ok=False, summary=msg)
            await self._notify(job.id, ok=False, title=f"No {service} subscription found", body=msg)
            return
        # The card names the site: a link from an email could point anywhere (a phishing page).
        from urllib.parse import urlparse

        host = (urlparse(sub.cancel_url).hostname or "") if sub.cancel_url else ""
        where = (
            f"the cancel link in your receipt, on {host}"
            if sub.cancel_from == "email"
            else f"{sub.service}'s account page, on {host}"
            if sub.cancel_url
            else f"{sub.service}'s website (Scout finds the cancel page in the background)"
        )
        ok = await self.hub.request(
            "confirm",
            timeout=900,
            job_id=job.id,
            agent="Pyxi",
            action=f"Open the cancel page for {sub.service}",
            reason=(
                f"{sub.service}: {sub.price()}, last charged {sub.last_charged}.\n"
                f'From: "{sub.email_subject}"\nOpens: {where}.\n'
                + (f"Link: {sub.cancel_url}\n" if sub.cancel_url else "")
                + "\n"
                "Pyxi only opens the page. You press Cancel yourself; nothing is cancelled for you."
            ),
        )
        if not ok:
            await self.jobs.settle(job, ok=False, summary="You said no, so nothing was opened.")
            return
        if sub.cancel_url:
            await self._quick_action("open_url", sub.cancel_url)
            await self.jobs.settle(job, ok=True, summary=f"Opened {sub.service}'s cancel page.")
            await self._notify(
                job.id,
                ok=True,
                title=f"{sub.service}'s cancel page is open",
                body="Press Cancel there to stop it. I didn't change anything.",
                link=sub.cancel_url,
            )
            return
        await self.jobs.settle(job, ok=True, summary="Scout is finding the cancel page.")
        await self.jobs.start(
            "scout",
            f"Find where to cancel the user's {sub.service} subscription ({sub.price()}). Open "
            f"{sub.service}'s own website, go to the account, billing or subscription page, and "
            "stop on the page with the cancel button. Do NOT press cancel, change any setting, "
            "sign up or pay. Report the page's URL and what the user should press.",
        )

    async def _watch_returns(self, quiet: bool = False) -> None:
        """Orders and their return deadlines, with reminders a few days before each closes. The
        daily check (quiet) only speaks up when there's a new order with a deadline."""
        from pyxi.mailwatch import find_orders, orders_query, reminder_day, returns_report

        if not self.connectors.google.connected():
            return
        now = datetime.now().astimezone()
        job = None
        try:
            job, mails = await self._scan(
                "Checking your orders for return windows", orders_query(), 60
            )
            orders = find_orders(mails, now)
        except Exception as exc:
            log.exception("returns scan failed")
            if job is not None:
                await self.jobs.settle(job, ok=False, summary=friendly_error(exc))
            if not quiet:
                await self._notify(
                    job.id if job else "returns",
                    ok=False,
                    title="Couldn't read your orders",
                    body=friendly_error(exc),
                )
            return
        for o in orders:
            when = reminder_day(o, now)
            key = f"return:{o.key}"
            if when and o.return_by and key not in self.mailwatch.reminded():
                end = datetime.fromisoformat(o.return_by)
                what = f"{o.store}, {o.item}" if o.item else f"your {o.store} order"
                self.routines.add(
                    f"Return reminder: {what}",
                    on=when.date().isoformat(),
                    time=when.strftime("%H:%M"),
                    kind="remind",
                    message=(
                        f"Last days to return {what}: the window closes "
                        f"{end:%A} {end.day} {end:%B}."
                    ),
                    key=key,
                )
                self.mailwatch.mark_reminded(key)
        if not self.routines.has_key("returns-watch"):
            self.routines.add(
                "Check new orders for return windows",
                time="09:00",
                kind="returns",
                key="returns-watch",
            )
        await self.hub.emit("routines", routines=self.routines.public())
        dated = {o.key for o in orders if o.return_by}
        new = dated - self.mailwatch.seen_orders()
        self.mailwatch.mark_orders(dated)
        title, body, lines = returns_report(orders, now)
        await self.jobs.settle(job, ok=True, summary=f"{title}. " + "; ".join(lines))
        if quiet and not new:
            return
        self.history = [
            *self.history[-11:],
            ("(returns check)", f"(notified the user: {title}. {body} " + "; ".join(lines) + ")"),
        ]
        await self._notify(job.id, ok=True, title=title, body=body, changes=lines)

    async def _notify(
        self,
        job_id: str,
        *,
        ok: bool,
        title: str,
        body: str,
        link: str | None = None,
        files: list[str] | None = None,
        changes: list[str] | None = None,
        notes: list[str] | None = None,
        actions: list[str] | None = None,
    ) -> None:
        await self.hub.emit(
            "job_ready",
            job_id=job_id,
            agent="Pyxi",
            ok=ok,
            title=title,
            body=body,
            link=link,
            files=files or [],
            changes=changes or [],
            notes=notes or [],
            actions=actions or (["link"] if link else []),
        )

    async def run_due_routines(self, now: datetime | None = None) -> int:
        """Run routines that are due, one at a time, when Pyxi isn't in the middle of something."""
        ran = 0
        for r in self.routines.due(now):
            if r.kind != "turn":  # a reminder or a quiet check: no model, no need to wait
                self.routines.mark_ran(r, now)
                if r.kind == "alarm":
                    await self.ring(r.message or "", alarm=True)
                elif r.kind == "remind":
                    job = await self.jobs.track("Pyxi", r.message or r.task, role="reminder")
                    await self.jobs.settle(job, ok=True, summary=r.message or r.task)
                    await self._notify(job.id, ok=True, title="Reminder", body=r.message or r.task)
                elif r.kind == "returns":
                    self._spawn(self._watch_returns(quiet=True))
                elif r.kind == "job":  # a flight check-in: Scout starts now (paperwork.py)
                    await paperwork.start_routine_job(self, r)
                ran += 1
                continue
            if self._lock.locked() or self.state != "idle":
                break
            self.routines.mark_ran(r, now)
            await self.turn(f"(Routine: {r.task})", shown=f"Routine: {r.task[:80]}", synthetic=True)
            ran += 1
        if ran:
            await self.hub.emit("routines", routines=self.routines.public())
        return ran

    async def _guide_target(
        self, ctx: ScreenContext | None, args: dict[str, Any], label: str
    ) -> tuple[int, int, int, int] | None:
        """Where a guide step points. Words found on screen and element ids are exact; a box is
        the model's guess, so it's zoomed into and checked first (an arrow pointing at the wrong
        button is worse than none)."""
        if ctx is None:
            return None
        exact = target_rect(ctx, {"element_id": args.get("element_id"), "quote": args.get("quote")})
        if exact:
            return exact
        guess = resolve_rect(ctx, None, args.get("box"))
        if guess is None or self.settings.local_mode:
            return guess
        if args.get("action") == "drag" and args.get("to_box"):
            # From one cell to another: a label like "Select A1 to C13" isn't written anywhere,
            # so checking it on screen fails, and the second guess was the fill handle.
            return guess
        x, y, w, h = guess
        if w * h >= 240 * 120:
            return guess
        cw, ch = max(w * 3, 360), max(h * 3, 240)
        region = (x + w // 2 - cw // 2, y + h // 2 - ch // 2, cw, ch)
        crop = ctx.crop(region)
        if crop is None:
            return guess
        origin = (max(ctx.origin[0], region[0]), max(ctx.origin[1], region[1]))
        point, usage = await confirm_target(self.llm, crop, origin, label)
        if usage is not None:
            self._usage = self._usage + usage
        if point is None:
            return None
        return (point[0] - w // 2, point[1] - h // 2, w, h)

    async def guide_clicked(self, how: str = "clicked") -> None:
        """They did the step (clicked the mark, or pressed Done), or did something else that
        changed the screen: look again and give the next step, until the goal is reached."""
        guide = self._guide
        if guide is None or getattr(self, "_guide_advanced", None) is guide:
            return
        for _ in range(150):  # they were quicker than Pyxi's turn: wait for it, don't drop it
            if not self._lock.locked():
                break
            await asyncio.sleep(0.2)
        if self._guide is not guide:
            return
        log.info("guide: %s on step %d (%s)", how, guide.step, guide.label)
        if how != "elsewhere":
            self._guide_advanced = guide  # a click and the page change it causes: one next step
        await asyncio.sleep(0.8)  # let the app react before looking
        if how == "elsewhere":
            # They clicked somewhere other than the mark. Only worth a new look if it changed
            # something (they found another way, or wandered off): a stray click changes nothing.
            now = await self.screen.capture(pixels=True)
            before = getattr(self, "_guide_glance", None)
            after = now.crop(now.window.rect) if now.screenshot is not None else None
            if (
                before is None
                or after is None
                or _same_view(before, after)
                or self._guide is not guide
            ):
                return
            if getattr(self, "_guide_advanced", None) is guide:
                return
            self._guide_advanced = guide
        said = {
            "clicked": f"I did step {guide.step} ({guide.action} '{guide.label}').",
            "done": f"I did step {guide.step} ({guide.action} '{guide.label}') and pressed Done.",
            "elsewhere": f"Instead of '{guide.label}' I clicked something else, and the screen changed.",
            "moved": f"The page changed after step {guide.step}.",
        }[how]
        last = (
            f" That was the last step you planned ({guide.step} of {guide.total}): unless the screen "
            "clearly shows it didn't work, the goal is reached."
            if how != "elsewhere" and guide.total and guide.step >= guide.total
            else ""
        )
        await self.turn(
            f"({said}{last} Look at the screen now and guide me to the next step toward: "
            f"{guide.goal}. If I went a different way, carry on from where I am. If the goal is "
            "reached, tell me and call guide with done=true.)",
            shown="Next step" if how != "clicked" else f"Clicked {guide.label}",
            synthetic=True,
        )

    # ------------------------------------------------------------------ memories (Settings)
    def _memory_stores(self) -> list[tuple[str, Any]]:
        """Pyxi's own memories, and what teammates remembered in their runs (Otto's store)."""
        stores = [("pyxi", self.memory)]
        otto_db = self.settings.data_dir / "otto" / "otto.db"
        if otto_db.exists():
            stores.append(("teammates", MemoryStore(otto_db)))
        return stores

    def memories_public(self) -> list[dict[str, Any]]:
        return [
            {
                "store": name,
                "scope": m.scope,
                "key": m.key,
                "content": m.content,
                "at": m.created_at,
            }
            for name, store in self._memory_stores()
            for m in store.all(None, 500)
        ]

    def forget_memory(self, store: str, scope: str, key: str) -> int:
        target = dict(self._memory_stores()).get(store)
        return target.forget(scope, key) if target is not None else 0

    async def wipe_everything(self) -> str:
        """Settings' "Wipe everything", after a confirm card: every memory (Pyxi's and the
        teammates'), the Memory Palace, and the conversations with their screenshots."""
        mems = self.memories_public()
        ok = await self.hub.request(
            "confirm",
            timeout=300,
            job_id="",
            agent="Pyxi",
            action="Wipe everything Pyxi remembers?",
            reason=(
                f"This forgets {len(mems)} memor{'y' if len(mems) == 1 else 'ies'}, your Memory "
                "Palace history, and your conversations with Pyxi (with their screenshots). It "
                "can't be undone. Your settings, connected accounts, files and the activity log "
                "stay."
            ),
        )
        if not ok:
            return "Okay, nothing was wiped."
        for _, store in self._memory_stores():
            for scope in {m.scope for m in store.all(None, 100000)}:
                store.forget(scope)
        self.spatial.forget_all()
        self.activity.forget_all()
        self.turns.prune(0)
        self.history = []
        self.audit.add(
            "wrote", "Wiped every memory, the Memory Palace and the conversations", agent="You"
        )
        return "Done: Pyxi has forgotten everything it remembered."

    async def stop_everything(self) -> str:
        """The "Stop everything" hotkey and tray item: every teammate job, the hands, Scout's
        browser, open cards (answered "no") and anything queued to go out, all at once."""
        jobs = await self.jobs.stop_all()
        hands_on = self._operator is not None
        if hands_on:
            self._operator.cancelled = True
        self.hub.cancel_all()  # every open card answers "no"
        queued = 0
        for task in list(self._bg):  # errands, sends waiting for a card, a drive starting
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
                queued += 1
        self._prefetch = None
        self.interrupt()
        # Stopped, not talked over: the request that was running is not picked up again.
        self._stopped_turn, self._dropped = (self._turn_id if self._lock.locked() else ""), None
        await self.set_state("idle")
        parts = [f"{jobs} teammate job{'s' if jobs != 1 else ''}"] if jobs else []
        if hands_on:
            parts.append("the mouse and keyboard")
        if queued:
            parts.append(f"{queued} waiting action{'s' if queued != 1 else ''}")
        message = "Stopped everything" + (
            f": {', '.join(parts)}." if parts else ". Nothing was running."
        )
        self.audit.add("clicked", message, agent="You (Stop everything)")
        await self.hub.emit("notice", message=message)
        return message

    def interrupt(self) -> None:
        self._speech_gen += 1
        if self._operator is not None:
            self._operator.cancelled = True  # talking to Pyxi stops her hands too
        self._spawn(self.hub.emit("stop_audio"))

    def _done_so_far(self) -> list[str]:
        """What this turn's tool calls started or did, a few words each (not speech, not lookups)."""
        lookups = INFO_TOOLS | {t.spec.name for t in self.connectors.tools() if t.info}
        done = []
        for c in self._calls:
            if c["tool"] == "say" or c["tool"] in lookups:
                continue
            args = ", ".join(f"{k}: {str(v)[:80]}" for k, v in (c.get("arguments") or {}).items())
            done.append(f"{c['tool']} ({args})" if args else str(c["tool"]))
        return done[:6]

    def _promise_nudge(self, round_no: int) -> str | None:
        """The turn is about to end. If Pyxi said it would do something that no tool call this
        turn carried out ("Heading to Display to lower the brightness.", and only Settings
        opened), the note that sends the model back to do it or say plainly that it can't.
        Once a turn at most, and never past the round limit, so it cannot loop."""
        if self._promise_nudged or round_no >= self.settings.budget.max_tool_rounds:
            return None
        found = undone_promise(self._said, self._calls, self._user_text or "")
        if found is None:
            return None
        self._promise_nudged = True
        line, opened = found
        return (
            f'You told them: "{line}" Nothing in this turn has done that yet'
            + (" (opening it is not doing it)" if opened else "")
            + ". Do it now with the right tool (use_computer for clicks and settings inside an "
            "app), or tell them plainly, in one short line, that you can't and why. Don't repeat "
            "what you have already said."
            # "Writing one now." and then the haiku itself: the words were the job.
            + ("" if opened else " If what you said was itself the whole job, add nothing.")
        )

    def _pick_up(self, user_text: str, behind: str) -> tuple[str, list[str]]:
        """They kept talking, or paused mid-sentence and the request arrived in two parts: the
        turn that had started on the first part was cut off. If it had done nothing yet, its
        words go in front of the new ones, so both are answered as one request (and its own row
        leaves the turn log: this turn is the one that answers it). If it had already acted,
        the words stay where they are and this turn is told what was done. Returns the words
        to answer and any notes for the model."""
        left, self._dropped = self._dropped, None
        if left is None or instant.is_stop(user_text):  # "stop" is never "do that, and stop"
            return user_text, []
        if (
            not (left["by_words"] or left["turn_id"] == behind)  # cut off by Escape, not words
            or time.monotonic() - left["at"] > _CARRY_S
        ):
            if not left["done"]:  # asked and never answered: the conversation still says so
                self.history = [*self.history[-11:], (left["text"], "")]
            return user_text, []
        if left["done"]:
            return user_text, [
                f'Your last turn was cut off because they spoke again. They had asked: "{left["text"]}". '
                f"Already started or done before the cut: {'; '.join(left['done'])}. Their new "
                "words may carry on from that request: finish what is left of it too, and never "
                "redo what is already done."
            ]
        log.info("turn %s was talked over: its words go with the next turn", left["turn_id"])
        self.turns.forget(left["turn_id"])
        return f"{left['text']} {user_text}", []

    async def turn(
        self, user_text: str, *, shown: str | None = None, synthetic: bool = False
    ) -> None:
        """``synthetic``: a routine or a guide step, not words the user actually said (so an
        address in it never counts as the user naming that person)."""
        self.warm_voice()  # the voice loads while the model thinks, if it was let go
        behind = self._turn_id if self._lock.locked() else ""  # sent while that turn still ran
        async with self._lock:
            self._ctx, self._images, self._usage, self._said, self._calls = (
                None,
                [],
                Usage(),
                [],
                [],
            )
            self._scene_retry = False
            self._turn_card = None
            self._visual_turn = False
            self._say_in_board = False  # the reply became a drawing's narration
            self._noted: set[int] = set()  # cue-card points the spoken lines have named
            self._narrated_card = ""
            self._visual_retry = False
            self._user_text = user_text
            self._synthetic = synthetic
            if not synthetic:
                self._guide_fresh = True  # a new request: a guide in it counts from step 1
            self._pending_say = ""  # an answer held back during a lookup round
            self._promise_nudged = False  # "you said you'd do it, so do it": once a turn at most
            self._refused = False  # a tool that can refuse did, this turn
            self._cut_short = False  # a round was dropped because they spoke again
            self._screens_text: str | None = None  # every screen and what's on it, once a turn
            self._shown_screen: str | None = None  # the screen look_again looked at, if it did
            self._opened_urls: set[str] = set()  # pages opened by this request: each once
            # Mail, pages and documents this turn read: after any, every outward action asks on
            # a fresh card, and its card says what came from them (pyxi/safety.py).
            self._turn_reads: safety.Reads = []
            self._narrated_items: set[int] = set()  # cue-card points already said as they arrived
            self._survey: asyncio.Task | None = None  # finding the parts of a picture
            self._searches = 0
            self._looked: dict[tuple[str, str], str] = {}  # lookups this turn, and what they said
            self._lookups: dict[str, int] = {}
            self._repeats = 0
            self._turn_gen = self._speech_gen  # push-to-talk bumps the gen: this turn is then stale
            turn_id = "turn_" + secrets.token_hex(4)
            self._turn_id = turn_id
            started = time.monotonic()
            await self.hub.emit("user", text=shown or user_text, turn_id=turn_id)
            error = None
            cut_off: list[str] = []  # what a turn they talked over had already done, if it had
            if not synthetic:
                from pyxi import wakename

                user_text = wakename.normalize(user_text)  # typed or heard: "Your pixxit..." -> "Pyxi, ..."
                user_text, cut_off = self._pick_up(user_text, behind)
                self._user_text = user_text
            try:
                hit = None if synthetic else instant.match(user_text)
                asked = None if synthetic or hit else weather.simple_question(user_text)
                song = None if synthetic or hit or asked else instant.play_request(user_text)
                if not synthetic and instant.is_stop(user_text):
                    # A spoken "stop" stops for real: jobs, the hands, cards and anything queued,
                    # like the hotkey. Before, it only made the model say "Stopping."
                    message = await self.stop_everything()
                    self._calls.append(
                        {"round": 1, "tool": "stop_everything", "arguments": {}, "instant": True}
                    )
                    self._speak("Stopped." if "Nothing was running" not in message else "Okay.")
                elif not synthetic and (remind := alarms.reminder_request(user_text)) is not None:
                    # "Remind me in 10 minutes to stretch": once, not every day at that time.
                    if isinstance(remind, alarms.Timer):
                        await self._set_timer(remind)
                    else:
                        await self._set_alarm(remind)
                elif not synthetic and self._ditto_watching() and _DONE_SHOWING.match(user_text):
                    # Ditto is watching and they say "done": stop and let it work out the rest,
                    # at once (the model sometimes asked "what were you working on?").
                    note = await specialists.handle(self, "watch_me", {"action": "stop"})
                    self._calls.append(
                        {"round": 1, "tool": "watch_me", "arguments": {"action": "stop"}, "instant": True}
                    )
                    self._speak(
                        "Got it. Ditto's working out the rest, and the plan comes on a card before anything changes."
                        if note and note.startswith("Started")
                        else "Okay, I've stopped watching."
                    )
                elif not synthetic and alarms.list_request(user_text):
                    self._list_alarms()
                elif not synthetic and (which := alarms.cancel_request(user_text)) is not None:
                    await self._cancel_alarm(which)
                elif not synthetic and (alarm := alarms.alarm_request(user_text)) is not None:
                    await self._set_alarm(alarm)  # about a second: no model, no Clock app
                elif not synthetic and (timer := alarms.timer_request(user_text)) is not None:
                    await self._set_timer(timer)
                elif asked is not None and await self._weather_now(*asked):
                    pass  # answered from the forecast in about a second
                elif song is not None:
                    self._calls.append(
                        {
                            "round": 1,
                            "tool": "play_music",
                            "arguments": {"query": song[0], "app": song[1]},
                            "instant": True,
                        }
                    )
                    self._speak(random.choice(["Putting on {0}.", "{0}, coming up.", "Cueing up {0}."]).format(song[0]))
                    await self._play_music({"query": song[0], "app": song[1]})
                elif hit is None or not await self._instant(hit):
                    await self._graph.ainvoke(
                        {
                            "user_text": user_text,
                            "turn_id": turn_id,
                            "round": 0,
                            "notes": cut_off,
                            "needs_more": False,
                            "stop": None,
                        }
                    )
            except asyncio.CancelledError:
                # "Stop everything" cancels the turn itself. It still goes in the history, with
                # what it did and cost: a stopped turn used to vanish, its spend with it.
                self.history = [*self.history[-11:], (user_text, " ".join(self._said))]
                self._record_turn(turn_id, user_text, started, "stopped")
                self._spawn(
                    self.hub.emit(
                        "turn_done",
                        turn_id=turn_id,
                        cost_usd=round(self._usage.cost_usd, 5),
                        tokens=self._usage.total_tokens,
                        spent_today=round(self.turns.spent_today(), 4),
                        daily_budget=self.settings.budget.daily_cost_usd,
                    )
                )
                raise
            except Exception as exc:
                log.exception("turn failed")
                error = f"{type(exc).__name__}: {exc}"
                await self.say_line("Oops, my brain hiccupped. Mind trying that again?")
            if self._guide is not None and not any(c.get("tool") == "guide" for c in self._calls):
                # A reply with no next step ends the coaching: an arrow and "3/3 Press Escape"
                # left on screen for 15 minutes after "I can't confirm it from here" was worse.
                self._guide, self._guide_trail = None, []
                await self.hub.emit("guide", active=False, finished=False, at=None)
            said = " ".join(self._said)
            if not error and self._speech_gen != self._turn_gen and (not said or self._cut_short):
                # They spoke again (or pressed talk) before the answer came: the answer was
                # dropped on purpose, so a silent or half-answered turn isn't a bug then.
                error = "interrupted"
            heard: str | None = said  # what the conversation keeps as Pyxi's answer
            if error == "interrupted" and not synthetic and self._stopped_turn != turn_id:
                # Talked over, not stopped: the turn their new words start gets these words too
                # (or, if this one had already acted, is told what it did). See _pick_up.
                done = self._done_so_far()
                self._dropped = {
                    "turn_id": turn_id,
                    "text": user_text,
                    "done": done,
                    "by_words": self._words_gen == self._speech_gen,
                    "at": time.monotonic(),
                }
                # A half nobody answered is not left in the conversation: it is carried forward.
                heard = (
                    f"{said} (Cut off here: they spoke again before you finished. Already "
                    f"started or done: {'; '.join(done)}.)".strip()
                    if done
                    else None
                )
            if heard is not None:
                self.history = [*self.history[-11:], (user_text, heard)]
            self._record_turn(turn_id, user_text, started, error)
            if self._speech:
                await asyncio.gather(self._speech, return_exceptions=True)
            await self.hub.emit(
                "turn_done",
                turn_id=turn_id,
                cost_usd=round(self._usage.cost_usd, 5),
                tokens=self._usage.total_tokens,
                spent_today=round(self.turns.spent_today(), 4),
                daily_budget=self.settings.budget.daily_cost_usd,
            )
            await self.set_state("idle")

    def _record_turn(
        self, turn_id: str, user_text: str, started: float, error: str | None
    ) -> None:
        """The turn, into the history: what was asked, seen, done, said and spent."""
        ctx = self._ctx
        image_path = None
        if ctx and self._images:
            image_path = self.turns.save_image(turn_id, self._images[0])
        self.turns.record(
            {
                "id": turn_id,
                "ts": time.time(),
                "user_text": user_text,
                "app": ctx.window.app if ctx else None,
                "window": ctx.window.title if ctx else None,
                "screen_text": ctx.render(60) if ctx else None,
                "image_path": image_path,
                "tool_calls": self._calls,
                "said": " ".join(self._said),
                "rounds": len({c["round"] for c in self._calls}),
                "input_tokens": self._usage.input_tokens + self._usage.cache_read_tokens,
                "output_tokens": self._usage.output_tokens,
                "cost_usd": self._usage.cost_usd,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": error,
            }
        )

    # ================================================================== graph
    def _build_graph(self) -> Any:
        async def perceive(state: TurnState) -> TurnState:
            await self.set_state("looking")
            task, self._prefetch = self._prefetch, None
            ctx = None
            if task is not None:
                try:
                    ctx = await task
                except Exception:
                    log.warning("screen prefetch failed; capturing again")
            if ctx is None:
                ctx = await self._capture()
            if named := self._named_window(state.get("user_text", ""), ctx):
                if windows.is_minimized(named):  # "...in Notepad" with Notepad tucked away
                    windows.snap(named, "restore")
                    await asyncio.sleep(0.45)
                ctx = await self._capture(hwnd=named)
            elif (top := self._screen_named_in(state.get("user_text", ""))) is not None:
                # "What's on my right screen?": look at that screen from the start (one model
                # round instead of two, about 5 s sooner).
                ctx = await self._capture(hwnd=top)
                self._shown_screen, self._screens_text = self._named_screen, None
            self._ctx = ctx
            await self.hub.emit(
                "capture",
                app=ctx.window.app,
                window=ctx.window.title[:120],
                excluded=ctx.excluded,
                elements=len(ctx.elements),
            )
            if ctx.excluded and not self.screen.paused:  # said plainly, not left to the model
                await self.hub.emit(
                    "notice", message=f"{ctx.excluded} is private, so I can't see that window."
                )
            if not ctx.excluded:
                sid = self.spatial.record(ctx, force=True)
                self.activity.log(
                    "turn",
                    app=ctx.window.app,
                    title=ctx.window.title,
                    snapshot_id=sid,
                    detail=state.get("user_text", ""),
                )
                if image := ctx.model_image(self.settings.screen.model_image_width):
                    self._images = [image]
            return {}

        async def think(state: TurnState) -> TurnState:
            cfg = state
            budget = self.settings.budget
            if self.turns.spent_today() >= budget.daily_cost_usd:
                await self.say_line(
                    "I've used up today's budget, so I'm taking a breather. You can raise it in settings."
                )
                return {"stop": "daily budget", "needs_more": False}
            if self._speech_gen != self._turn_gen:
                # They spoke again before this round began: no model call for an answer that
                # would be dropped. The turn their new words start takes it from here.
                self._cut_short = True
                return {"stop": "interrupted", "needs_more": False}
            await self.set_state("thinking")
            self._say_in_board = False  # a board only takes over the reply of its own round
            pyxi = self.registry.get("pyxi")
            system = prompts.system_prompt(
                pyxi.persona if pyxi else "", self.registry.roster_text()
            )
            now_t = time.time()
            jobs = "\n".join(
                j.brief()
                for j in self.jobs.recent(6)
                if j.role != "model" and (j.ended_at is None or now_t - j.ended_at < 120)
            )
            memories = "\n".join(
                m.render() for m in self.memory.search("agent:pyxi", cfg["user_text"], 6)
            )
            text = prompts.turn_prompt(
                user_text=cfg["user_text"],
                history=self.history,
                screen=(
                    self._ctx.render(self.settings.screen.max_elements)
                    if self._ctx
                    else "(no screen)"
                )
                + self._screens_note(),
                jobs=jobs,
                memories=memories,
                notes=state.get("notes", []),
                on_screen=object_summary(self._object) if self._object else "",
                now=await self._now(),
                language=self._reply_language(),
                budget_line=f"spent today ${self.turns.spent_today():.3f} of ${budget.daily_cost_usd:.2f}; this turn ${self._usage.cost_usd:.3f} of ${budget.max_cost_usd:.2f}",
            )
            parts: list[TextPart | ImagePart] = [TextPart(text)] + [
                ImagePart(img, "image/jpeg") for img in self._images
            ]
            connector_tools = self.connectors.tools()
            early: set[str] = set()

            head = {"text": ""}  # the first sentence(s) of `say`, already being spoken
            live_items = {"n": 0}
            scene_preview = {"shown": False}

            held = {"say": None}  # a later round's reply, waiting to go just before its card
            claim = {"say": None}  # a line that says work is under way, until we see what follows

            async def speak_early(call: ToolCall, wait: bool = True) -> None:
                # Streamed: start talking the moment the spoken line is complete, while the cue
                # card and drawings are still arriving.
                if call.name == "say" and not first_round:
                    held["say"] = call  # may be lookup chatter; only said if a card follows
                    return
                if call.name == "say" and not early and self._speech_gen == self._turn_gen:
                    if wait and not head["text"] and claims_work(str(call.arguments.get("text", ""))):
                        # "Putting the table in now.": true only once the tool has agreed. If the
                        # next call is one that can refuse, the line waits for its answer.
                        claim["say"] = call
                        return
                    early.add(call.id)
                    self._calls.append(
                        {
                            "round": state.get("round", 0) + 1,
                            "tool": "say",
                            "arguments": call.arguments,
                        }
                    )
                    text = str(call.arguments.get("text", ""))
                    if head["text"] and text.startswith(head["text"]):
                        text = text[
                            len(head["text"]) :
                        ].strip()  # the rest, after the first sentence
                    if text:
                        await self._act("say", {"text": text})

            async def as_it_streams(i: int, name: str, raw: str) -> None:
                if self._speech_gen != self._turn_gen:
                    return
                if claim["say"] is not None and name != "say":
                    waiting, claim["say"] = claim["say"], None
                    if name not in CAN_REFUSE:
                        await speak_early(waiting, wait=False)  # nothing here says no: say it now
                if name == "say" and first_round and not early and not head["text"]:
                    # The first sentence goes to the voice as soon as it's written.
                    got = partial_string(raw, "text")
                    first = first_chunk(got[0]) if got and not got[1] else ""
                    if first and not claims_work(first):
                        head["text"] = first
                        self._noted |= {
                            int(m.group(2))
                            for m in MARKER.finditer(first)
                            if m.group(1).lower() == "note"
                        }
                        self._pending_say = ""
                        self._speak(strip_filler(first))
                elif name == "explain_in_3d" and not scene_preview["shown"]:
                    # Nodes and edges are in once the steps begin: show the scene now, and let
                    # its narration (half of the JSON) finish arriving while it flies in.
                    if '"steps"' in raw and (scene := self._scene_preview(raw)) is not None:
                        scene_preview["shown"] = True
                        await self.show_scene(scene)
                elif name in ("explain_on_screen", "explain_on_picture"):
                    if held["say"] is not None and held["say"].id not in early:
                        # This round's reply came first: say it before the points.
                        call, held["say"] = held["say"], None
                        early.add(call.id)
                        self._calls.append(
                            {
                                "round": state.get("round", 0) + 1,
                                "tool": "say",
                                "arguments": call.arguments,
                            }
                        )
                        await self._act("say", dict(call.arguments))
                    # A picture: start finding its parts the moment we know which picture.
                    head_args = picture_head(raw, on_picture=name == "explain_on_picture")
                    if head_args:
                        self._start_survey(head_args)
                    # Each cue-card point is shown, and said, as soon as it has fully arrived.
                    items = complete_items(raw)
                    if len(items) > live_items["n"]:
                        live_items["n"] = len(items)
                        title = partial_string(raw, "title")
                        await self._show_card_so_far(
                            title[0] if title and title[1] else "", items, head_args
                        )

            first_round = state.get("round", 0) == 0
            extra: dict[str, Any] = {}
            if isinstance(self.llm, StreamingChat):
                extra = {"on_call": speak_early, "on_partial": as_it_streams}
            response = await self.llm.complete(
                system=system,
                messages=[Message("user", parts)],
                # Reading accounts happens here; anything that sends, books or creates is an
                # errand: background, checked, approved, and reported in the notch.
                tools=[*self._companion_tools(), *(t.spec for t in connector_tools if t.info)],
                max_tokens=9000,  # a detailed 3D model is a long tool call
                **extra,
            )
            self._usage = self._usage + response.usage
            round_no = state.get("round", 0) + 1
            if self._speech_gen != self._turn_gen:
                # They pressed talk while this was thinking: drop it, the new question wins.
                self._cut_short = True
                return {"round": round_no, "stop": "interrupted", "needs_more": False}
            if not response.tool_calls:
                text, promised = drop_false_promises(response.text or "")
                if promised and not text and round_no < self.settings.budget.max_tool_rounds:
                    return {
                        "round": round_no,
                        "needs_more": True,
                        "notes": [
                            *state.get("notes", []),
                            "You said you'd search or check, but didn't call a tool. Call web_search "
                            "now, or give your answer without promising anything.",
                        ],
                    }
                if text:
                    self._speak(text)
                if nudge := self._promise_nudge(round_no):
                    return {
                        "round": round_no,
                        "needs_more": True,
                        "notes": [*state.get("notes", []), nudge],
                    }
                return {"round": round_no, "needs_more": False}
            notes: list[str] = []
            needs_more = False
            # Show first, then talk: when a 3D model or scene is on screen its own narration does
            # the explaining, so the spoken reply is kept to one sentence.
            visual = {
                "build_3d_model",
                "edit_3d_model",
                "explain_in_3d",
                "explode_view",
                "sketch_diagram",
                "show_scene",
            }
            calls = sorted(response.tool_calls, key=lambda c: c.name not in visual)
            # The spoken reply of this round, in case a drawing arrives without its own steps.
            self._round_say = " ".join(
                str(c.arguments.get("text", "")) for c in calls if c.name == "say"
            )
            retrying, self._visual_retry = self._visual_retry, False
            looks_up = any(
                c.name in INFO_TOOLS
                or any(t.info and t.spec.name == c.name for t in connector_tools)
                for c in calls
            )
            # A line said with a tool that can refuse waits for that tool's answer: "Putting a
            # table into your doc now." is never said and then taken back in the next sentence.
            gate = max((i for i, c in enumerate(calls) if c.name in CAN_REFUSE), default=-1)
            waits = [c for c in calls[: max(gate, 0)] if c.name == "say" and c.id not in early]
            if waits:
                rest = [c for c in calls[: gate + 1] if all(c is not w for w in waits)]
                calls = [*rest, *waits, *calls[gate + 1 :]]
            unsaid = False  # the tool refused, or said its own line: this round's line is dropped
            for call in calls:
                if self._speech_gen != self._turn_gen:
                    # They spoke again mid-round: the rest of it is not done behind their back.
                    # What was done so far is passed to the turn their new words start.
                    self._cut_short = True
                    return {"round": round_no, "stop": "interrupted", "needs_more": False}
                if call.id in early:
                    continue  # already said, while the rest was still streaming in
                if call.name == "say" and looks_up and self._said:
                    # Mid-lookup chatter ("my first search came up empty"): held back, and only
                    # said if the turn ends before a newer answer.
                    self._pending_say = str(call.arguments.get("text", ""))
                    continue
                if call.name == "say" and retrying and self._said:
                    continue  # a retried drawing mustn't make Pyxi say its reply twice
                if call.name == "say" and said_before(
                    str(call.arguments.get("text", "")), self._said
                ):
                    continue  # the same thing again, in the same or other words: said once
                if call.name == "say" and unsaid:
                    continue  # it would claim the job is under way; the model speaks next, knowing
                self._calls.append(
                    {"round": round_no, "tool": call.name, "arguments": call.arguments}
                )
                if call.arguments.get("__unparseable__"):
                    # Cut off by the length limit or garbled in transit: ask for it again.
                    notes.append(
                        f"TOOL_INVALID: your {call.name} call was cut off or malformed. "
                        f"Call {call.name} again, shorter."
                    )
                    needs_more = self._visual_retry = True
                    unsaid = unsaid or call.name in CAN_REFUSE  # it never ran: nothing to claim yet
                    continue
                is_info = any(t.info and t.spec.name == call.name for t in connector_tools)
                lookup = is_info or call.name in INFO_TOOLS
                key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
                looks = self._lookups.setdefault(call.name, 0) + 1 if lookup else 0
                if lookup:
                    self._lookups[call.name] = looks
                if lookup and (key in self._looked or looks > 3):
                    # The same lookup again (an empty memory, a calendar that said nothing new):
                    # looping on it only makes them wait. Answer with what's known.
                    seen = self._looked.get(key) or "nothing new"
                    notes.append(
                        f"{call.name}: you already looked this turn and got: {seen[:300]}. Don't "
                        "look again: answer them now with what you have, and say plainly if "
                        "that's nothing."
                    )
                    self._repeats += 1
                    needs_more = needs_more or self._repeats <= 1
                    continue
                spoken = len(self._said)
                try:
                    note = await self._act(call.name, dict(call.arguments))
                except Exception as exc:
                    log.exception("tool %s failed", call.name)
                    note = f"{call.name} failed: {exc}"
                if call.name in CAN_REFUSE:
                    if note and note.startswith(f"{call.name} failed:"):
                        needs_more = True  # it broke: the model says so, instead of the claim
                    self._refused = bool(
                        note and note.startswith((f"{call.name}:", f"{call.name} failed:"))
                    )
                    # It said no, or it said its own line (which is then the true one).
                    unsaid = unsaid or self._refused or len(self._said) > spoken
                if note and call.name in safety.UNTRUSTED_TOOLS:
                    if call.name in safety.SOURCE_LABEL:
                        self._turn_reads.append((safety.SOURCE_LABEL[call.name], note))
                    note = safety.fence(call.name, note)  # the web, files, mail: data only
                if lookup:
                    self._looked[key] = note or ""
                if note:
                    notes.append(note)
                if (
                    is_info
                    or call.name in INFO_TOOLS
                    or (
                        note
                        and note.startswith(("SCENE_INVALID", "MODEL_INVALID", "SKETCH_INVALID"))
                    )
                ):
                    needs_more = True
                if note and note.startswith(("SCENE_INVALID", "MODEL_INVALID", "SKETCH_INVALID")):
                    self._visual_retry = True
                # A tool that couldn't do its job ("errand: no accounts are connected yet") goes
                # back to the model, so the user hears what happened instead of silence.
                if note and note.startswith(f"{call.name}:") and call.name not in QUIET_NOTES:
                    needs_more = True
            self._narrate_card()
            if needs_more and self._said:
                notes.append(
                    "ALREADY_SAID (out loud, this turn): \"" + " ".join(self._said)[-400:] + "\". "
                    "Don't say any of it again, reworded or not. If you speak next, say only what's new "
                    "(the answer, the result), in one or two short sentences, or nothing."
                )
            opened = any(
                c.name == "quick_action" and c.arguments.get("action") in ("open_url", "open_app")
                for c in calls
            )
            if (
                opened
                and all(c.name in ("say", "quick_action") for c in calls)
                and not needs_more
                and not _JUST_OPEN.match(self._user_text or "")
                and _WANTS_ANSWER.search(self._user_text or "")
                and round_no < self.settings.budget.max_tool_rounds
            ):
                # "Find coffee near me": opening Maps isn't the answer. Look at what opened and
                # answer from it, even if the model forgot to ask for a look.
                await self._act("look_again", {"seconds": 3})
                needs_more = True
                notes.append(
                    "It's open on their screen now (you're looking at it). Answer their question "
                    "from it: mark the best options with explain_on_screen and say your pick. "
                    "Don't open it again."
                )
            promised = any(
                drop_false_promises(str(c.arguments.get("text", "")))[1]
                for c in calls
                if c.name == "say"
            )
            if (
                promised
                and all(c.name == "say" for c in calls)
                and not needs_more
                and round_no < self.settings.budget.max_tool_rounds
            ):
                # "Checking the news on that one." and then nothing: make it true.
                needs_more = True
                notes.append(
                    "You told them you'd check, so check now: call web_search (or delegate for "
                    "deep research), then answer. Don't say again that you're checking."
                )
            if not needs_more and (nudge := self._promise_nudge(round_no)):
                # "Heading to Display to lower the brightness." and then only Settings opened.
                needs_more = True
                notes.append(nudge)
            return {
                "round": round_no,
                "notes": [*state.get("notes", []), *notes],
                "needs_more": needs_more,
            }

        def route(state: TurnState) -> str:
            b = self.settings.budget
            if (
                state.get("needs_more")
                and state.get("round", 0) < b.max_tool_rounds
                and self._usage.cost_usd < b.max_cost_usd
            ):
                return "think"
            return "finish"

        async def finish(state: TurnState) -> TurnState:
            if self._pending_say and state.get("stop") != "interrupted":
                held, _ = drop_false_promises(self._pending_say)  # never "searching now" at the end
                if held:
                    self._speak(held)  # the latest answer from a lookup round
                self._pending_say = ""
            if not self._said and not state.get("stop"):
                card = self._turn_card
                if card is not None:
                    # Drew a note card but forgot to speak: narrate its own points, in sync.
                    points = " ".join(f"[note:{it.n}] {it.text.rstrip('.')}." for it in card.items)
                    self._speak(f"Here's the gist. {points}")
                elif self._visual_turn:
                    pass  # the drawing or model narrates itself; a canned line would only interrupt
                elif self._refused:
                    # The tool said no, its line was held back, and the model said nothing
                    # after: never "On it." for a job that did not start.
                    self._speak("I couldn't do that just now.")
                elif any(c.get("tool") in _BACKGROUND_TOOLS for c in self._calls):
                    self._speak("On it.")  # started, not finished: "Done." then a failure misleads
                elif any(
                    c.get("tool") not in INFO_TOOLS | QUIET_NOTES | {"guide"} for c in self._calls
                ):
                    self._speak("Done.")  # something was really done (a looked-at screen or a guide arrow isn't)
                else:
                    self._speak("Hmm, I'm not sure what to say to that.")
            return {}

        g = StateGraph(TurnState)
        g.add_node("perceive", perceive)
        g.add_node("think", think)
        g.add_node("finish", finish)
        g.add_edge(START, "perceive")
        g.add_edge("perceive", "think")
        g.add_conditional_edges("think", route, {"think": "think", "finish": "finish"})
        g.add_edge("finish", END)
        return g.compile()

    # ================================================================== tools
    async def _act(self, name: str, args: dict[str, Any]) -> str | None:
        ctx = self._ctx
        if name in TOOL_ALIASES:  # a name the model made up for a real tool
            name, extra = TOOL_ALIASES[name]
            args = {**extra, **args}
        if name == "say":
            text = strip_filler(str(args.get("text", "")))
            self._pending_say = ""
            self._noted |= {
                int(m.group(2)) for m in MARKER.finditer(text) if m.group(1).lower() == "note"
            }
            if self._say_in_board:
                self._said.append(split_markers(text)[0])  # said by the drawing, step by step
                return None
            if self._visual_turn:
                text = visual_opener(text)
            self._speak(text)
            return None
        if name in {"point", "circle", "highlight"}:
            if name == "point":
                at = resolve_point(
                    ctx,
                    args.get("element_id"),
                    [args["x"], args["y"]] if "x" in args and "y" in args else None,
                )
                rect = (
                    target_rect(ctx, args)
                    if args.get("quote")
                    else resolve_rect(ctx, args.get("element_id"), None)
                )
                if args.get("quote") and rect:
                    at = (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
                ann = (
                    Annotation(kind="point", at=at, rect=rect, label=args.get("label"))
                    if at
                    else None
                )
            else:
                rect = target_rect(ctx, args)
                ann = Annotation(kind=name, rect=rect, label=args.get("label")) if rect else None
            if ann is None:
                return f"{name}: couldn't find that on screen."
            await self.hub.emit("annotate", annotations=[ann.model_dump()])
            self._watch_window()
            return None
        if name == "arrow":
            a = resolve_point(ctx, args.get("from_element_id"), args.get("from_xy"))
            b = resolve_point(ctx, args.get("to_element_id"), args.get("to_xy"))
            if not (a and b):
                return "arrow: couldn't resolve both ends."
            await self.hub.emit(
                "annotate",
                annotations=[
                    Annotation(kind="arrow", at=a, to=b, label=args.get("label")).model_dump()
                ],
            )
            self._watch_window()
            return None
        if name == "clear_drawings":
            await self.hub.emit("clear")
            self._placed.put("card", None, 0)
            return None
        if name == "explain_on_screen":
            found = None
            if args.get("style") == "on_picture":
                self._start_survey(args)
                found = await self._survey_result()
            keep = self._turn_card if self._narrated_items else None
            card = build_card(ctx, args, self._placed.others("card"), found, keep)
            if not card.items:
                return "explain_on_screen: give at least one point with text."
            if self._turn_card is not None and self._narrated_items:
                # It was shown point by point while streaming: same card, same spot, now complete.
                card.id, card.place = self._turn_card.id, self._turn_card.place
            await self.hub.emit("notes", card=card.model_dump(mode="json"))
            if card.place:
                w, h = card_size([it.text for it in card.items], card.title)
                linger = min(22.0, 7.0 + 1.8 * len(card.items)) + 8.0  # reading time, plus speech
                self._placed.put("card", (*card.place, w, h), linger)
            self._turn_card = card
            self._watch_window()
            if args.get("bill"):  # what you owe and what looks off, in the task tray too
                await paperwork.bill_chip(self, args["bill"])
            missing = [it.n for it in card.items if it.mark != "none" and it.rect is None]
            return f"explain_on_screen: points {missing} had nothing to frame." if missing else None
        if name == "guide":
            if args.get("done"):
                last, self._guide = self._guide, None
                at = None
                if last is not None:  # celebrate where they made the last move
                    x, y, w, h = last.rect
                    at = (x + w // 2, y + h // 2)
                await self.hub.emit("guide", active=False, finished=last is not None, at=at)
                return None
            label = str(
                args.get("label")
                or (
                    ctx.element(int(args["element_id"])).name
                    if ctx
                    and args.get("element_id") is not None
                    and ctx.element(int(args["element_id"]))
                    else "here"
                )
            )[:40]
            action = str(args.get("action") or "click")
            rect = await self._guide_target(ctx, args, label)
            if rect is None:
                if action in ("press", "look") and ctx is not None:
                    rect = ctx.window.rect  # keys and looks don't need a precise spot
                else:
                    return (
                        f"guide: couldn't find '{label}' on screen. Use an element id or the exact "
                        "words on it (quote), or tell them where it is in words."
                    )
            to = None
            if action == "drag":
                dest = target_rect(
                    ctx,
                    {
                        "element_id": args.get("to_element_id"),
                        "quote": args.get("to_quote"),
                        "box": args.get("to_box"),
                    },
                )
                to = (dest[0] + dest[2] // 2, dest[1] + dest[3] // 2) if dest else None
            going = self._guide if not getattr(self, "_guide_fresh", False) else None
            self._guide_fresh = False
            step = going.step + 1 if going else 1  # one guide at a time: keep counting
            total = (
                int(args["steps_total"]) if str(args.get("steps_total") or "").isdigit() else None
            )
            if going and not total:
                total = going.total
            total = max(total, step) if total else None
            trail = (getattr(self, "_guide_trail", []) if going else []) + [label.lower()]
            self._guide_trail = trail
            if len(trail) >= 4 and trail[-1] == trail[-3] and trail[-2] == trail[-4]:
                # Round in circles (View, Word wrap, View, Word wrap): they've done it already.
                self._guide, self._guide_trail = None, []
                await self.hub.emit("guide", active=False, finished=True, at=None)
                return (
                    f"guide: they've already done '{trail[-2]}' then '{trail[-1]}' twice, so the "
                    "task is done as far as the screen can show. Don't guide again: tell them it's "
                    "done and how to check it."
                )
            self._guide = Guide(
                goal=str(args["goal"])[:200],
                label=label,
                rect=rect,
                step=step,
                action=action,
                total=total,
            )
            self._guide_glance = ctx.crop(ctx.window.rect) if ctx else None
            hint = str(args.get("text") or args.get("direction") or "")[:80] or None
            ann = Annotation(
                kind="click",
                rect=rect,
                to=to,
                label=label,
                ttl_s=900,
                guide=True,
                action=action,
                hint=hint,
                step=step,
                total=total,
            )
            await self.hub.emit("annotate", annotations=[ann.model_dump()])
            await self.hub.emit(
                "guide",
                active=True,
                step=step,
                total=total,
                goal=self._guide.goal,
                label=label,
                action=action,
            )
            self._watch_window()
            return None
        if name == "explain_in_3d":
            try:
                scene = parse_scene(args)
            except ValidationError as exc:
                if self._scene_retry:
                    return "explain_in_3d failed twice; explain in words instead."
                self._scene_retry = True
                return f"SCENE_INVALID: fix these and call explain_in_3d again: {exc.errors()[:6]}"
            self._anchor_scene(scene)
            await self.show_scene(scene)
            return f"Showing 3D scene '{scene.title}' ({len(scene.nodes)} nodes, {len(scene.steps)} steps)."
        if name == "sketch_diagram":
            spoken: list[str] = []
            if not args.get("steps") and isinstance(args.get("shapes"), list):
                # No steps: narrate with the spoken reply instead, so nothing is said twice.
                # (Not if the reply was already said, on a retry: it would be heard twice.)
                spoken = (
                    split_sentences(split_markers(self._round_say)[0])
                    if self._round_say and not self._said
                    else []
                )
                args["steps"] = steps_from_speech(
                    spoken, args["shapes"], str(args.get("title", ""))
                )
            try:
                board = Sketchboard.model_validate(args)
            except ValidationError as exc:
                if self._scene_retry:
                    return "sketch_diagram failed twice; explain in words instead."
                self._scene_retry = True
                return (
                    f"SKETCH_INVALID: fix these and call sketch_diagram again: {exc.errors()[:6]}"
                )
            # Only once it's really on screen does the drawing take over the reply.
            self._say_in_board = bool(spoken)
            await self.show_sketch(board)
            note = (
                f"Drawing '{board.title}' ({len(board.shapes)} shapes, {len(board.steps)} steps)."
            )
            return f"{note} Repairs: {'; '.join(board.notes[:4])}" if board.notes else None
        if name == "build_3d_model" and self._builder_on() and not args.get("parts"):
            # A brief: the 3D builder makes the model in the background while Pyxi talks.
            self._visual_turn = True
            self._spawn(self._build_from_brief(args))
            return None
        if name == "build_3d_model":
            try:
                model = ObjectScene.model_validate(args)
            except ValidationError as exc:
                if self._scene_retry:
                    return "build_3d_model failed twice; explain in words instead."
                self._scene_retry = True
                return f"MODEL_INVALID: fix these and call build_3d_model again: {exc.errors()[:6]}"
            await self.show_object(model, restart=True)
            note = f"Showing 3D model '{model.title}' ({len(model.parts)} parts, {len(model.beats)} beats)."
            return f"{note} Repairs: {'; '.join(model.notes[:6])}" if model.notes else None
        if name == "edit_3d_model":
            if self._object is None:
                return (
                    "edit_3d_model: there's no 3D model on screen; build one with build_3d_model."
                )
            try:
                edit = ObjectEdit.model_validate(args)
                model = apply_edit(self._object, edit)
            except ValidationError as exc:
                return f"MODEL_INVALID: fix these and call edit_3d_model again: {exc.errors()[:6]}"
            await self.show_object(model, restart=edit.beats is not None)
            return None
        if name == "explode_view":
            if ctx is None or ctx.screenshot is None:
                return "explode_view: I don't have a screenshot."
            scene = build_exploded_scene(
                ctx,
                int(args["element_id"]),
                self.settings.crops_dir,
                title=args.get("title"),
                steps=args.get("steps"),
            )
            await self.show_scene(scene)
            return f"Exploded view of [{args['element_id']}] with {len(scene.nodes)} pieces."
        if name == "show_scene":
            key = str(args["name"])
            if key in OBJECT_GALLERY:
                await self.show_object(OBJECT_GALLERY[key](), restart=True)
            else:
                await self.show_scene(get_scene(key))
            return None
        if name == "delegate":
            then = (
                Handoff(agent=args["then_agent"], task=args.get("then_task") or "Use the result.")
                if args.get("then_agent") and self.registry.get(str(args["then_agent"]))
                else None
            )
            files = [str(f) for f in args.get("files", []) or [] if Path(str(f)).is_file()]
            job = await self.jobs.start(
                str(args["agent"]),
                str(args["task"]),
                start_url=args.get("start_url"),
                then=then,
                files=files,
            )
            return f"Started {job.id}: {job.agent_name} is on it."
        if name == "weather":
            place = str(args.get("place") or "").strip() or await self._home_city()
            if not place:
                return "weather: which city? Ask them in a few words."
            f = await weather.forecast(place)
            return f.describe() if f else f"weather: couldn't find a forecast for {place!r}."
        if name == "web_search":
            self._searches = getattr(self, "_searches", 0) + 1
            if self._searches > 3:
                return "web_search: that's enough searching. Answer now from what you've found, and say how sure you are."
            return await web.search_text(str(args.get("query", ""))[:200])
        if name == "read_page":
            page = await web.read_text(str(args.get("url", ""))[:500])
            # Every extra page is another model round (4-5 s): one page answers one fact.
            return (page or "") + (
                "\n(If this answers their question, answer now. Don't read another page for the same fact.)"
                if page and not page.startswith("read_page:")
                else ""
            )
        if name == "recall_screen":
            hits = self.spatial.recall(str(args["query"]), 5)
            if not hits:
                return f"recall_screen({args['query']!r}): nothing in spatial memory."
            lines = [h.describe() for h in hits]
            if ctx:
                for h in hits:
                    now = next(
                        (e for e in ctx.elements if e.name == h.name and e.role == h.role), None
                    )
                    if now and ctx.window.app.lower() == h.app.lower():
                        lines.append(f'"{h.name}" is on screen right now as [{now.id}].')
                        break
            return "recall_screen results:\n" + "\n".join(lines)
        if name == "look_closer":
            rect = resolve_rect(ctx, args.get("element_id"), args.get("box"))
            crop = ctx.crop(rect, pad=8) if ctx and rect else None
            if crop is None:
                return "look_closer: nothing to zoom into."
            scale = min(3.0, 1100 / max(crop.width, 1))
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="JPEG", quality=85)
            self._images.append(buf.getvalue())
            return f"Zoomed image of {rect} added below the screenshot."
        if name == "remember":
            from pyxi.memory_guard import SensitiveMemory

            try:
                self.memory.remember("agent:pyxi", str(args["key"]), str(args["content"]))
            except SensitiveMemory as exc:
                return f"remember: {exc} Tell them, briefly."
            return None
        if name == "recall_memories":
            q = str(args.get("query") or "").strip()
            found = (
                self.memory.search("agent:pyxi", q, 20) if q else self.memory.all("agent:pyxi", 30)
            )
            return (
                "You remember:\n" + "\n".join(f"- {m.key}: {m.content}" for m in found)
                if found
                else "You don't remember anything about them yet."
            )
        if name == "forget":
            if args.get("everything"):
                n = self.memory.forget("agent:pyxi")
            elif args.get("key"):
                n = self.memory.forget("agent:pyxi", str(args["key"]))
            else:
                return "forget: say which key, or everything=true."
            return f"Forgot {n} memor{'y' if n == 1 else 'ies'}."
        if (connector_tool := self.connectors.find(name)) is not None:
            result = await connector_tool.handler(args)
            if isinstance(result, Outgoing):
                reads = list(getattr(self, "_turn_reads", []))
                if origin := safety.provenance(
                    result.recipients, result.preview, getattr(self, "_user_text", ""), reads
                ):
                    result = dataclasses.replace(result, preview=f"{origin}\n\n{result.preview}")
                self._spawn(self._approve(result))
                return f"Waiting for the user to approve: {result.action}. Tell them it's ready to check; don't say it's done."
            return result
        if name == "schedule_routine":
            days = [str(d) for d in args.get("days", []) or []] or None
            alarm = bool(args.get("alarm"))
            once = None
            if alarm and not days:
                # "Set an alarm for 7 tomorrow" said to the model: once, at the next 7:00, not
                # every day. A repeating alarm names its days ("every day", "weekdays").
                from pyxi.routines import parse_time

                hhmm = parse_time(str(args.get("time") or "")) or "07:00"
                now = datetime.now()
                at = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0, microsecond=0)
                once = (at if at > now else at + timedelta(days=1)).date().isoformat()
            r = self.routines.add(
                str(args["task"]),
                days=days,
                time=args.get("time"),
                every_minutes=None if alarm else args.get("every_minutes"),
                on=once,
                kind="alarm" if alarm else "turn",
                # A label that only says it's an alarm ("Alarm: tomorrow, Monday 9 November")
                # rings as "Time to get up."; a real one ("Gym") is said.
                message=(None if re.match(r"(?i)\W*(?:my\s+)?alarm\b", str(args["task"])) else str(args["task"]))
                if alarm
                else None,
            )
            await self.hub.emit("routines", routines=self.routines.public())
            when = datetime.fromtimestamp(r.next_run).strftime("%a %H:%M")
            return f"Routine saved ({r.describe()}); first run {when}. It runs while Pyxi is open."
        if name == "list_routines":
            items = self.routines.public()
            if not items:
                return "No routines yet."
            return "Routines:\n" + "\n".join(
                f"- {i['id']}: {i['when']}: {i['task']}" for i in items
            )
        if name == "cancel_routine":
            gone = self.routines.remove(str(args["routine"]))
            await self.hub.emit("routines", routines=self.routines.public())
            return (
                f"Deleted the routine '{gone.task[:60]}'."
                if gone
                else "cancel_routine: no routine like that."
            )
        if name == "errand":
            task = str(args.get("task", "")).strip()
            if not task:
                return "errand: say what to do."
            if not self.connectors.tools():
                return "errand: no accounts are connected yet; tell the user to connect one in Settings."
            if self.turns.spent_today() >= self.settings.budget.daily_cost_usd:
                return "errand: today's budget is used up, so I can't start it. Tell the user."
            words = "" if getattr(self, "_synthetic", False) else self._user_text
            reads = list(getattr(self, "_turn_reads", []))
            self._spawn(self._run_errand(task, words, **({"reads": reads} if reads else {})))
            return None
        if name in ("subscriptions", "returns"):
            if not self.connectors.google.connected():
                return f"{name}: Gmail isn't connected yet; tell the user to connect Google in Settings."
            if name == "returns":
                self._spawn(self._watch_returns())
            elif args.get("action") == "cancel" and args.get("service"):
                self._spawn(self._cancel_subscription(str(args["service"])))
            else:
                self._spawn(self._watch_subscriptions())
            return None
        if name == "arrange_windows":
            return await self._arrange_windows(args)
        if name == "play_music":
            return await self._play_music(args)
        if name == "book":
            return await self._book(args)
        if name == "use_computer":
            return await self._use_computer(args)
        if name == "tidy_folder":
            return await self._tidy(args)
        if name == "look_again":
            # Something they asked for just opened (Maps, a site, an app): wait for it to load,
            # then look at it, so the answer comes from what's really on screen now.
            try:
                secs = max(0.5, min(float(args.get("seconds") or 2.5), 8.0))
            except (TypeError, ValueError):
                secs = 2.5
            await asyncio.sleep(secs)
            if args.get("screen") and windows.available():
                # "What's on my left screen?": its top window, read like the one under the mouse.
                where = windows.screen_named(str(args["screen"]))
                if where is None:
                    return f"look_again: there's no {args['screen']!r} screen."
                top = next(
                    (
                        h
                        for h, t, _ in windows.open_windows()
                        if not windows._NOT_CONTENT.match(t)
                        and (r := windows.rect_of(h))
                        and r[2] > 80
                        and windows.screen_of(r, [where]) is not None
                    ),
                    None,
                )
                if top is None:
                    return f"look_again: nothing is open on the {where['name']} screen (just the desktop)."
                ctx = await self._capture(hwnd=top)
                self._shown_screen, self._screens_text = where["name"], None  # the note says so now
            else:
                ctx = await self._capture(follow_mouse=False)  # what just opened is in front
            if ctx.excluded:
                return f"look_again: that's a private app ({ctx.excluded}), so I can't look."
            self._ctx = ctx
            if image := ctx.model_image(self.settings.screen.model_image_width):
                self._images = [image]
            return f"look_again: this is the screen now ({ctx.window.title[:80]!r})."
        if name == "quick_action":
            return await self._quick_action(str(args.get("action", "")), str(args.get("value", "")))
        if name == "paste_table":
            return await self._paste_table(args)
        if name == "start_walkthrough":
            await self.start_walkthrough(args.get("job_id"))
            return None
        if name == "get_activity":
            events = self.activity.recent(float(args.get("minutes") or 30))
            if not events:
                return "get_activity: no activity recorded yet (the Memory Palace builds up as the user works)."
            return "Recent activity (oldest first):\n" + "\n".join(
                e.describe() for e in events[-40:]
            )
        if name == "time_travel":
            return await self.time_travel(str(args.get("query", "")), str(args.get("when") or ""))
        if name == "causal_replay":
            ids = [int(i) for i in args.get("event_ids", [])][:10]
            events = sorted(self.activity.get(ids), key=lambda e: e.ts)
            if not events:
                return "causal_replay: none of those event ids exist; call get_activity first."
            snaps = {
                e.snapshot_id: snap
                for e in events
                if e.snapshot_id and (snap := self.spatial.snapshot(e.snapshot_id))
            }
            scene = causal_scene(
                events,
                args.get("links") or [],
                title=str(args.get("title", "How you got here")),
                summary=str(args.get("summary", "")),
                what_ifs=args.get("what_ifs"),
                crops_dir=self.settings.crops_dir,
                snapshots=snaps,
            )
            await self.show_scene(scene)
            return f"Showing the causal chain of {len(events)} events."
        if name == "pin_note":
            e = ctx.element(int(args["element_id"])) if ctx else None
            if e is None:
                return "pin_note: that element isn't on screen."
            self.activity.add_pin(ctx.window.app, e.name, e.role, str(args["note"]))
            await self.emit_pins(ctx.window.app, ctx)
            return None
        if name == "create_agent":
            extra = {"voice": args["voice"]} if args.get("voice") else {}
            agent = self.registry.create(
                str(args["name"]), args["role"], str(args["persona"]), **extra
            )
            await self.hub.emit("agents", agents=[a.model_dump() for a in self.registry.all()])
            return f"Created {agent.name} ({agent.role})."
        if name in specialists.TOOLS:  # Atlas and the other specialists: companion/specialists.py
            return await specialists.handle(self, name, args)
        if name in FILE_TOOLS:  # Pixel, Ledger and file search: companion/filework.py
            return await filework.handle(self, name, args)
        if name in PAPER_TOOLS:  # forms, flight check-in, showing a bill: companion/paperwork.py
            return await paperwork.handle(self, name, args)
        if name in pricecheck.TOOLS:  # cheaper elsewhere: companion/pricecheck.py
            return await pricecheck.handle(self, name, args)
        # Phrased as a problem note, so the model hears it and tries a real tool.
        return f"{name}: there's no tool by that name. Use one of the tools you have."

    # ================================================================== speech
    def _speak(
        self, text: str, *, agent: str = "Pyxi", voice: str | None = None, record: bool = True
    ) -> None:
        """Queue speech behind anything already being said. ``record=False`` for background
        lines (a finished build, an error) that aren't part of the current turn."""
        if record and agent == "Pyxi":
            text = trim_chatter(text, said_before=bool(self._said))
            text = strip_filler(text)  # "There you go, I have done this."
            text = hide_secrets(text)
            text = drop_repeats(text, self._said)
            text = vary_ack(text, self._recent_acks)
        if not text.strip():
            return
        if record:
            self._said.append(split_markers(text)[0])
        previous, gen, ctx = self._speech, self._speech_gen, self._ctx

        async def run() -> None:
            if previous:
                await asyncio.gather(previous, return_exceptions=True)
            await self._stream_speech(text, agent=agent, voice=voice, gen=gen, ctx=ctx)

        self._speech = asyncio.create_task(run())

    async def _home_city(self) -> str:
        """Where they are, for "is it going to rain?": the calendar's time zone, else this PC's."""
        from pyxi.localzone import local_zone

        if self.connectors.google.connected():
            try:
                if city := weather.city_of(await self.connectors.google.time_zone()):
                    return city
            except Exception:
                pass
        return weather.city_of(local_zone())

    async def _weather_now(self, place: str, when: str) -> bool:
        """'What's the weather tomorrow?' answered straight from the forecast, no model and no
        web search. False when the place isn't known or found, so the model takes it."""
        place = place or await self._home_city()
        if not place:
            return False
        f = await weather.forecast(place)
        if f is None:
            return False
        self._calls.append(
            {
                "round": 1,
                "tool": "weather",
                "arguments": {"place": place, "when": when},
                "instant": True,
            }
        )
        self._speak(f.say(when))
        return True

    async def _instant(self, hit: instant.Instant) -> bool:
        """A simple request done at once, without the model (pyxi/companion/instant.py). False
        when it didn't work, so the model takes it from there."""
        note = await self._quick_action(hit.action, hit.value)
        self._calls.append(
            {
                "round": 1,
                "tool": "quick_action",
                "arguments": {"action": hit.action, "value": hit.value},
                "instant": True,
            }
        )
        if note:
            log.info("instant route didn't work (%s), asking the model", note)
            return False
        if hit.action != "media":  # music stopping or skipping says it all
            self._speak(hit.line or "On it.")
        return True

    async def say_line(
        self, text: str, *, agent: str = "Pyxi", voice: str | None = None, record: bool = True
    ) -> None:
        self._speak(text, agent=agent, voice=voice, record=record)
        if self._speech:
            await asyncio.gather(self._speech, return_exceptions=True)
        if not self._lock.locked():
            await self.set_state("idle")  # said outside a turn: don't stay "speaking" forever

    async def _stream_speech(
        self, text: str, *, agent: str, voice: str | None, gen: int, ctx: ScreenContext | None
    ) -> None:
        sentences: list[str] = []
        chunks = split_sentences(text)
        for chunk in chunks:  # a marker alone belongs with the words before it
            if sentences and not split_markers(chunk)[0]:
                sentences[-1] += " " + chunk
                continue
            # "...a failing test. [note:1] Then..." : a note marker trailing a sentence belongs
            # to that sentence, so the point appears while it is being said.
            lead = LEADING_NOTES.match(chunk)
            if sentences and lead:
                sentences[-1] += " " + lead.group(0).strip()
                chunk = chunk[lead.end() :]
            sentences.append(chunk)
        # Smoother speech: the first sentence goes alone (so Pyxi starts talking fast), then
        # sentences without drawing markers are voiced together, so the voice flows instead of
        # resetting at every full stop.
        merged: list[str] = []
        for sentence in sentences:
            joinable = (
                len(merged) >= 2  # never into the first sentence
                and not MARKER.search(sentence)
                and not MARKER.search(merged[-1])
                and len(merged[-1]) + len(sentence) < 260
            )
            if joinable:
                merged[-1] += " " + sentence
            else:
                merged.append(sentence)
        sentences = merged
        for i, sentence in enumerate(sentences):
            if gen != self._speech_gen:
                return
            clean, markers = split_markers(sentence)
            anns = [a.model_dump() for kind, eid in markers if (a := from_marker(ctx, kind, eid))]
            notes = [eid for kind, eid in markers if kind.lower() == "note"]
            if not clean and not anns and not notes:
                continue
            speech = await self.tts.synthesize(clean, voice)
            if gen != self._speech_gen:
                return
            if i == 0:
                await self.set_state("speaking")
            await self.hub.emit(
                "say",
                agent=agent,
                index=i,
                total=len(sentences),
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
                duration_s=round(speech.duration_s, 2),
                annotations=anns,
                notes=notes,
            )
            # Lines play one after another: remember when the last one will have finished.
            now = time.monotonic()
            self._talking_until = max(self._talking_until, now) + speech.duration_s

    # ================================================================== scenes & walkthroughs
    def _anchor_scene(self, scene: SceneSpec) -> None:
        if scene.anchor and scene.anchor.element_id is not None:
            rect = resolve_rect(self._ctx, scene.anchor.element_id, None)
            scene.anchor = SceneAnchor(
                element_id=scene.anchor.element_id, rect=list(rect) if rect else None
            )

    def _scene_preview(self, raw: str) -> SceneSpec | None:
        """An explain_in_3d scene from its streaming JSON once its nodes and edges are complete:
        one silent step until the real steps arrive (under its own id, so the full scene that
        replaces it is narrated from the top)."""
        data: dict[str, Any] = {
            "nodes": complete_items(raw, "nodes"),
            "edges": complete_items(raw, "edges"),
        }
        for key in ("title", "subtitle", "layout", "mode", "theme"):
            got = partial_string(raw, key)
            if got and got[1]:
                data[key] = got[0]
        if m := re.search(r'"anchor"\s*:\s*(\{[^{}]*\})', raw):
            try:
                data["anchor"] = json.loads(m.group(1))
            except ValueError:
                pass
        if not data["nodes"] or "title" not in data:
            return None
        data["steps"] = [{"title": data["title"], "narration": "", "focus": []}]
        try:
            scene = parse_scene(data)
        except ValidationError:
            return None
        scene.id += "_preview"
        self._anchor_scene(scene)
        return scene

    async def show_scene(self, scene: SceneSpec) -> None:
        self._visual_turn = True
        place = (
            None
            if scene.anchor
            else place_stage(self._ctx, self._placed.others("stage"), PLATE_SIZE)
        )
        self._placed.put("stage", place, 90.0)
        await self.hub.emit(
            "scene",
            scene=scene.model_dump(by_alias=True, mode="json"),
            place=list(place) if place else None,
        )
        self._spawn(self._narrate(scene))

    async def show_sketch(self, board: Sketchboard) -> None:
        """Put a self-drawing diagram on screen, in empty space, threaded to what it explains."""
        self._visual_turn = True
        ctx = self._ctx
        if board.anchor and ctx is not None:
            rect = target_rect(ctx, board.anchor.model_dump())
            board.anchor.rect = list(rect) if rect else None
        avoid = self._placed.others("stage")
        if board.anchor and board.anchor.rect:
            avoid = [*avoid, tuple(board.anchor.rect)]  # never cover the thing it explains
        # A diagram has to be readable: size counts for a lot more than covering a sidebar.
        place = place_stage(ctx, avoid, BOARD_SIZES, size_weight=2.0)
        self._placed.put("stage", place, 60.0)
        audio_key = f"{board.id}:{secrets.token_hex(3)}"
        await self.hub.emit(
            "sketch",
            board=board.model_dump(mode="json"),
            audio_key=audio_key,
            place=list(place) if place else None,
        )
        self._watch_window()
        self._spawn(self._narrate_beats(audio_key, [s.narration for s in board.steps]))

    async def show_object(
        self,
        model: ObjectScene,
        *,
        restart: bool,
        background: bool = False,
        keep_place: bool = False,
    ) -> None:
        """Put a 3D model on screen. Edits keep the id (the view keeps its camera); new beats get
        a new audio key so old narration can't play over the new explanation. ``keep_place``:
        the same stage as last time (a model still assembling), without re-reading the screen."""
        self._object = model
        if not background:  # a finished background build mustn't reshape the turn in progress
            self._visual_turn = True
        place = getattr(self, "_object_place", None) if keep_place else None
        if place is None:
            place = place_stage(self._ctx, self._placed.others("stage"))
            self._placed.put("stage", place, 90.0)
        self._object_place = place
        audio_key = (
            f"{model.id}:{secrets.token_hex(3)}"
            if restart
            else getattr(self, "_object_audio", model.id)
        )
        self._object_audio = audio_key
        await self.hub.emit(
            "object",
            scene=model.model_dump(mode="json"),
            audio_key=audio_key,
            restart=restart,
            place=list(place) if place else None,
        )
        if restart:
            self._spawn(self._narrate_beats(audio_key, [b.narration for b in model.beats]))

    async def _narrate_beats(self, key: str, lines: list[str]) -> None:
        for i, line in enumerate(lines):
            if not line:
                continue
            speech = await self.tts.synthesize(line)
            await self.hub.emit(
                "scene_audio",
                scene_id=key,
                step=i,
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
                duration_s=round(speech.duration_s, 2),
            )

    async def _narrate(self, scene: SceneSpec) -> None:
        for i, step in enumerate(scene.steps):
            if not step.narration:
                continue
            speech = await self.tts.synthesize(step.narration)
            await self.hub.emit(
                "scene_audio",
                scene_id=scene.id,
                step=i,
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
                duration_s=round(speech.duration_s, 2),
            )

    async def _scene_for_job(self, task: str, context: str, agent: AgentProfile) -> dict[str, Any]:
        explain = next(t for t in COMPANION_TOOLS if t.name == "explain_in_3d")
        response = await self.llm.complete(
            system=f"You are {agent.name}. {agent.persona} Build one excellent 3D scene with the explain_in_3d tool.",
            messages=[Message.user(f"Topic: {task}\n\nContext:\n{context}")],
            tools=[explain],
            max_tokens=6000,
        )
        self.turns.add_spend(f"job:{agent.id}", response.usage.cost_usd)
        call = next((c for c in response.tool_calls if c.name == "explain_in_3d"), None)
        if call is None:
            raise RuntimeError("No scene was produced.")
        scene = parse_scene(call.arguments)
        await self.show_scene(scene)
        return scene.model_dump(by_alias=True, mode="json")

    async def time_travel(self, query: str, when: str = "", snapshot_id: int | None = None) -> str:
        """Rebuild a past interface as a 3D hologram, with the surrounding moments on a scrubber."""
        if snapshot_id is None:
            window = parse_when(f"{when} {query}")
            topic = strip_when(query)
            hits = self.spatial.find_snapshots(
                topic, start=window[0] if window else None, end=window[1] if window else None
            )
            if not hits and window:
                hits = self.spatial.find_snapshots(
                    topic
                )  # right thing, wrong day: still better than nothing
            if not hits:
                return f"time_travel: nothing in the Memory Palace matches {query!r}."
            snapshot_id = hits[0]["id"]
        snap = self.spatial.snapshot(snapshot_id)
        if snap is None:
            return "time_travel: that moment has been forgotten."
        scene = snapshot_scene(snap, self.settings.crops_dir, highlight=query)
        timeline = self.spatial.neighbours(snapshot_id)
        await self.hub.emit(
            "palace",
            snapshot_id=snapshot_id,
            timeline=timeline,
            scene=scene.model_dump(by_alias=True, mode="json"),
        )
        self._spawn(self._narrate(scene))
        return f"Time-travelled to {snap['app']} ({snap['title'][:60]}) with {len(scene.nodes)} ghost elements."

    async def emit_pins(self, app: str, ctx: ScreenContext | None = None) -> None:
        """Re-place pinned notes on their elements if they're on screen now."""
        pins = self.activity.pins_for(app)
        if not pins:
            return
        ctx = ctx or await self.screen.capture(pixels=False)
        placed = []
        for pin in pins:
            e = next(
                (
                    x
                    for x in ctx.elements
                    if x.name == pin.element_name and x.role == pin.element_role
                ),
                None,
            )
            if e:
                placed.append({"id": pin.id, "note": pin.note, "color": pin.color, "rect": e.rect})
        await self.hub.emit("pins", app=app, pins=placed)

    async def start_walkthrough(self, job_id: str | None = None) -> None:
        candidates = [j for j in self.jobs.recent(20) if j.role == "browser" and j.run_id]
        job = self.jobs.jobs.get(job_id) if job_id else (candidates[-1] if candidates else None)
        if job is None or not job.run_id:
            await self.say_line("I don't have a finished browser job to walk you through yet.")
            return
        walk = build_walkthrough(self.otto_store, job.run_id)
        await self.hub.emit("walkthrough", walkthrough=walk.model_dump(mode="json"))
        for step in walk.steps:
            speech = await self.tts.synthesize(step.narration)
            await self.hub.emit(
                "walkthrough_audio",
                walkthrough_id=walk.id,
                step=step.index - 1,
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
            )

    async def _job_done(self, job: Job) -> None:
        self.turns.add_spend(f"job:{job.agent_id}", job.cost_usd)
        self.activity.log(
            "job", app=job.agent_name, detail=f"{job.agent_name} {job.status}: {job.task[:60]}"
        )
        if job.then and job.status == "done":
            return  # the next teammate takes it from here
        # Background work is quiet: no voice, just a notification with the result and the files.
        # (You asked Pyxi to go off and do it; it shouldn't start talking over you when it's done.)
        data = job.data if isinstance(job.data, dict) else {}
        if (note := paperwork.notice(job, data)) is not None:  # a check-in or a filled form
            await self.hub.emit(
                "job_ready",
                job_id=job.id,
                agent=job.agent_name,
                files=[],
                changes=[],
                notes=[],
                **note,
            )
            self.history = [
                *self.history[-11:],
                (
                    f"(job {job.id} finished: {job.summary[:600]})",
                    f"(notified the user: {note['title']}. {note['body']})",
                ),
            ]
            return
        fixed = data.get("fixed") or []
        files = [str(f) for f in (fixed or job.outputs)]
        if job.status == "done":
            for f in files:
                self.audit.add("wrote", f"Saved {Path(f).name}", agent=job.agent_name, detail=f)
        booked = job.kind == "booking" and job.status == "done" and bool(data.get("booked"))
        if booked:
            what = str(data.get("what") or "Your booking")
            when = str(data.get("when") or "").replace("T", " at ")
            where = str(data.get("where") or "")
            code = str(data.get("confirmation") or "")
            title = f"Booked: {what}"[:70]
            body = (
                " ".join(
                    x
                    for x in (
                        f"{when}." if when else "",
                        f"{where}." if where else "",
                        f"Confirmation {code}." if code else "",
                    )
                    if x
                )
                or (job.summary or "Done.")[:220]
            )
            actions = ["calendar"]
        elif job.status == "done" and fixed:
            names = ", ".join(Path(f).name for f in fixed)
            n = len(data.get("changes") or [])
            title = "Your document is fixed"
            body = f"{n} fix{'es' if n != 1 else ''} in {names}. The original is untouched."
            actions = ["open", "folder", "send_back"]
        elif job.status == "done":
            title = f"{job.agent_name} finished"
            body = " ".join(split_sentences(job.summary)[:2]) if job.summary else "Done."
            if job.role == "writer":
                title, body = "Your draft is ready", clip(job.summary or "", 220)
            actions = (["open"] if files else []) + (
                ["walkthrough"] if job.role == "browser" else []
            )
        else:
            title = f"{job.agent_name} couldn't finish"
            body = clip(job.summary or job.status, 220)
            actions = []
        if job.role in specialists.ROLES and data.get("title"):  # a specialist's own card
            title, body = str(data["title"])[:70], str(data.get("body") or body)[:300]
            actions = [str(a) for a in data.get("actions") or actions]
        await self.hub.emit(
            "job_ready",
            job_id=job.id,
            agent=job.agent_name,
            ok=job.status == "done",
            title=title,
            body=body,
            files=files,
            changes=(data.get("changes") or [])[:8],
            notes=(data.get("notes") or [])[:3],
            actions=actions,
            link=data.get("link") if job.role in specialists.ROLES else None,
            calendar=(
                f"Add this booking to my Google Calendar, with a reminder the day before: {title}. {body}"
                f" Address: {data.get('address') or data.get('where') or ''}"
                if booked
                else None
            ),
        )
        # The model still gets the full result (file paths included) for "send it back".
        self.history = [
            *self.history[-11:],
            (
                f"(job {job.id} finished: {job.summary[:600]})",
                f"(notified the user: {title}. {body})",
            ),
        ]
        if job.role in specialists.ROLES:  # reminders, check-ins, the next monthly summary
            await specialists.after_job(self, job)

    # ================================================================== misc
    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            self._bg.discard(t)
            if not t.cancelled() and (exc := t.exception()):
                log.error("background task failed", exc_info=exc)

        task.add_done_callback(done)

    def _reply_language(self) -> str:
        studio = self.studio
        if not studio:
            return ""
        code = (
            studio.prefs.language if studio.prefs.language != "auto" else studio.detected_language
        )
        return "" if code == "en" else LANGUAGES.get(code, code)

    @property
    def studio(self) -> VoiceStudio | None:
        primary = getattr(self.tts, "primary", self.tts)
        return primary if isinstance(primary, VoiceStudio) else None

    def public_state(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "agents": [a.model_dump() for a in self.registry.all()],
            "jobs": [j.model_dump(mode="json") for j in self.jobs.recent(20)],
            "spent_today": round(self.turns.spent_today(), 4),
            "daily_budget": self.settings.budget.daily_cost_usd,
            "model": self.settings.brain_model,
            "local_mode": self.settings.local_mode,
            "tts": self.studio.engine_name if self.studio else self.tts.name,
            "voice": self.studio.public() if self.studio else None,
            "privacy_paused": self.screen.paused,
            "ambient_memory": self.settings.screen.ambient_memory,
            "keep_screenshots": self.settings.screen.keep_screenshots,
        }


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


_FILLER = re.compile(
    r"^(sure|okay|ok|alright|great question|good question|there you go|here you go|"
    r"let me show you|let me draw (that|it)|here'?s how|watch this)\b[^.!?]*[.!?]?\s*$",
    re.IGNORECASE,
)


# Tools whose notes are advice for next time, not a problem worth another round.
# "Open YouTube", "go to my bank": opening it is the whole request, nothing to answer after.
_JUST_OPEN = re.compile(
    r"^\W*(hey,? )?(pyxi,? )?(can you |could you |please )?(open|go to|launch|start|bring up|pull up)\b"
    r"(?!.*\b(and|then|find|which|what|best|show me|tell me)\b)",
    re.I,
)
# Tools that start work which finishes later (the notification says how it went).
# "Done", "that's the first one", "now you": what people say when they've shown Ditto one.
_DONE_SHOWING = re.compile(
    r"(?i)^\W*(?:(?:ok|okay|right|so|alright|yes|yeah)[,.]?\s+)*(?:i'?m\s+)?(?:done|finished|that'?s (?:the first one|one|it|done)|"
    r"now you(?: do (?:it|the rest))?|your turn|there you go|that one'?s done)\b[^?]{0,60}$"
)


_MULTI_SCREEN_NOTE = (
    "The screenshot is of the screen marked above; the list is every screen. Never say you "
    "can only see one screen, and never mention 'the list' or 'the shot': just say what's there. "
    "To see another screen properly, look_again with screen set to it (once: then answer from "
    "the new screenshot). To move or lay out windows on a particular screen, arrange_windows "
    "takes screen ('left', 'middle', 'right'). To play or pause what's on a particular screen "
    "('play the song on my middle screen'), quick_action screen_playpause with value 'middle': "
    "never the mouse for that."
)


# Words from a turn they talked over are carried into their next turn only this soon after.
_CARRY_S = 90.0
_BACKGROUND_TOOLS = {
    "edit_document",
    "convert_files",
    "delegate",
    "errand",
    "book",
    "use_computer",
    "tidy_folder",
    "build_3d_model",
    "explain_in_3d",
}
# Tools that can say no (a file that isn't there, an account that isn't connected, a card they
# decline): a line said with one waits for its answer, and is dropped if the answer is no.
CAN_REFUSE = {"edit_document", "convert_files", "delegate", "book", "errand", "use_computer"}
# "Find coffee near me": opening Maps isn't the answer, so Pyxi looks at what opened and
# answers from it. "I said open Gmail, not GL" asks nothing: one model call, not two.
_WANTS_ANSWER = re.compile(
    r"\b(find|best|which|what|where|who|when|how|show me|tell me|near me|nearby|cheapest|"
    r"compare|reviews?|open now|rating|price|look up|search|check)\b",
    re.I,
)
# Names models sometimes invent for a real tool (from its options), and what they mean.
TOOL_ALIASES: dict[str, tuple[str, dict[str, Any]]] = {
    "explain_on_picture": ("explain_on_screen", {"style": "on_picture"}),
    "explain_picture": ("explain_on_screen", {"style": "on_picture"}),
}
QUIET_NOTES = {"say", "explain_on_screen", "point", "circle", "highlight", "arrow", "remember"}
THREE_D_TOOLS = {"build_3d_model", "edit_3d_model", "explain_in_3d", "explode_view"}
_ASKS_3D = re.compile(
    r"\b(3d|3-d|three[- ]?d|in three dimensions|(build|make|show) (me )?(a |the )?model|render|build (it|this|me|one|a|an|the)|"
    r"cut (it |this )?open|cutaway|cross[- ]section|inside of|show me (the )?inside|what'?s inside|"
    r"take (it )?apart|exploded?|assembl\w*|how (do|to) (i )?(attach|connect|install|fit|plug))",
    re.IGNORECASE,
)
_EDITS_3D = re.compile(
    r"\b(spin|rotate|turn it|zoom|casing|inside|bigger|smaller|again|replay|that part|the model)\b",
    re.IGNORECASE,
)


_COUNTER = re.compile(r"^\s*[\(\[]\d+\+?[\)\]]\s*|^\s*[•●]\s*|\s*[\(\[]\d+\+?[\)\]]\s*$")


def friendly_error(exc: BaseException) -> str:
    """'Google said 400: {"error": ...}' becomes 'Google said the request was invalid.'"""
    text = str(exc)
    # The Pyxi beta server says why in plain words ("You've used today's Pyxi beta allowance...");
    # show exactly that, without the "OpenRouter API error: Error code: 429 - " in front.
    beta = re.search(
        r"(You've used today's Pyxi beta allowance[^\"'}]*|The Pyxi beta is at its limit[^\"'}]*|Sign in to the Pyxi beta first[^\"'}]*)",
        text,
    )
    if beta:
        return beta.group(1).strip()
    if "is switched off in Pyxi's Google Cloud project" in text:
        return text[:260]  # already says what to do, with the link
    m = re.search(r'"message":\s*"([^"]{3,140})"', text)
    detail = m.group(1) if m else ""
    service = "Google" if "Google" in text else "The service"
    if re.search(r"\b40[13]\b", text):
        return f"{service} says I'm not allowed to do that; reconnecting the account may fix it."
    if re.search(r"\b400\b", text):
        return f"{service} rejected the request{f': {detail}' if detail else ''}."
    if re.search(r"\b5\d\d\b|timed? ?out|Timeout", text):
        return f"{service} didn't answer in time. Want me to try again?"
    return (detail or re.sub(r"[{}\[\]\"]", "", text))[:140]


_FILLER_LEAD = re.compile(
    r"^\s*(there you go|here you go|sure thing|sure|okay so|alright)\b[,.!]?\s*"
    r"(i(?:'ve| have) done (this|that|it)[.!]?\s*)?",
    re.IGNORECASE,
)


_PROMISE = re.compile(
    r"\b(i'?m|i am|i'?ll|i will|let me|give me a (sec|second|moment))\b[^.!?]{0,60}"
    r"\b(search|searching|look(ing)? (it |that )?up|check(ing)?|dig(ging)?|find(ing)? out|research)"
    # ...or with no "I": "Checking the news on that one.", "Looking it up now."
    r"|^\W*(checking (the|on|that|it|now)|searching|looking (it |that )?(up|into)|digging|"
    r"pulling (it |that )?up|researching)\b",
    re.IGNORECASE,
)


def clip(text: str, limit: int) -> str:
    """At most ``limit`` characters, ending on a whole sentence when one fits, else a whole
    word with an ellipsis: never "the deadline for written n"."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if end >= limit // 2:
        return head[: end + 1]
    return head[: head.rfind(" ")].rstrip(",;:") + "…" if " " in head else head


def drop_false_promises(text: str) -> tuple[str, bool]:
    """Sentences like "I'm searching for it now" in a reply that makes no tool call are false:
    nothing is going to happen. Remove them; say whether any were there."""
    kept, promised = [], False
    for s in split_sentences(text):
        if _PROMISE.search(s):
            promised = True
        else:
            kept.append(s)
    return " ".join(kept).strip(), promised


def strip_filler(text: str) -> str:
    """Drop stock openers ("There you go, I have done this.") when there's a real sentence after."""
    rest = _FILLER_LEAD.sub("", text, count=1)
    return rest[0].upper() + rest[1:] if rest.strip() and rest != text else text


# "On it." and its kin: said once a turn at most.
_ACK = re.compile(r"^\W*(on it|got it|sure thing|on my way)\W*$", re.IGNORECASE)
# Short running commentary on the work ("I'll look at what's on your desktop first.", "Looking up
# your Skyward booking."). Not "I'll check you in 24 hours before...": that's the result.
_NARRATION = re.compile(
    r"^\W*(?:(?:okay|ok|alright),?\s+)?"
    r"(?:(?:i'?ll|i will|let me|i'?m going to)\s+(?:first\s+)?(?:have a\s+|take a\s+)?"
    r"(?:look|check|scan|search|go through|dig|see|pull|grab|read|find)"
    r"(?!\s+(?:you|them|him|her|us)\s+in\b)"
    r"|(?:looking|checking|searching|scanning|digging|going through|pulling)"
    r"\s+(?:up|at|for|through|into|on|your|the)\b)"
    r"[^,;:]{0,70}(?:,\s*then\b[^;:]{0,60})?$",
    re.IGNORECASE,
)


# A bare acknowledgement, varied so it isn't "On it" every time. Plain words a person says, not
# stock phrases ("Roger that", "Aye aye" sounded like a call centre). Pyxi's prompt asks for a
# line that names the job; these only cover the model's bare "On it.".
ACKS = [
    "On it",
    "Got it",
    "Okay",
    "Will do",
]
# The model's own acknowledgement at the start of a line ("On it.", "On it, I'll let you know.").
# English only: a reply in another language keeps its own words.
_ACK_LEAD = re.compile(
    r"^(\W*)(on it|got it|sure thing|will do|roger that|you got it|copy that|on my way)(?=[\s,.!]|$)",
    re.IGNORECASE,
)


_TOOL_TAG = re.compile(r"\[(?:say|[a-z]+_[a-z_]+)\]\s*")
_WORD = re.compile(r"[a-z0-9']+")


# Words that carry no news: a sentence made only of these plus words already said says nothing new.
_PLAIN = set(
    """a an the and or but so then now just also too very really okay ok right yes no not it its it's
    this that these those there here is are was were be been being am i i'm i'll i've me my you your
    you're we our us they them their he she him her his to of in on at for with from by as up down
    out off over into onto about all any some one more most can could will would should do does did
    done have has had get got go going let let's make made what which who when where how why if
    already still again sure guess guessing think mean meant way bit little quick fresh
    new open opening opened up coming now ready here""".split()
)


_MONTHS = {
    "jan": "january", "feb": "february", "mar": "march", "apr": "april", "jun": "june",
    "jul": "july", "aug": "august", "sep": "september", "sept": "september", "oct": "october",
    "nov": "november", "dec": "december",
}
_ORDINAL = re.compile(r"^(\d+)(?:st|nd|rd|th)$")


def _norm(word: str) -> str:
    """'Nov' and 'November', '28th' and '28', "Altman's" and 'Altman' are the same word."""
    if word.endswith("'s"):
        word = word[:-2]
    if m := _ORDINAL.match(word):
        return m.group(1)
    return _MONTHS.get(word, word)


def _content(sentence: str) -> set[str]:
    words = (_norm(w) for w in _WORD.findall(split_markers(sentence)[0].lower()))
    return {w for w in words if w and w not in _PLAIN}


def drop_repeats(text: str, earlier: list[str]) -> str:
    """Say each thing once. A model sometimes writes its line in the reply and again in `say`,
    reworded ("I'll fill the blank sheet..." then "I'll fill the open sheet..."), says it again in
    the next round ("New doc, opening a blank one." then "Got you, opening a new blank doc."), or
    leaks a tool's name ("[say] Ledger can't..."). A sentence is dropped when it mostly repeats one
    sentence already said, or when everything it says (its words that carry meaning) was already
    said this turn. A short line goes too when it is one already said word for word ("Opening
    Settings." twice), or the start line again in other words ("Putting the 5 by 5 table in
    now." then "The table's going in now."); what came of it ("The table is in.") always stays."""
    text = _TOOL_TAG.sub("", text)
    seen = [set(_WORD.findall(s.lower())) for e in earlier for s in split_sentences(e)]
    said_words: set[str] = set().union(*(_content(s) for e in earlier for s in split_sentences(e)))
    kept = []
    for sentence in split_sentences(text):
        words = set(_WORD.findall(split_markers(sentence)[0].lower()))
        content = _content(sentence)
        again = (
            len(words) >= 4 and any(len(words & w) / len(words | w) >= 0.6 for w in seen if len(w) >= 4)
        ) or (
            said_words
            and len(content) >= 2
            # all of it said already, or (in a longer sentence) three quarters of it
            and len(content - said_words) <= (len(content) // 4 if len(content) >= 4 else 0)
        ) or (
            # still only "it's under way", about the one thing already named
            len(content) == 1
            and content <= said_words
            and bool(_UNDERWAY.search(sentence))
            and not _SETTLED.search(sentence)
        ) or (bool(words) and words in seen)  # the same line, word for word, however short
        if again:
            marks = " ".join(m.group(0) for m in MARKER.finditer(sentence))
            if marks:
                kept.append(marks)  # the card point or pointer still appears; only the words go
            continue
        kept.append(sentence)
        seen.append(words)
        said_words |= content
    return " ".join(kept)


# A line that only says the work is under way: "Putting it in now.", "The table's going in.",
# "Ledger is drawing the pivot.", "A new doc, coming up.", "I'll add it."
_UNDERWAY = re.compile(
    r"^\W*[a-z]{2,}ing\b"
    r"|(?:\b(?:is|are|am)|'s|'re|'m)\s+(?:(?:now|just|already|still)\s+)?"
    r"(?!(?:no|some|any|every)thing\b)[a-z]{2,}ing\b"
    r"|\b(?:coming (?:right )?up|on (?:its|the|their) way|i'?ll|i will|let me)\b",
    re.IGNORECASE,
)
# ...unless it reports how things turned out, which is news.
_SETTLED = re.compile(
    r"\b(?:done|finished|ready|not|no|never|cannot|failed|sorry|but|instead|because|since)\b|n't\b",
    re.IGNORECASE,
)


# Other ways a line says a job has started: "...now.", "back in a minute", "Scout's on the flights."
_STARTED = re.compile(
    r"\b(?:now|right away|in a (?:sec|second|moment|minute)|back (?:in|with|soon)|off to|on it)\b"
    r"|(?:\bis|'s) on (?:it|the|your|that|those)\b",
    re.IGNORECASE,
)


def claims_work(text: str) -> bool:
    """Does this line say a job has started or is about to ("Putting the table in now.",
    "Scout's on the flights.", "A new doc, coming up.")? Said with a tool that can refuse, such
    a line waits for the tool's answer. A plain answer ("Real. Only the B version hovers.")
    does not, and starts being spoken as it arrives."""
    return any(_UNDERWAY.search(s) or _STARTED.search(s) for s in split_sentences(text))


def _verb_forms(verbs: str, *, doing_only: bool = False) -> str:
    """'lower set change' as a regex: lower, lowers, lowering, set, sets, setting, change..."""
    forms = []
    for v in verbs.split():
        stem = v[:-1] if v.endswith("e") else v
        doing = f"{stem}{stem[-1]}?ing"
        forms.append(doing if doing_only else f"{v}s?|{doing}")
    return "|".join(forms)


# Getting somewhere, which opening an app or a page really does...
_GO_VERBS = "open launch start pull bring head go navigate switch"
# ...and doing something there, which it does not.
_DO_VERBS = (
    "set change turn lower raise dim brighten increase decrease reduce mute unmute put add "
    "insert create make build draft write send email reply book schedule cancel delete remove "
    "move rename copy paste type click press scroll close save download upload convert merge "
    "split fill play pause install update fix run arrange snap drop select enable disable "
    "toggle adjust tidy clean organize organise resize crop rotate share post format print "
    "sign attach forward archive unsubscribe order"
)
# "I'll lower the brightness.", "Let me add that row.", "Lowering the brightness now."
_WILL_DO = re.compile(
    r"^\W*(?:(?:okay|ok|alright|right|sure|got it|on it)[,.!]?\s+)?(?:"
    r"(?:i'?ll|i will|i'?m going to|i am going to|let me|i'?m|i am)\s+"
    r"(?:(?:just|now|quickly|go ahead and|going ahead and)\s+)?"
    # ways of talking, not jobs: "I'll put it this way", "let me start with the catch"
    r"(?!put it\b|make (?:sure|it (?:quick|short|simple|easy|clear))\b|go(?:ing)? (?:with|out on)\b|"
    r"start (?:with|by|off)\b|add (?:that|one thing)\b|save you\b|fill you in\b|run you\b|"
    r"drop the\b|play along\b|turn to\b)"
    rf"(?:{_verb_forms(_GO_VERBS + ' ' + _DO_VERBS)})\b"
    r"|(?!putting it\b|making (?:sure|it)\b|turning to\b|running (?:the|through)\b|playing it\b)"
    rf"(?:{_verb_forms('open launch pull bring head navigate switch ' + _DO_VERBS, doing_only=True)})\b)",
    re.IGNORECASE,
)
# "...to lower the brightness", "...and turning it down": more than getting there.
_THEN_DO = re.compile(
    r"\b(?:to|and|then|so i can)\s+(?:(?:i'?ll|then|just)\s+)?"
    rf"(?:{_verb_forms(_DO_VERBS)})\b",
    re.IGNORECASE,
)
_ASKED_TO_DO = re.compile(rf"\b(?:{_verb_forms(_GO_VERBS + ' ' + _DO_VERBS)})\b", re.IGNORECASE)
# "How do I change it?", "What's going on?": a question to answer, not a job to do.
_ASKS_A_QUESTION = re.compile(
    r"^\W*(?:hey,? )?(?:pyxi,? )?(?:how|what|why|who|when|where|which|is|are|was|does|do|did|should)\b",
    re.IGNORECASE,
)
# An offer, a question or a "can't" is not a promise.
_NO_PROMISE = re.compile(
    r"\?\s*$|\b(?:if you|want me to|shall i|should i|would you like|do you want|once you|when "
    r"you|after you|you can|you could|you'll need|you need to|cannot|unable|not able|say the "
    r"word|let me know|tell me)\b|n't\b",
    re.IGNORECASE,
)


def undone_promise(
    said: list[str], calls: list[dict[str, Any]], user_text: str
) -> tuple[str, bool] | None:
    """The line in which Pyxi said it would do something that no tool call this turn carried
    out, and whether something was at least opened. Two shapes only, to stay sure of it:
    nothing was done at all (the request was for an action, and the line says "I'll..." or
    "Lowering..."), or the only thing done was opening an app or a page while the line promised
    more than getting there ("Heading to Display to lower the brightness."). Any other tool
    call (a teammate, an errand, a drawing, the mouse) counts as carrying it out."""
    did = [c for c in calls if c.get("tool") != "say" and c.get("tool") not in INFO_TOOLS]
    opened = bool(did)
    if any(
        c.get("tool") != "quick_action"
        or (c.get("arguments") or {}).get("action") not in ("open_url", "open_app")
        for c in did
    ):
        return None
    if opened and _JUST_OPEN.match(user_text):
        return None  # opening it was the whole request
    if not opened and (not _ASKED_TO_DO.search(user_text) or _ASKS_A_QUESTION.match(user_text)):
        return None  # a chat, not a job: "I'll be honest" promises nothing
    for line in (s for text in said for s in split_sentences(text)):
        if _NO_PROMISE.search(line):
            continue
        if (_THEN_DO if opened else _WILL_DO).search(line):
            return line, opened
    return None


def said_before(text: str, earlier: list[str]) -> bool:
    """Is all of this `say` something already said this turn, in the same or other words? Then
    the whole call goes. A line with a pointer or note marker never counts: its mark still draws."""
    return bool(earlier and text.strip()) and not drop_repeats(strip_filler(text), earlier).strip()


def vary_ack(text: str, recent: list[str], pick: Any = random.choice) -> str:
    """Swap a stock acknowledgement at the start of what Pyxi says for a fresh one (remembered in
    ``recent``, so the next few differ)."""
    m = _ACK_LEAD.match(text)
    if not m:
        return text
    used = {r.lower() for r in recent}
    ack = pick([a for a in ACKS if a.lower() not in used] or ACKS)
    recent.append(ack)
    del recent[:-3]
    return m.group(1) + ack + text[m.end() :]


def trim_chatter(text: str, said_before: bool) -> str:
    """Pyxi says one short line while it works, not a running commentary: a second "On it" in a
    turn goes, and so does commentary once something else has been or is being said. Sentences
    with markers (a note or a pointer) always stay."""
    sentences = split_sentences(text)
    kept: list[str] = []
    for i, s in enumerate(sentences):
        others = said_before or kept or i < len(sentences) - 1
        if not MARKER.search(s) and (
            (_ACK.match(s) and said_before) or (_NARRATION.match(s) and others)
        ):
            continue
        kept.append(s)
    return " ".join(kept) if len(kept) < len(sentences) else text


def _same_view(a: Image.Image, b: Image.Image) -> bool:
    """The window looks the same as before (nothing their click did)."""
    from pyxi.companion.operator import _same

    size = (480, 270)
    return _same(a.convert("L").resize(size), b.convert("L").resize(size))


def ctypes_foreground() -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return 0


def same_page(a: str | None, b: str | None) -> bool:
    """Is this still the page Pyxi drew on? Tab titles flicker with notification counters
    ("Facebook" -> "(1) Facebook", "Inbox (42,575)"), which isn't the user going anywhere."""

    def norm(t: str | None) -> str:
        t = _COUNTER.sub("", t or "")
        t = re.sub(r"\(\d[\d,]*\)", "", t)  # counts inside the title: "Inbox (42,575) - Gmail"
        return re.sub(r"\s+", " ", t).strip().lower()

    return norm(a) == norm(b)


def make_chat_llm(settings: PyxiSettings) -> LLMProvider:
    """The conversation model: streamed and prompt-cached on OpenRouter, else Otto's client."""
    from otto.config import infer_provider

    if not settings.local_mode and infer_provider(settings.model) == "openrouter":
        return StreamingChat(settings.model)  # type: ignore[return-value]
    return make_llm(settings)


def wants_3d(user_text: str, *, has_model: bool = False) -> bool:
    """Did they ask for something 3D? (Or, with a model on screen, ask to change it?)"""
    return bool(_ASKS_3D.search(user_text)) or (has_model and bool(_EDITS_3D.search(user_text)))


def visual_opener(text: str, limit: int = 200) -> str:
    """What Pyxi says before a drawing or model takes over: the verdict and a hook (two short
    sentences at most), minus empty openers like "There you go." that say nothing."""
    sentences = [
        s for s in split_sentences(text) if not (_FILLER.match(s.strip()) and len(s.strip()) < 40)
    ]
    out = ""
    for s in sentences[:2]:
        if out and len(out) + 1 + len(s) > limit:
            break
        out = f"{out} {s}".strip()
    return out or (split_sentences(text) or [text])[0]
