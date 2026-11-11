"""Trace, memory: "where did I see that pricing page last week?", answered with the exact place.

Trace looks in three places at once, all from this PC, with no model call:

* **Screen history**, the Memory Palace (:mod:`pyxi.memory.spatial`): every window they had in
  front of them, with its title and what was on it. A hit is a moment: the app, the window
  title, the day and time, and the snapshot, so "show me" brings it back as a hologram.
* **Their files** (only with "Let Pyxi search my files" on, :mod:`pyxi.files_access`): names
  and, through Windows Search, what's in them. A hit is a path to open.
* **Their mail** (when Gmail is connected): a hit is the email itself, with a link that opens
  it, and any link inside it that matches (the pricing page they were sent).

Time words ("last week", "on Tuesday", "yesterday afternoon") narrow all three. The best
match leads the notification; the others are listed under it.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi.palace.when import parse_when, strip_when

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

FILLER = {
    "where", "did", "see", "saw", "seen", "that", "the", "this", "those", "these", "was", "were",
    "what", "which", "when", "find", "show", "look", "looked", "looking", "open", "opened", "had",
    "have", "some", "thing", "one", "again", "for", "with", "from", "about", "and", "you", "your",
    "my", "me", "i", "a", "an", "of", "in", "it", "at", "on", "to", "week", "month", "remember",
    "can", "could", "please", "place", "there", "somewhere", "back",
}  # fmt: skip
KINDS = {  # what kind of thing it was, which is rarely written on the thing itself
    "page", "pages", "webpage", "site", "website", "tab", "window", "screen", "app", "email",
    "emails", "mail", "message", "file", "files", "document", "doc", "pdf", "link", "video",
    "article", "picture", "photo", "image", "spreadsheet", "sheet", "slide", "slides", "folder",
}  # fmt: skip
EVERYDAY = {  # on too many screens to find one by: never enough alone, of two words
    "main", "home", "start", "menu", "view", "edit", "help", "search", "settings", "options",
    "tools", "info", "list", "open", "close", "save", "share", "next", "back", "more", "other",
    "sign", "login", "account", "welcome", "untitled", "inbox", "online", "free", "best", "good",
    "thing", "stuff", "name", "time", "date", "today", "google", "chrome", "microsoft", "edge",
    "firefox", "mozilla", "windows", "browser", "your",
}  # fmt: skip
BROWSERS = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc"}
APP_NAMES = {  # what they call the app, not its process ("winword")
    "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "acrobat": "Adobe Acrobat",
    "acrord32": "Adobe Acrobat", "olk": "Outlook", "outlook": "Outlook", "explorer": "File Explorer",
    "code": "VS Code", "notepad": "Notepad", "figma": "Figma", "teams": "Teams", "ms-teams": "Teams",
}  # fmt: skip
APP_SUFFIX = re.compile(
    r"\s+[-\u2013\u2014]\s+(Adobe Acrobat( Reader)?( \(\d+-bit\))?|Adobe Reader|Word|Excel|"
    r"PowerPoint|Notepad|Visual Studio Code|File Explorer|Outlook|Figma|Paint|Photos|"
    r"Microsoft (Word|Excel|PowerPoint|Outlook))$",
    re.I,
)


@dataclass
class Hit:
    source: str  # "screen", "file", "email"
    title: str
    where: str  # the pointer, in words
    ts: float
    score: float
    link: str = ""
    path: str = ""
    snapshot_id: int = 0

    def line(self) -> str:
        when = datetime.fromtimestamp(self.ts)
        return (
            f"{self.where}, {when:%a} {when.day} {when:%b} at {when:%H:%M}"
            if self.ts
            else self.where
        )


def words(query: str) -> list[str]:
    q = strip_when(query.lower())
    q = re.sub(r"\b(last|this|next) (week|month)\b", " ", q)
    # Any letters, so "café", "Zürich" and 李雷 are words too
    found = re.findall(r"[^\W_][\w'.-]*", q)
    return [w for w in found if w not in FILLER and (len(w) > 1 or not w.isascii())][:8]


def window(query: str, when: str, now: datetime) -> tuple[float, float] | None:
    text = f"{when} {query}".lower()
    if "last week" in text:
        monday = (now - timedelta(days=now.weekday() + 7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return monday.timestamp(), (monday + timedelta(days=7)).timestamp()
    if "this week" in text:
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return monday.timestamp(), now.timestamp()
    if "last month" in text:
        first = (now.replace(day=1) - timedelta(days=1)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return first.timestamp(), now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
    if m := re.search(r"(\d+|a|few|couple of) (days|weeks) ago", text):
        n = {"a": 1, "few": 3, "couple of": 2}.get(m.group(1)) or int(m.group(1))
        days = n * (7 if m.group(2) == "weeks" else 1)
        center = now - timedelta(days=days)
        spread = timedelta(days=max(1, days * 0.4))
        return (center - spread).timestamp(), min(now, center + spread).timestamp()
    return parse_when(text, now)


def overlap(terms: list[str], text: str) -> float:
    t = text.lower()
    return sum(1.0 for w in terms if w in t) / max(1, len(terms))


def short(title: str) -> str:
    """A browser's window title without " - Google Chrome" and its tab count."""
    t = re.sub(
        r"\s+[-\u2013\u2014]\s+(Personal|Work)?\s*[-\u2013\u2014]?\s*(Google Chrome|Microsoft\u200b? Edge|Mozilla Firefox|Brave|Opera)$",
        "",
        title,
    )
    t = re.sub(r"\s+and \d+ more pages?$", "", t)
    t = re.sub(APP_SUFFIX, "", t)  # "Quote.pdf - Adobe Acrobat" is the file's name
    return re.sub(r"\s+[\u2013\u2014]\s+", " - ", t).strip()[:90]  # no dashes in what they read


# ------------------------------------------------------------------------------ the three places
def named(terms: list[str]) -> list[str]:
    """The words a screen has to have on it: the ones the screen history can search on,
    without the words for what kind of thing it was ("that pricing page" is about pricing;
    the page itself is titled "Pricing | Acme")."""
    real = [t for t in terms if len(t) > 2 or not t.isascii() or any(c.isdigit() for c in t)]
    return [t for t in real if t not in KINDS] or real  # "q3" and "v2" name things too


def telling(word: str) -> bool:
    """A word that picks a window out on its own: not an everyday one, and not a scrap."""
    long_enough = len(word) >= 4 or not word.isascii() or any(c.isdigit() for c in word)
    return long_enough and word not in EVERYDAY


def on_it(want: list[str], seen: str, heading: str) -> int:
    """How many of the words they named were on this screen, or 0 when that isn't enough
    to call it the one. ``seen``: everything on it; ``heading``: its title and its app.

    One word has to be there. Of three or more, at least half. Of two, both, or one that is
    in the heading and tells: people add a word of their own ("that stripe dashboard" for
    "Payments - Stripe", "the kitchenaid blender" for a mixer), and asking for both lost
    those (review). What that must not let back in is one ordinary word doing the finding
    ("page", "main"), which is how Wikipedia's Main Page was once "found" for an invoice."""
    tokens = set(re.findall(r"[^\W_]+", seen))

    def there(word: str, text: str) -> bool:  # a scrap like "q3" as a word, not inside one
        return word in tokens if len(word) <= 2 and word.isascii() else word in text

    have = [w for w in want if there(w, seen)]
    if len(want) == 2 and len(have) == 1:
        return 1 if telling(have[0]) and there(have[0], heading) else 0
    if len(want) <= 2:
        return len(have) if len(have) == len(want) else 0
    return len(have) if len(have) * 2 >= len(want) else 0


def from_screen(spatial: Any, terms: list[str], span: tuple[float, float] | None) -> list[Hit]:
    want = named(terms)
    if not want:
        return []  # nothing it can search on: every recent screen would come back
    query = " ".join(terms)
    found = spatial.find_snapshots(
        query, start=span[0] if span else None, end=span[1] if span else None, k=12
    )
    if not found and span:  # the right thing on the wrong day still helps
        found = spatial.find_snapshots(query, k=8)
    best: dict[tuple[str, str], Hit] = {}
    for s in found:
        app = str(s.get("app") or "")
        title = short(str(s.get("title") or ""))
        # The history matches any one word, so a screen comes back for "page" alone. It only
        # counts when enough of what they named was really on it (audit: "the Zephyr invoice
        # pricing page" was "Found it: Wikipedia", for its Main Page).
        snap = spatial.snapshot(int(s["id"])) or {}
        names = [str(e.get("name") or "") for e in snap.get("elements") or []]
        exe = app.lower().removesuffix(".exe")
        what = "your browser" if exe in BROWSERS else APP_NAMES.get(exe, app) or "a window"
        seen = " ".join([str(s.get("title") or ""), app, *names]).lower()
        # The heading is the title without " - Google Chrome": the browser's own name is on
        # every one of its windows.
        have = on_it(want, seen, f"{title} {exe} {what}".lower())
        if not have:
            continue
        score = (
            0.2
            + 0.6 * have / len(want)
            + overlap(terms, title)
            + (0.3 if span and span[0] <= s["ts"] <= span[1] else 0)
        )
        hit = Hit(
            "screen",
            title or app,
            f'On screen in {what}: "{title}"',
            float(s["ts"]),
            score,
            snapshot_id=int(s["id"]),
        )
        # One hit for one window, the last time it was in front: the same page looked at
        # five times was the whole list, and pushed the files and the mail off it.
        key = (app.lower(), title)
        if key not in best or hit.ts > best[key].ts:
            best[key] = hit
    return sorted(best.values(), key=lambda h: (-h.score, -h.ts))[:6]


def find_files(query: str, access: Any, limit: int = 8) -> tuple[list[Any], bool]:
    """Their files, and whether Windows Search found them (it looks inside them as well, and
    a file it returns has every word, in its name or in its text)."""
    from pyxi import files_access

    indexed = files_access.search_index(query, access.folders, limit * 3) or []
    indexed = [h for h in indexed if files_access.allowed(Path(h.path), access)]
    if indexed:
        return indexed[:limit], True
    return files_access.search(query, access, "", limit, use_index=False), False


def from_files(
    found: list[Any],
    terms: list[str],
    span: tuple[float, float] | None,
    indexed: bool = False,
) -> list[Hit]:
    hits = []
    for h in found:
        p = Path(h.path)
        in_span = bool(span and span[0] <= h.modified <= span[1] + 86400)
        score = overlap(terms, p.stem) + 0.2
        if indexed:  # found by what's in it: its name needn't say so (it scored 0.2, and
            score = max(score, 0.5)  # "I couldn't find it" was said of a file that was found)
        score += 0.3 if in_span else 0
        hits.append(
            Hit("file", p.name, f"In your files: {p}", float(h.modified), score, path=str(p))
        )
    return hits


def from_mail(
    mails: list[dict[str, Any]], terms: list[str], span: tuple[float, float] | None
) -> list[Hit]:
    hits = []
    for m in mails:
        d = m.get("date")
        ts = d.timestamp() if isinstance(d, datetime) else 0.0
        subject = str(m.get("subject") or "(no subject)")
        sender = re.sub(r"\s*<[^>]*>", "", str(m.get("from") or "")).strip('" ')
        link = f"https://mail.google.com/mail/u/0/#all/{m.get('id')}" if m.get("id") else ""
        inside = ""
        for text, url in m.get("links") or []:  # a link in the email that is the thing itself
            if terms and overlap(terms, f"{text} {url}") >= 0.5 and str(url).startswith("http"):
                inside = str(url)
                break
        about = overlap(terms, subject) + 0.5 * overlap(terms, str(m.get("text") or "")[:2000])
        if not about and not inside and not overlap(terms, sender):
            continue  # in the right week, but not about it
        score = about + 0.3
        if span and span[0] <= ts <= span[1]:
            score += 0.3
        where = f'In your mail: "{subject[:80]}" from {sender or "someone"}'
        hits.append(
            Hit(
                "email",
                subject,
                where + (f" (the link: {inside})" if inside else ""),
                ts,
                score,
                link=inside or link,
            )
        )
    return hits


def gmail_query(terms: list[str], span: tuple[float, float] | None) -> str:
    q = " ".join(terms)
    if span:
        start = datetime.fromtimestamp(span[0])
        end = datetime.fromtimestamp(span[1]) + timedelta(days=1)
        q += f" after:{start:%Y/%m/%d} before:{end:%Y/%m/%d}"
    return q


def best_first(hits: list[Hit]) -> list[Hit]:
    """Best match first. A document seen on screen that is also one of their files becomes one
    hit: the moment they had it open, and the file to open now."""
    files = [h for h in hits if h.source == "file"]
    merged: set[int] = set()
    for h in hits:
        if h.source != "screen":
            continue
        for f in files:
            if id(f) not in merged and Path(f.path).name.lower() in h.title.lower():
                h.path, h.title = f.path, Path(f.path).name
                h.where += f" (the file: {f.path})"
                h.score = max(h.score, f.score) + 0.1
                merged.add(id(f))
                break
    return sorted((h for h in hits if id(h) not in merged), key=lambda h: (-h.score, -h.ts))


def how_long(hours: float) -> str:
    """72 -> "3 days", 24 -> "a day", 6 -> "6 hours"."""
    if hours >= 48:
        return f"{round(hours / 24)} days"
    return "a day" if hours >= 24 else f"{max(1, round(hours))} hours"


def report(
    hits: list[Hit], query: str, looked: list[str], history_hours: float | None = None
) -> dict[str, Any]:
    places = " and ".join([", ".join(looked[:-1]), looked[-1]] if len(looked) > 1 else looked)
    if not hits:
        # How far back the screen history goes: "last week" can't be in three days of it,
        # and "nothing in your screen history" read as if all of it had been looked through.
        back = (
            f" Screen history goes back {how_long(history_hours)}."
            if history_hours and "screen history" in looked
            else ""
        )
        return {
            "title": "I couldn't find it",
            "body": f"Nothing about {query!r} in your {places}. A word from its title helps "
            f"most.{back}",
            "summary": f"Nothing found for {query!r} in {places}.{back}",
        }
    top, rest = hits[0], hits[1:5]
    # The notification names the file; its full path is behind the Open button (and in the
    # summary, for the model), not spelled out in the notch.
    body = top.line().replace(f" (the file: {top.path})", "") + "."
    if top.source == "screen":
        body += ' Say "show me" and I\'ll bring that screen back.'
    elif top.source == "file":
        body += " It's ready to open."
    sections = []
    for name, src in (("On screen", "screen"), ("Files", "file"), ("Mail", "email")):
        lines = [h.line() for h in hits if h.source == src][:4]
        if lines:
            sections.append({"heading": name, "lines": lines})
    summary = (
        "Best match: "
        + top.line()
        + (f" (snapshot {top.snapshot_id})" if top.snapshot_id else "")
        + ". "
        + " ".join(f"Also: {h.line()}." for h in rest)
    )
    return {
        "title": f"Found it: {top.title}"[:70],
        "body": body,
        "changes": [h.line() for h in rest],
        "card": {"title": f"Where you saw {query}"[:80], "sections": sections},
        "link": top.link,
        "summary": summary,
    }


# ------------------------------------------------------------------------------ the job
clock = datetime.now  # tests move time


async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    from pyxi import files_access

    query = str(job.params.get("query") or job.task)
    when = str(job.params.get("when") or "")
    terms = words(f"{query} {when}")
    if not terms:
        job.status, job.summary = "failed", "What should I look for? A word or two from it helps."
        job.data = {"title": "What should I look for?", "body": job.summary}
        return
    span = window(query, when, clock())
    looked: list[str] = []
    hits: list[Hit] = []
    await manager._status(job, f"Looking for {' '.join(terms)}")

    spatial = getattr(manager, "spatial", None)
    if spatial is not None:
        looked.append("screen history")
        hits += await asyncio.to_thread(from_screen, spatial, terms, span)
    access = files_access.load(manager.settings.data_dir)
    if access.enabled and job.params.get("files", True):
        looked.append("files")
        found, indexed = await asyncio.to_thread(find_files, " ".join(terms), access)
        hits += from_files(found, terms, span, indexed)
    google = getattr(manager, "google", None)
    if google is not None and google.connected():
        looked.append("mail")
        try:
            mails = await google.gmail_fetch(gmail_query(terms, span), 8)
        except Exception:
            mails = []
        hits += from_mail(mails, terms, span)
    if not looked:
        raise RuntimeError(
            "There's nowhere to look yet: screen memory is off and nothing is connected."
        )
    ranked = best_first([h for h in hits if h.score >= 0.45])
    kept = getattr(getattr(manager.settings, "screen", None), "retention_hours", None)
    rep = report(ranked, " ".join(terms), looked, kept)
    job.summary = rep.pop("summary")
    job.data = {**rep, "hits": [asdict(h) for h in ranked[:10]]}
    if ranked and ranked[0].path:
        job.outputs = [ranked[0].path]
        job.data["actions"] = ["open", "folder"]
    elif ranked and ranked[0].link:
        job.data["actions"] = ["link"]
    job.status = "done" if ranked else "failed"
