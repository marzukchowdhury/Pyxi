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
import io
import json
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from otto.llm.base import ImagePart, LLMProvider, Message, TextPart
from otto.storage import MemoryStore, TrajectoryStore
from otto.types import ToolCall, Usage
from PIL import Image
from pydantic import ValidationError

from pyxi import booking, hands, media, tidy, web, windows
from pyxi.agents import AgentProfile, AgentRegistry, Job, JobManager
from pyxi.agents.jobs import Handoff
from pyxi.companion import prompts
from pyxi.companion.actions import app_target, open_app, safe_url
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
from pyxi.companion.tools import BUILD_BRIEF, COMPANION_TOOLS, INFO_TOOLS
from pyxi.companion.turns import TurnStore
from pyxi.config import PyxiSettings
from pyxi.connectors import Connectors, Outgoing
from pyxi.hub import Hub
from pyxi.llm import make_llm
from pyxi.memory import SpatialMemory
from pyxi.palace import ActivityLog, causal_scene, parse_when, snapshot_scene, strip_when
from pyxi.partial_json import complete_items, first_chunk, partial_string, picture_head
from pyxi.routines import RoutineStore
from pyxi.scenes import SceneSpec, build_exploded_scene, get_scene, parse_scene
from pyxi.scenes.builder import build_model
from pyxi.scenes.object_gallery import OBJECT_GALLERY
from pyxi.scenes.objects import ObjectEdit, ObjectScene, apply_edit, object_summary
from pyxi.scenes.sketch import Sketchboard, steps_from_speech
from pyxi.scenes.spec import SceneAnchor
from pyxi.screen import ScreenContext, ScreenReader
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
        db = settings.db_path
        self.registry = AgentRegistry(db)
        thumbs = settings.palace_dir if settings.screen.keep_screenshots else None
        self.spatial = SpatialMemory(
            db, retention_hours=settings.screen.retention_hours, thumbs_dir=thumbs
        )
        self.activity = ActivityLog(db)
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
        self._placed = Placed()  # what's on screen now (cards, stages), so new things keep clear
        self.overlay_in_capture = (
            False  # the desktop app says whether screen shares see the overlay
        )
        self._object: ObjectScene | None = None  # the 3D model on screen, so follow-ups can edit it
        self._guide: Guide | None = None  # a click Pyxi is waiting for
        self._operator: Operator | None = None  # Pyxi using the mouse and keyboard
        self.connectors = Connectors(settings.data_dir, Path.home() / "Documents" / "Pyxi")
        self.routines = RoutineStore(settings.data_dir / "routines.json")

    # ================================================================== entry points
    async def set_state(self, state: State) -> None:
        self.state = state
        await self.hub.emit("state", state=state)

    def begin_listening(self) -> None:
        """Push-to-talk pressed: capture the screen *now*, while the user starts speaking."""
        self.interrupt()
        self._prefetch = asyncio.create_task(self._capture())
        self._spawn(self.set_state("listening"))

    def look_ahead(self) -> None:
        """They started typing to Pyxi: capture the screen now, so the answer starts sooner."""
        if self._prefetch is None and not self._lock.locked():
            self._prefetch = asyncio.create_task(self._capture())

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
        await self.hub.emit("transcript", text=text, seconds=round(took, 2))
        if not text.strip():
            self._prefetch = None  # the next question looks at the screen afresh
            await self.say_line("Sorry, I didn't catch that.")
            await self.set_state("idle")
            return
        await self.turn(text)

    async def _approve(self, out: Outgoing) -> None:
        """Show exactly what would go out; act only on Allow."""
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
                f"tidy_folder: {listing}\nNow call tidy_folder again with groups (and say one short "
                "line about what you see, e.g. the funniest thing in there)."
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
        moved, failed, _ = await asyncio.to_thread(tidy.apply, plan, log_dir)
        folders = len({into for _, into in plan.moves})
        tail = f" {len(failed)} were open somewhere, so I left those." if failed else ""
        self.history = [
            *self.history[-11:],
            (f"(tidied {plan.folder})", f"(moved {moved} files into {folders} folders)"),
        ]
        await self.say_line(
            f"Done. {moved} files are in {folders} folders now.{tail} Say undo if you want it back."
        )

    def _hands_always(self) -> bool:
        try:
            return bool(json.loads((self.settings.data_dir / "hands.json").read_text())["always"])
        except (OSError, ValueError, KeyError, TypeError):
            return False

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
        self._spawn(self._drive(task, window, bool(args.get("come_back"))))
        return None

    async def _drive(self, task: str, window: int | None, come_back: bool = False) -> None:
        if not self._hands_always():
            ok = await self.hub.request(
                "confirm",
                timeout=300,
                job_id="",
                agent="Pyxi",
                action=f"Use your mouse and keyboard to: {task}",
                reason="Move the mouse or press Esc to stop me. Anything that sends, buys, posts "
                "or deletes asks you again.",
                always="Always allow",
            )
            if ok == "always":
                (self.settings.data_dir / "hands.json").write_text(json.dumps({"always": True}))
            elif not ok:
                await self.say_line("Okay, hands off.")
                return
        job = await self.jobs.track("Pyxi", task)

        async def report(doing: str, step: int) -> None:
            log.info("hands step %d: %s", step, doing)
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
        await self.say_line(out.summary)

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
            windows.work_area(hwnd), len(panes), str(args.get("arrangement") or "auto")
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
        allowed = await self.hub.request(
            "confirm",
            agent="Pyxi",
            action=f"Book {what}{place}",
            reason=(
                f"Scout opens a browser you can watch and uses {booking.shared_labels(profile)} on "
                "that site. It asks you before the final booking click, and never pays: at the "
                "payment page it hands the window to you."
            ),
        )
        if not allowed:
            return "book: they said no, so nothing was booked. Say that's fine, briefly."
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
                played = await media.play(
                    query, music=args.get("app") == "youtube_music", back_to=here
                )
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
            await self.hub.emit("action", kind="open_url", value=url)
            return None
        if action == "open_app":
            note = await open_app(value)
            return None if note.startswith("Opened") else note
        if action == "copy":
            await self.hub.emit("action", kind="copy", value=value)
            return None
        if action == "insert":
            # Only type into the app they asked from; if they've switched away, don't guess.
            asked_in = self._ctx.window.app if self._ctx else None
            try:
                now = await self.screen.capture(pixels=False)
                current = now.window.app
            except Exception:
                current = asked_in
            if asked_in and current and current != asked_in:
                await self.hub.emit("action", kind="copy", value=value)
                return f"insert: they switched from {asked_in} to {current}, so it was copied instead. Tell them to paste it."
            await self.hub.emit("action", kind="insert", value=value)
            return None
        return f"Unknown quick action {action}."

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
        if getattr(self, "_model_llm", None) is None:
            from otto.llm import make_provider

            from pyxi.llm import otto_settings

            self._model_llm = make_provider(
                otto_settings(
                    self.settings, model=self.settings.model_3d, effort=self.settings.effort_3d
                )
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
        job = await self.jobs.track("Pyxi", f"Building {subject} in 3D", role="model")
        await self.hub.emit(
            "job_event",
            job_id=job.id,
            agent="Pyxi",
            event="status",
            data={"message": f"is building {subject} in 3D"},
        )
        try:
            result = await build_model(self._builder_llm(), brief, context)
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

    def _errand_llm(self) -> LLMProvider:
        """Errands can use a stronger model than conversation (PYXI_JOB_MODEL), if set."""
        if self.settings.job_model and self.settings.job_model != self.settings.brain_model:
            if self._job_llm is None:
                from pyxi.llm import make_llm

                self._job_llm = make_llm(self.settings, model=self.settings.job_model)
            return self._job_llm
        return self.llm

    async def _run_errand(self, task: str, user_text: str) -> None:
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

    def interrupt(self) -> None:
        self._speech_gen += 1
        if self._operator is not None:
            self._operator.cancelled = True  # talking to Pyxi stops her hands too
        self._spawn(self.hub.emit("stop_audio"))

    async def turn(
        self, user_text: str, *, shown: str | None = None, synthetic: bool = False
    ) -> None:
        """``synthetic``: a routine or a guide step, not words the user actually said (so an
        address in it never counts as the user naming that person)."""
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
            try:
                await self._graph.ainvoke(
                    {
                        "user_text": user_text,
                        "turn_id": turn_id,
                        "round": 0,
                        "notes": [],
                        "needs_more": False,
                        "stop": None,
                    }
                )
            except Exception as exc:
                log.exception("turn failed")
                error = f"{type(exc).__name__}: {exc}"
                await self.say_line("Oops, my brain hiccupped. Mind trying that again?")
            said = " ".join(self._said)
            self.history = [*self.history[-11:], (user_text, said)]
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
                    "said": said,
                    "rounds": len({c["round"] for c in self._calls}),
                    "input_tokens": self._usage.input_tokens + self._usage.cache_read_tokens,
                    "output_tokens": self._usage.output_tokens,
                    "cost_usd": self._usage.cost_usd,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "error": error,
                }
            )
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
            self._ctx = ctx
            await self.hub.emit(
                "capture",
                app=ctx.window.app,
                window=ctx.window.title[:120],
                excluded=ctx.excluded,
                elements=len(ctx.elements),
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
                screen=self._ctx.render(self.settings.screen.max_elements)
                if self._ctx
                else "(no screen)",
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

            held = {"say": None}  # a later round's reply, waiting to go just before its card

            async def speak_early(call: ToolCall) -> None:
                # Streamed: start talking the moment the spoken line is complete, while the cue
                # card and drawings are still arriving.
                if call.name == "say" and not first_round:
                    held["say"] = call  # may be lookup chatter; only said if a card follows
                    return
                if call.name == "say" and not early and self._speech_gen == self._turn_gen:
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
                if name == "say" and first_round and not early and not head["text"]:
                    # The first sentence goes to the voice as soon as it's written.
                    got = partial_string(raw, "text")
                    first = first_chunk(got[0]) if got and not got[1] else ""
                    if first:
                        head["text"] = first
                        self._noted |= {
                            int(m.group(2))
                            for m in MARKER.finditer(first)
                            if m.group(1).lower() == "note"
                        }
                        self._pending_say = ""
                        self._speak(strip_filler(first))
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
            for call in calls:
                if call.id in early:
                    continue  # already said, while the rest was still streaming in
                if call.name == "say" and looks_up and self._said:
                    # Mid-lookup chatter ("my first search came up empty"): held back, and only
                    # said if the turn ends before a newer answer.
                    self._pending_say = str(call.arguments.get("text", ""))
                    continue
                if call.name == "say" and retrying and self._said:
                    continue  # a retried drawing mustn't make Pyxi say its reply twice
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
                try:
                    note = await self._act(call.name, dict(call.arguments))
                except Exception as exc:
                    log.exception("tool %s failed", call.name)
                    note = f"{call.name} failed: {exc}"
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
            opened = any(
                c.name == "quick_action" and c.arguments.get("action") in ("open_url", "open_app")
                for c in calls
            )
            if (
                opened
                and all(c.name in ("say", "quick_action") for c in calls)
                and not needs_more
                and not _JUST_OPEN.match(self._user_text or "")
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
                elif self._calls:
                    self._speak("Done.")
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
            if scene.anchor and scene.anchor.element_id is not None:
                rect = resolve_rect(ctx, scene.anchor.element_id, None)
                scene.anchor = SceneAnchor(
                    element_id=scene.anchor.element_id, rect=list(rect) if rect else None
                )
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
        if name == "web_search":
            self._searches = getattr(self, "_searches", 0) + 1
            if self._searches > 3:
                return "web_search: that's enough searching. Answer now from what you've found, and say how sure you are."
            return await web.search_text(str(args.get("query", ""))[:200])
        if name == "read_page":
            return await web.read_text(str(args.get("url", ""))[:500])
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
            self.memory.remember("agent:pyxi", str(args["key"]), str(args["content"]))
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
                self._spawn(self._approve(result))
                return f"Waiting for the user to approve: {result.action}. Tell them it's ready to check; don't say it's done."
            return result
        if name == "schedule_routine":
            days = [str(d) for d in args.get("days", []) or []] or None
            r = self.routines.add(
                str(args["task"]),
                days=days,
                time=args.get("time"),
                every_minutes=args.get("every_minutes"),
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
            self._spawn(self._run_errand(task, words))
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
            ctx = await self._capture(follow_mouse=False)  # what just opened is in front
            if ctx.excluded:
                return f"look_again: that's a private app ({ctx.excluded}), so I can't look."
            self._ctx = ctx
            if image := ctx.model_image(self.settings.screen.model_image_width):
                self._images = [image]
            return f"look_again: this is the screen now ({ctx.window.title[:80]!r})."
        if name == "quick_action":
            return await self._quick_action(str(args.get("action", "")), str(args.get("value", "")))
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
        # Phrased as a problem note, so the model hears it and tries a real tool.
        return f"{name}: there's no tool by that name. Use one of the tools you have."

    # ================================================================== speech
    def _speak(
        self, text: str, *, agent: str = "Pyxi", voice: str | None = None, record: bool = True
    ) -> None:
        """Queue speech behind anything already being said. ``record=False`` for background
        lines (a finished build, an error) that aren't part of the current turn."""
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
        self, model: ObjectScene, *, restart: bool, background: bool = False
    ) -> None:
        """Put a 3D model on screen. Edits keep the id (the view keeps its camera); new beats get
        a new audio key so old narration can't play over the new explanation."""
        self._object = model
        if not background:  # a finished background build mustn't reshape the turn in progress
            self._visual_turn = True
        place = place_stage(self._ctx, self._placed.others("stage"))
        self._placed.put("stage", place, 90.0)
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
        fixed = data.get("fixed") or []
        files = [str(f) for f in (fixed or job.outputs)]
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
                title, body = "Your draft is ready", (job.summary or "")[:220]
            actions = (["open"] if files else []) + (
                ["walkthrough"] if job.role == "browser" else []
            )
        else:
            title = f"{job.agent_name} couldn't finish"
            body = (job.summary or job.status)[:220]
            actions = []
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
