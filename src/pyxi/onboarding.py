"""The first-run tour: the firefly shows a new user around, once, on their own desktop.

The overlay plays it (desktop/src/renderer/src/overlay/onboarding); this is the brain's half:

* **when it runs**: a marker file, ``onboarding.json``, in the data folder. No marker means the
  tour has never been seen, which is true of a new install and of anyone updating from a
  version that had no tour. Finished, skipped or ended for age: never again by itself. A tour
  that broke off (an error, the app closed) is tried again on the next start, twice at most.
  "Replay the tour" in Settings plays it again without touching the marker.
* **what it keeps**: the name, email and date of birth go into the saved details Pyxi already
  keeps for forms and bookings (booking.py, ``profile.json``). What to call them, what they
  like and what they mostly use the PC for become memories, worded for the chat model
  ("Call them Sam.") and always in its prompt (:func:`memories_for`). All of it stays on this
  PC; nothing here talks to any server.
* **children**: Pyxi is for people of :data:`MIN_AGE` and over (docs/PRIVACY.md, Children). A
  date of birth below that stores nothing at all and ends the tour.

The messages (``{"type": "onboarding", "action": ...}`` in, ``{"type": "onboarding",
"event": ...}`` out) are typed in desktop/src/renderer/src/shared/protocol.ts.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from pyxi import booking

log = logging.getLogger(__name__)

FILE = "onboarding.json"
VERSION = 1
MAX_RETRIES = 2  # a tour that broke off is offered again on the next two starts
MIN_AGE = 13  # docs/PRIVACY.md, "Children"
STEPS = ("arrival", "talk", "screen", "about", "google", "demos", "landing")
ENDED = ("finished", "skipped", "underage")  # never shown again by itself
TALK_STEP_S = 120.0  # how long the "say hello" step may take their words for itself

SCOPE = "agent:pyxi"
CALL_KEY = "what to call them"
LIKES_KEY = "things they like"
USE_KEY = "main use of this PC"
KEYS = (CALL_KEY, LIKES_KEY, USE_KEY)
USES = {
    "study": "Mostly uses this PC for study.",
    "work": "Mostly uses this PC for work.",
    "creating": "Mostly uses this PC for creating things.",
    "everything": "Uses this PC for a bit of everything.",
}
# The fields of the saved details the tour may fill in.
PROFILE_FIELDS = ("first_name", "last_name", "email", "date_of_birth")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


# ------------------------------------------------------------------ small checks
def clean_text(value: Any, limit: int) -> str:
    """One line of plain text: no line breaks or control characters, no runs of spaces."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(text.split())[:limit].strip()


def valid_email(text: str) -> bool:
    return bool(_EMAIL.match(text)) and len(text) <= 200


def parse_birthday(text: str) -> date | None:
    """'2001-04-23' (as the tour sends it) to a date; None when it isn't a real one."""
    m = _DATE.match(str(text or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def birthday_problem(text: str, today: date) -> str | None:
    """Why this date of birth can't be right, or None."""
    born = parse_birthday(text)
    if born is None:
        return "That date doesn't look right."
    if born >= today:
        return "A date of birth is in the past."
    if born.year < 1900:
        return "That year looks too far back."
    return None


def join_words(words: list[str]) -> str:
    """'football', 'football and cooking', 'football, cooking and films'."""
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def memories_for(store: Any, query: str, k: int = 6) -> list[Any]:
    """What goes under "Things you remember about the user" for this request: what they told
    Pyxi in the tour (their name, what they like, what the PC is for), always, then whatever
    else matches their words. A search alone left "Call them Sam." out of every request that
    didn't happen to share a word with it."""
    basics = [m for m in store.all(SCOPE, 200) if m.key in KEYS]
    basics.sort(key=lambda m: KEYS.index(m.key))
    have = {m.key for m in basics}
    return basics + [m for m in store.search(SCOPE, query, k) if m.key not in have]


# ------------------------------------------------------------------ the marker
def load(data_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads((data_dir / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(data_dir: Path, state: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def due(data_dir: Path) -> bool:
    """Should the tour play by itself at this start? Never with PYXI_TOUR=0 (checks and
    recordings that start Pyxi on an empty data folder); "Replay the tour" still plays it."""
    if os.environ.get("PYXI_TOUR", "1") == "0":
        return False
    state = load(data_dir)
    if state.get("status") in ENDED:
        return False
    try:
        attempts = int(state.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    return attempts <= MAX_RETRIES


class Tour:
    """One brain's tour: the marker, the step it is on, and what it saves."""

    def __init__(self, data_dir: Path, memory: Any) -> None:
        self.data_dir = Path(data_dir)
        self.memory = memory
        self.active = False
        self.replay = False
        self.step: str | None = None
        self.step_at = 0.0  # when the overlay last said which step it is on

    # -------------------------------------------------------------- when it runs
    def due(self) -> bool:
        return due(self.data_dir)

    def listening_for_hello(self) -> bool:
        """In the "say hello" step, and recently: an overlay that went away mid-tour must not
        leave every later question answered by a tour nobody is watching."""
        return self.active and self.step == "talk" and time.monotonic() - self.step_at < TALK_STEP_S

    def start(self, *, replay: bool = False) -> bool:
        """The overlay is about to play it. False: not this time (it has been seen). Asked for
        again while it plays (the overlay was rebuilt: a monitor plugged in), it starts over
        and counts as the same try."""
        if not replay and not self.active:
            if not self.due():
                return False
            state = load(self.data_dir)
            state.update(
                version=VERSION,
                status="started",
                attempts=int(state.get("attempts") or 0) + 1,
                at=time.time(),
            )
            _write(self.data_dir, state)
        self.active, self.replay, self.step = True, replay, STEPS[0]
        self.step_at = time.monotonic()
        return True

    def enter(self, step: str) -> None:
        if self.active and step in STEPS:
            self.step, self.step_at = step, time.monotonic()

    def finish(self, how: str) -> None:
        """``finished`` or ``skipped`` (never again), ``underage`` (never again, nothing kept),
        anything else is a tour that broke off: it stays due for the next start."""
        was_active, replay = self.active, self.replay
        self.active, self.replay, self.step = False, False, None
        if not was_active:
            return
        state = load(self.data_dir)
        if replay and state.get("status") in ENDED and how != "underage":
            return  # a replay never changes how the first tour ended
        state.update(version=VERSION, status=how if how in ENDED else "failed", at=time.time())
        _write(self.data_dir, state)

    # -------------------------------------------------------------- what it keeps
    def save(self, details: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
        """Keep what they told the tour. Returns what the overlay needs to say: ``underage``
        (nothing was kept and the tour is over), ``errors`` per field that was left out, and
        ``saved``, the names of what was kept."""
        today = today or date.today()
        name = clean_text(details.get("name"), 40)
        email = clean_text(details.get("email"), 200)
        birthday = clean_text(details.get("date_of_birth"), 12)
        raw_likes = details.get("likes")
        likes: list[str] = []
        for item in raw_likes if isinstance(raw_likes, list) else []:
            word = clean_text(item, 40).strip(".,;")
            if word and word.lower() not in {w.lower() for w in likes}:
                likes.append(word)
        likes = likes[:12]
        use = str(details.get("use") or "")

        errors: dict[str, str] = {}
        if birthday:
            problem = birthday_problem(birthday, today)
            born = parse_birthday(birthday)
            if problem or born is None:
                errors["date_of_birth"] = problem or "That date doesn't look right."
                birthday = ""
            elif age_on(born, today) < MIN_AGE:
                # A child: nothing they typed is kept, and the tour is over for good. (What an
                # earlier tour kept is left alone: on a shared PC that is somebody else's.)
                self.finish("underage")
                return {"ok": False, "underage": True, "errors": {}, "saved": []}
            else:
                birthday = born.isoformat()
        if email and not valid_email(email):
            errors["email"] = "That doesn't look like an email address."
            email = ""

        saved: list[str] = []
        profile = booking.load(self.data_dir)
        wrote: list[str] = []
        if name and not (profile.get("first_name") or profile.get("last_name")):
            # What they asked to be called is the name on forms too, until they save another
            # in Settings. A name that is already saved there is never replaced by it.
            first, _, last = name.partition(" ")
            profile["first_name"] = first
            wrote.append("first_name")
            if last:
                profile["last_name"] = last
                wrote.append("last_name")
        for key, value in (("email", email), ("date_of_birth", birthday)):
            if value:  # an empty answer never replaces what is already there
                profile[key] = value
                wrote.append(key)
        if wrote:
            booking.save(self.data_dir, profile)
            saved += [k for k in ("email", "date_of_birth") if k in wrote]

        from pyxi.memory_guard import why_sensitive

        likes = [w for w in likes if why_sensitive(f"{LIKES_KEY}: Likes {w}.") is None]
        notes = [
            (CALL_KEY, f"Call them {name}." if name else "", "name"),
            (LIKES_KEY, f"Likes {join_words(likes)}." if likes else "", "likes"),
            (USE_KEY, USES.get(use, ""), "use"),
        ]
        for key, content, label in notes:
            if not content:
                continue
            try:
                self.memory.remember(SCOPE, key, content)
                saved.append(label)
            except ValueError as exc:  # the memory guard: it looked like a secret
                log.info("the tour did not remember %s: %s", label, exc)
        if wrote:
            state = load(self.data_dir)
            state["wrote"] = sorted({*state.get("wrote", []), *wrote})
            _write(self.data_dir, state)
        return {"ok": not errors, "underage": False, "errors": errors, "saved": saved}

    def forget(self) -> int:
        """Delete what the tour kept: its three memories, and the saved details it filled in
        (never a detail they saved themselves). Returns how many things went."""
        gone = sum(self.memory.forget(SCOPE, key) for key in KEYS)
        state = load(self.data_dir)
        wrote = [k for k in state.get("wrote", []) if k in PROFILE_FIELDS]
        if wrote:
            profile = booking.load(self.data_dir)
            kept = {k: v for k, v in profile.items() if k not in wrote}
            gone += len(profile) - len(kept)
            booking.save(self.data_dir, kept)
            state.pop("wrote", None)
            _write(self.data_dir, state)
        return gone

    def public(self) -> dict[str, Any]:
        return {"due": self.due(), "active": self.active}


# ------------------------------------------------------------------ the messages
async def _voice(brain: Any, hub: Any, line: str, text: str) -> None:
    """One of the tour's lines in Pyxi's own voice. The caption is drawn by the overlay either
    way, so no voice (they asked for quiet, the voice isn't ready, it failed) is just silence."""
    audio = None
    try:
        if not brain._quiet():
            speech = await brain.tts.synthesize(text, None)
            audio = base64.b64encode(speech.wav).decode() if speech.wav else None
    except Exception as exc:  # the caption carries it
        log.info("the tour's voice failed: %s", exc)
    await hub.emit("onboarding", event="voice", line=line, text=text, audio=audio)


async def heard(tour: Tour, hub: Any, text: str) -> bool:
    """Their first words to Pyxi, in the tour's "say hello" step: the tour answers them (by
    name, at once), so they never become a question for the model. True when taken."""
    if not tour.listening_for_hello():
        return False
    await hub.emit("onboarding", event="heard", text=clean_text(text, 200))
    return True


async def handle(tour: Tour, brain: Any, hub: Any, msg: dict[str, Any]) -> None:
    """Everything the overlay (and Settings) sends about the tour."""
    action = msg.get("action")
    if action in ("start", "replay"):
        replay = action == "replay"
        if tour.start(replay=replay):
            brain.warm_voice()  # so the first line can be spoken, if the voice is on this PC
            profile = booking.load(tour.data_dir)
            await hub.emit(
                "onboarding",
                event="start",
                replay=replay,
                # What is already saved, so someone updating isn't asked for it twice.
                have={
                    k: profile[k] for k in ("first_name", "email", "date_of_birth") if k in profile
                },
            )
    elif action == "step" and msg.get("step") in STEPS and tour.active:
        tour.enter(str(msg["step"]))
        # To every screen: the tour plays on one, the notch and the firefly live on another.
        await hub.emit("onboarding", event="step", step=tour.step)
    elif action == "say" and tour.active and clean_text(msg.get("text"), 240):
        brain._spawn(
            _voice(brain, hub, clean_text(msg.get("line"), 40), clean_text(msg["text"], 240))
        )
    elif action == "save" and tour.active and isinstance(msg.get("details"), dict):
        result = tour.save(msg["details"])
        await hub.emit("onboarding", event="saved", **result)
        if result["underage"]:
            await hub.emit("onboarding", event="ended", how="underage")
    elif action == "finish" and tour.active:
        how = str(msg.get("how") or "failed")
        how = how if how in (*ENDED, "failed") else "failed"
        tour.finish(how)
        await hub.emit("onboarding", event="ended", how=how)
    elif action == "forget":
        gone = tour.forget()
        await hub.emit(
            "notice",
            message="Deleted what you told Pyxi in the tour."
            if gone
            else "There was nothing from the tour to delete.",
        )
        await hub.emit("profile", profile=booking.load(tour.data_dir))
        await hub.emit("memories", memories=brain.memories_public())
