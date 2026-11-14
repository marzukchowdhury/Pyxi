"""'Is this the address you meant?': a card before Pyxi acts on an email address the user gave.

People misspell addresses, and speech-to-text mishears them ("sam at example dot com",
"sam@example. com", "s a m underscore 21"). So before a tool call sends or drafts mail to an
address the user said or typed, searches mail from or to it, or types it into a form, one card
shows the address large, a part at a time, and waits: "Yes, that's it", a correction typed in
place, or "Never mind".

It stays out of the way. No card when the address
* is the user's own (their saved details, or the connected Google account);
* was confirmed on this card before (kept in ``confirmed_addresses.json`` in the data folder;
  "Wipe everything" clears it);
* was not given by the user: Pyxi found it in a mail it read, on the screen or in a contact;
* is exactly one already seen in their mail this session (the Google connector's own list of
  names and addresses, so no extra call is made).

One card a turn: every address in the request is on it together, and the tool calls that follow
use what was confirmed. A corrected address goes into the tool call in place of the old one.
"Never mind", no answer in time, or a card that fails: nothing is sent, searched or typed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyxi.companion.brain import Brain

log = logging.getLogger(__name__)

FILE = "confirmed_addresses.json"
KEEP = 500  # confirmed addresses remembered, newest last
TIMEOUT_S = 300  # the same as Pyxi's other in-turn cards

# Tool calls that act on an address in their arguments (every connected-account tool counts too).
ACTS = {
    "errand",
    "delegate",
    "fill_form",
    "book",
    "check_in_flight",
    "use_computer",
    "schedule_routine",
}

# ------------------------------------------------------------------ finding and normalising
_TLD = (
    r"(?:com|org|net|edu|gov|io|co|ca|uk|us|de|fr|in|bd|au|nz|sg|pk|app|dev|ai|me|info|biz|xyz)"
)
_PIECE = r"(?:(?:[A-Za-z0-9]\s+)+[A-Za-z0-9]\b|[A-Za-z0-9][A-Za-z0-9._+-]*)"  # "s a m", or "sam"
_JOIN = r"\s+(?:dot|period|point|underscore|under\s+score|dash|hyphen|minus|plus)\s+"
_LOCAL = rf"{_PIECE}(?:{_JOIN}{_PIECE})*"
_AT = r"(?:\s*@\s*|\s+(?:at\s+the\s+rate(?:\s+of)?|at\s+sign|at)\s+)"
_LABEL = r"[A-Za-z0-9-]+(?:\s+(?:dash|hyphen|minus)\s+[A-Za-z0-9-]+)*"
_DOT = r"(?:\s+(?:dot|period|point)\s+|\s*\.\s*)"
_CC = r"(?:uk|bd|in|au|ca|nz|sg|pk|za|jp|br|mx)"
# Said aloud or written with stray spaces: it must end in an ending people really use.
_HEARD = rf"{_LABEL}(?:{_DOT}{_LABEL}){{0,2}}?{_DOT}{_TLD}(?:(?:\.|\s+dot\s+){_CC})?(?![\w-])"
# Written out: any domain, and "sam@example" too (shown, and marked as not finished).
_WRITTEN = r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
_ANY = re.compile(
    rf"(?<![\w@.+'’-])(?P<local>{_LOCAL})(?:{_AT}(?P<heard>{_HEARD})|\s*@\s*(?P<written>{_WRITTEN}))",
    re.IGNORECASE,
)
_GLUE = (
    (r"\s+(?:dot|period|point)\s+", "."),
    (r"\s+(?:underscore|under\s+score)\s+", "_"),
    (r"\s+(?:dash|hyphen|minus)\s+", "-"),
    (r"\s+plus\s+", "+"),
)
_SHAPE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class Found:
    raw: str  # the words as they were said or typed
    address: str  # the address they make


def _joined(words: str) -> str:
    for pattern, sign in _GLUE:
        words = re.sub(pattern, sign, words, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", words)


def find(text: str) -> list[Found]:
    """Every email address in ``text``, said aloud or written, each as a real address:
    "sam at example dot com" and "sam@example. com" are both sam@example.com."""
    out: list[Found] = []
    for m in _ANY.finditer(text or ""):
        raw = m.group(0)
        local = _joined(m.group("local"))
        domain = _joined(m.group("heard") or m.group("written")).lower().strip(".-")
        address = f"{local}@{domain}"
        if re.sub(r"\s+", "", raw).lower() != address.lower():
            address = address.lower()  # heard, not typed: capitals are the listener's, not theirs
        out.append(Found(raw, address))
    return out


def problem(address: str) -> str | None:
    """What stops this from being an address, in a few words; None when the shape is right."""
    a = address.strip()
    if not a:
        return "It is empty."
    if re.search(r"\s", a):
        return "There is a space in it."
    if a.count("@") == 0:
        return "There is no @ in it."
    if a.count("@") > 1:
        return "There is more than one @."
    local, domain = a.split("@")
    if not local:
        return "Nothing comes before the @."
    if not domain:
        return "Nothing comes after the @."
    if "." not in domain:
        return "The part after the @ needs a dot, like example.com."
    if ".." in a:
        return "There are two dots in a row."
    if local[0] == "." or local[-1] == "." or domain[0] in ".-" or domain[-1] in ".-":
        return "A dot is in the wrong place."
    if not _SHAPE.fullmatch(a):
        tail = domain.rsplit(".", 1)[-1]
        if not re.fullmatch(r"[A-Za-z]{2,}", tail):
            return "The ending after the last dot looks wrong."
        return "It has a character an address can't have."
    return None


# ------------------------------------------------------------------ near misses
DOMAIN_TYPOS = {
    "gmial.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "gmaill.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.con": "gmail.com",
    "gmail.cm": "gmail.com",
    "hotmial.com": "hotmail.com",
    "hotmal.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "hotmail.con": "hotmail.com",
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "yhaoo.com": "yahoo.com",
    "yahoo.con": "yahoo.com",
    "outlok.com": "outlook.com",
    "outloo.com": "outlook.com",
    "outlookk.com": "outlook.com",
    "outlook.con": "outlook.com",
    "iclod.com": "icloud.com",
    "icloud.con": "icloud.com",
}


def distance(a: str, b: str, limit: int = 2) -> int:
    """How many single slips apart: a wrong, missing or extra character, or two swapped."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    before: list[int] = []
    last = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            best = min(last[j] + 1, row[j - 1] + 1, last[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                best = min(best, before[j - 2] + 1)
            row.append(best)
        before, last = last, row
    return last[-1]


def suggest(address: str, known: set[str]) -> str | None:
    """The address they most likely meant, when this one is a slip away from it: a well-known
    domain misspelt, or one or two characters off an address already known to be right."""
    low = address.lower()
    local, _, domain = low.partition("@")
    if local and domain in DOMAIN_TYPOS:
        return f"{local}@{DOMAIN_TYPOS[domain]}"
    if local and domain.endswith(".con"):
        return f"{local}@{domain[:-1]}m"
    near = sorted((distance(low, k), k) for k in known if k != low)
    near = [(d, k) for d, k in near if d == 1 or (d == 2 and len(low) >= 10)]
    if not near or (len(near) > 1 and near[0][0] == near[1][0]):
        return None  # nothing close, or two equally close: no guess
    return near[0][1]


# ------------------------------------------------------------------ what is remembered
class Book:
    """The addresses confirmed on the card, in a small file in the data folder."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / FILE

    def all(self) -> set[str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(a).lower() for a in raw if isinstance(a, str)} if isinstance(raw, list) else set()

    def add(self, *addresses: str) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            kept = [str(a).lower() for a in raw] if isinstance(raw, list) else []
        except (OSError, ValueError):
            kept = []
        for a in addresses:
            low = a.strip().lower()
            if low and problem(low) is None:
                kept = [k for k in kept if k != low] + [low]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(kept[-KEEP:], indent=2), encoding="utf-8")
        except OSError:
            log.warning("couldn't save the confirmed addresses")  # it will ask again: no harm

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            log.warning("couldn't clear the confirmed addresses")


# ------------------------------------------------------------------ who said it
def _squash(text: str) -> str:
    t = re.sub(r"\s+(?:at\s+the\s+rate(?:\s+of)?|at\s+sign|at)\s+", "@", (text or "").lower())
    return _joined(t)


def said(address: str, words: str) -> bool:
    """Is this address in the user's own words, however it was heard or spaced? ("Tarek Noor
    xyz at example dot com" does say tareknoorxyz@example.com.)"""
    return bool(address) and address.lower() in _squash(words)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


def _swap(value: Any, changes: dict[str, str]) -> Any:
    """The same arguments with each old address replaced by the confirmed one."""
    if isinstance(value, str):
        for old, new in changes.items():
            value = re.sub(re.escape(old), lambda _m, n=new: n, value, flags=re.IGNORECASE)
        return value
    if isinstance(value, dict):
        return {k: _swap(v, changes) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap(v, changes) for v in value]
    return value


def own_addresses(brain: Brain) -> set[str]:
    out: set[str] = set()
    try:
        from pyxi import booking

        out.add(booking.load(brain.settings.data_dir).get("email", ""))
    except Exception:
        log.debug("no saved details to read", exc_info=True)
    try:
        google = brain.connectors.google
        out.add(google.email or google._saved_email() or "")
    except Exception:
        log.debug("no connected Google account to read", exc_info=True)
    return {a.strip().lower() for a in out if a and a.strip()}


def correspondents(brain: Brain) -> set[str]:
    """Addresses seen in their mail this session (the connector keeps them for names): free to
    read. Their whole sent mail is not searched here: that would be a slow call on every turn."""
    try:
        return {str(a).lower() for a in brain.connectors.google._names}
    except Exception:
        return set()


def _earlier_words(brain: Brain) -> str:
    """What they said in the last few turns: "email sam at example dot com", "what should it
    say?", "that I'm running late" is still their address."""
    out = []
    for entry in list(getattr(brain, "history", []) or [])[-3:]:
        user = str(entry[0]) if isinstance(entry, list | tuple) and entry else ""
        if user and not user.startswith("("):
            out.append(user)
    return "\n".join(out)


@dataclass
class _Turn:
    turn_id: str
    final: dict[str, str]  # address as given (lower case) -> the one to use
    stopped: str = ""  # "cancelled" or "unconfirmed": nothing with an address goes on this turn


def _turn(brain: Brain) -> _Turn:
    state = getattr(brain, "_address_turn", None)
    turn_id = str(getattr(brain, "_turn_id", ""))
    if state is None or state.turn_id != turn_id:
        state = brain._address_turn = _Turn(turn_id, {})
    return state


def _stop_note(name: str, why: str) -> str:
    if why == "cancelled":
        return (
            f"{name}: they said never mind at the address check, so nothing was sent, searched "
            "or typed. They have been told. Don't try it another way; say nothing more."
        )
    return (
        f"{name}: the address was not confirmed (the check went unanswered), so nothing was "
        "sent, searched or typed. They have been told. Don't try it another way; say nothing "
        "more."
    )


def _answers(value: Any, n: int) -> list[str] | None:
    """What the card sent back, if it is n real addresses."""
    got = value.get("addresses") if isinstance(value, dict) else None
    if not isinstance(got, list) or len(got) != n:
        return None
    out = [str(a).strip() for a in got]
    return out if all(problem(a) is None for a in out) else None


async def before(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    """Called before a tool runs. Shows the card if the call acts on an address the user gave
    and nothing says it is right; puts the confirmed address into ``args`` (in place). Returns a
    note for the model when the action must not go ahead, else None."""
    if getattr(brain, "_synthetic", False):
        return None  # a routine or a guide step: not words they just said
    words = str(getattr(brain, "_user_text", "") or "")
    if name != "errand" and not any(find(s) for s in _strings(args)):
        return None  # no address in it: nearly every call ends here
    try:
        if name not in ACTS and brain.connectors.find(name) is None:
            return None
    except Exception:
        return None
    earlier = _earlier_words(brain)
    read = "\n".join(text for _, text in getattr(brain, "_turn_reads", []) or []).lower()
    spoken = find(words)
    spoken_low = {f.address.lower() for f in spoken}
    given: list[str] = []
    for s in _strings(args):
        for f in find(s):
            low = f.address.lower()
            theirs = said(low, words) or said(low, earlier)
            if not theirs and low not in read:
                # Not their exact words and not something Pyxi read: a slip away from what
                # they said is still their address, as the model wrote it down.
                theirs = any(distance(low, s2) <= 2 for s2 in spoken_low)
            if theirs and low not in {g.lower() for g in given}:
                given.append(f.address)
    if name == "errand":  # it is handed their words too, and works from them
        given += [f.address for f in spoken if f.address.lower() not in {g.lower() for g in given}]
    if not given:
        return None
    state = _turn(brain)
    if state.stopped:
        return _stop_note(name, state.stopped)
    todo = [a for a in given if a.lower() not in state.final]
    if todo:
        # One card a request: the other addresses they said go on it too.
        seen = {a.lower() for a in todo} | set(state.final)
        todo += [f.address for f in spoken if f.address.lower() not in seen]
        book = Book(brain.settings.data_dir)
        mine, kept, met = own_addresses(brain), book.all(), correspondents(brain)
        by_voice = _by_voice(brain, words)
        items: list[dict[str, Any]] = []
        for a in dict.fromkeys(todo):
            low = a.lower()
            wrong = problem(a)
            if wrong is None and low in (mine | kept | met):
                state.final[low] = a
                continue
            raw = next((f.raw for f in spoken if f.address.lower() == low), "")
            items.append(
                {
                    "address": a,
                    "suggestion": suggest(a, mine | kept | met),
                    "problem": wrong,
                    "heard": raw if by_voice and raw and raw != a else None,
                }
            )
        if items:
            if by_voice:
                brain._speak("Just checking the address.", record=False)
            brain._address_waiting = True  # words said now may be an answer to it (see heard)
            try:
                answer = await brain.hub.request(
                    "check_address",
                    timeout=TIMEOUT_S,
                    job_id="",
                    agent="Pyxi",
                    addresses=items,
                    voice=by_voice,
                )
            except Exception:
                log.exception("the address check card failed")
                answer = None
            finally:
                brain._address_waiting = False
            finals = _answers(answer, len(items))
            if finals is None:
                state.stopped = "cancelled" if answer is False else "unconfirmed"
                brain._speak(
                    "Okay, I've left it, and nothing was sent."
                    if state.stopped == "cancelled"
                    else "The address wasn't confirmed, so I've left it."
                )
                return _stop_note(name, state.stopped)
            for item, final in zip(items, finals, strict=True):
                state.final[item["address"].lower()] = final
            book.add(*finals)
    changes = {
        a: state.final[a.lower()] for a in given if state.final[a.lower()].lower() != a.lower()
    }
    # Their words, with each address as it was confirmed: what the errand and the approval
    # card check a recipient against.
    for f in spoken:
        final = state.final.get(f.address.lower())
        if final and f.raw != final:
            words = words.replace(f.raw, final)
    brain._user_text = words
    if changes:
        fixed = _swap(args, changes)
        if name == "errand":
            task = str(fixed.get("task", ""))
            missing = [new for new in changes.values() if new.lower() not in task.lower()]
            if missing:
                fixed["task"] = f"{task} (The address they confirmed: {', '.join(missing)}.)"
        args.clear()
        args.update(fixed)
    return None


def _by_voice(brain: Brain, words: str) -> bool:
    heard = str(getattr(brain, "_heard_text", "") or "").strip().lower()
    return bool(heard) and re.sub(r"\s+", " ", heard) in re.sub(r"\s+", " ", words.lower())


# ------------------------------------------------------------------ answering aloud
_NAME = r"(?:(?:hey|ok|okay)\s+)?(?:pyxi\W+)?"
_YES = re.compile(
    rf"(?i)\W*{_NAME}(?:yes|yeah|yep|yup|correct|right|confirm|it is|go ahead|looks? good|"
    r"that(?:'s| is)(?: right| correct| it| the one)|yes,? that(?:'s| is)(?: right| correct| it))"
    r"(?:\s+please)?\W*"
)
_NO = re.compile(rf"(?i)\W*{_NAME}(?:no|nope|not quite|that(?:'s| is) wrong|wrong|fix it)\W*")
_LEAVE = re.compile(
    rf"(?i)\W*{_NAME}(?:never ?mind|cancel(?: (?:it|that))?|forget (?:it|that)|leave it|don'?t)\W*"
)


def waiting(brain: Brain) -> bool:
    return bool(getattr(brain, "_address_waiting", False))


async def heard(brain: Brain, text: str) -> bool:
    """Words said or typed while the card is up. "Yes" confirms what the card shows; a spoken
    address fills the card and still waits for a tap or a "yes" (a second mishearing must not
    slip through); "no" opens the box; "never mind" cancels. Anything else is not for the card."""
    if not waiting(brain):
        return False
    found = find(text)
    if _YES.fullmatch(text or ""):
        event: dict[str, Any] = {"action": "confirm"}
    elif found:
        event = {"action": "fill", "address": found[-1].address, "heard": found[-1].raw}
    elif _LEAVE.fullmatch(text or ""):
        event = {"action": "cancel"}
    elif _NO.fullmatch(text or ""):
        event = {"action": "edit"}
    else:
        return False
    # The talk key was an answer to the card, not a new request: the waiting turn goes on.
    brain._turn_gen = brain._speech_gen
    brain._prefetch = None
    await brain.hub.emit("address_voice", **event)
    return True
