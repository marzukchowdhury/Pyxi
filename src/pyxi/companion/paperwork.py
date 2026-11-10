"""Paperwork: the errands nobody wants, done quietly in the background.

* **Bills explained on the bill.** A phone bill, an insurance statement or a tax letter on
  screen gets tags pinned to its real lines (what you owe, by when, why, and anything that
  looks wrong). That's ``explain_on_screen`` with style 'on_picture' and a ``bill`` summary;
  this module puts the summary in the task tray. ``show_file`` opens a PDF bill on screen first.
* **Forms filled from your documents.** A job application or a government form is filled by
  Scout from the saved booking details plus a CV or document they name (read with the file
  tools), as a booking job: Submit is approved on a card first, and passwords, card numbers
  and government IDs are left for them to type (they're cut out of the documents Scout sees).
* **Flight check-in when it opens.** A one-off routine exactly 24 hours before departure starts
  a Scout check-in job (no model turn in between). Scout skips paid seats and upgrades, and the
  boarding pass link arrives in a notification.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi import booking, files_access
from pyxi.companion.filework import _paths, consent

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job
    from pyxi.companion.brain import Brain
    from pyxi.routines import Routine

PAPER_TOOLS = {"fill_form", "check_in_flight", "show_file"}
DOC_TYPES = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt", ".png", ".jpg", ".jpeg"}

clock = datetime.now  # tests move time


def open_path(path: Path) -> None:
    """Open a file in its usual app (Edge for a PDF, Preview on a Mac). Replaced in tests."""
    if __import__("sys").platform == "darwin":
        import subprocess

        subprocess.Popen(["open", str(path)])
        return
    os.startfile(str(path))  # type: ignore[attr-defined]


async def handle(brain: Brain, name: str, args: dict[str, Any]) -> str | None:
    if name == "show_file":
        return await _show_file(brain, args)
    if name == "fill_form":
        return await _fill_form(brain, args)
    if name == "check_in_flight":
        return await _check_in(brain, args)
    return None


# ---------------------------------------------------------------------------------- bills
async def _show_file(brain: Brain, args: dict[str, Any]) -> str:
    raw = _paths(args.get("path"))
    if not raw:
        return "show_file: which file? Find its path with search_files first."
    path = Path(raw[0])
    if not path.is_file():
        return f"show_file: there's no file at {path}. Use search_files to find it."
    access = await consent(brain)
    if isinstance(access, str):
        return f"show_file: {access}"
    if not files_access.allowed(path, access):
        return "show_file: that file is outside the folders they allowed (Settings > Let Pyxi search my files)."
    try:
        await asyncio.to_thread(open_path, path)
    except (OSError, AttributeError) as exc:
        return f"show_file: Windows couldn't open {path.name} ({exc})."
    # The viewer takes a moment to draw the first page; then look at it, like look_again.
    seen = await brain._act("look_again", {"seconds": 2.5})
    return (
        f"Opened {path.name} on their screen. {seen or ''} Now explain it on the page with "
        "explain_on_screen (a bill: style on_picture with `bill`)."
    )


def bill_summary(bill: dict[str, Any]) -> tuple[str, str]:
    """The tray chip for an explained bill: ('Explaining your Telus bill', 'You owe $84.12 by
    28 Nov. 2 things to check: ...')."""
    who = str(bill.get("from") or "").strip()
    title = f"Explaining your {who} bill" if who else "Explaining your bill"
    owe, due = str(bill.get("owe") or "").strip(), str(bill.get("due") or "").strip()
    flags = [str(f).strip() for f in bill.get("flags") or [] if str(f).strip()]
    parts = []
    if owe:
        parts.append(f"You owe {owe}" + (f" by {due}" if due else "") + ".")
    elif due:
        parts.append(f"Due {due}.")
    if flags:
        n = len(flags)
        parts.append(f"{n} thing{'s' if n > 1 else ''} to check: " + "; ".join(flags[:3]) + ".")
    else:
        parts.append("Nothing looks wrong.")
    return title[:60], " ".join(parts)[:300]


async def bill_chip(brain: Brain, bill: Any) -> None:
    """A quiet chip in the task tray with the bill's verdict; it stays a few seconds after the
    tags are on the bill, and the summary is in the job list for follow-ups."""
    if not isinstance(bill, dict):
        return
    title, summary = bill_summary(bill)
    job = await brain.jobs.track("Pyxi", title)
    await brain.jobs.settle(job, ok=True, summary=summary, data={"bill": bill})


# ---------------------------------------------------------------------------------- forms
async def _read_documents(brain: Brain, paths: list[str]) -> dict[str, str] | str:
    if not paths:
        return {}
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        return (
            f"fill_form: I can't find {', '.join(Path(p).name for p in missing)}. Find the exact "
            "path with search_files first, or ask them."
        )
    access = await consent(brain)
    if isinstance(access, str):
        return f"fill_form: {access}"
    out: dict[str, str] = {}
    for p in paths:
        path = Path(p)
        if not files_access.allowed(path, access):
            return f"fill_form: {path.name} is outside the folders they allowed (Settings > Let Pyxi search my files)."
        try:
            out[str(path)] = await asyncio.to_thread(files_access.read_text, path, 8000)
        except Exception as exc:
            return f"fill_form: couldn't read {path.name}: {str(exc)[:160]}"
    return out


async def _fill_form(brain: Brain, args: dict[str, Any]) -> str | None:
    what = str(args.get("what") or "").strip() or "the form"
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return (
            "fill_form: which page is the form on? Use the address from their browser's address "
            "bar if it's on screen; otherwise ask them for the link."
        )
    profile = booking.load(brain.settings.data_dir)
    paths = [p for p in _paths(args.get("documents")) if Path(p).suffix.lower() in DOC_TYPES]
    docs = await _read_documents(brain, paths)
    if isinstance(docs, str):
        return docs
    if not profile and not docs:
        return (
            "fill_form: I have nothing to fill it from yet. Ask them to add their details in "
            "Settings, Booking details, or to name a CV or document to use."
        )
    used = booking.shared_labels(profile) if profile else ""
    names = " and ".join(Path(p).name for p in docs)
    source = " and ".join(x for x in (used, names) if x)
    allowed = await brain.hub.request(
        "confirm",
        agent="Pyxi",
        action=f"Fill in {what}",
        reason=(
            f"Scout opens a browser you can watch and fills it from {source}. It asks you before "
            "the final Submit, and never types passwords, card numbers or ID numbers: it leaves "
            "those for you."
        ),
    )
    if not allowed:
        return "fill_form: they said no, so nothing was filled in. Say that's fine, briefly."
    job = await brain.jobs.start(
        "scout",
        booking.form_brief(args, profile, docs, await brain._now()),
        start_url=url,
        kind="booking",
        files=list(docs),
        params={"form": True},
    )
    return (
        f"Started {job.id}: Scout is filling it in a window they can watch. Say one short line "
        "that names the job and that you'll check with them before it's sent, and nothing more."
    )


# ---------------------------------------------------------------------------------- check-in
async def _check_in(brain: Brain, args: dict[str, Any]) -> str | None:
    ref = str(args.get("confirmation") or "").strip().upper()
    departure = booking.parse_departure(str(args.get("departure") or ""))
    if not ref:
        return (
            "check_in_flight: I need the booking reference (6 letters and numbers). Look in their "
            "mail for the airline's confirmation (gmail_search), or ask them."
        )
    if departure is None:
        return (
            "check_in_flight: I need the departure date and time, as YYYY-MM-DDTHH:MM in the "
            "departure airport's local time. It's in the airline's confirmation email."
        )
    profile = booking.load(brain.settings.data_dir)
    last = str(args.get("last_name") or profile.get("last_name") or "").strip()
    if not last:
        return "check_in_flight: what last name is on the booking? Ask them, briefly."
    now = clock()
    if departure <= now:
        return "check_in_flight: that flight has already left. Check the date with them."
    key = f"checkin:{ref}:{departure:%Y-%m-%dT%H:%M}"
    if brain.routines.has_key(key):
        return "check_in_flight: that check-in is already scheduled. Say so in a few words."
    hours = int(args.get("opens_hours_before") or 24)
    opens = booking.checkin_opens(departure, hours)
    trip = {**args, "confirmation": ref, "last_name": last, "departure": f"{departure:%Y-%m-%d %H:%M}"}
    task = booking.checkin_brief(trip, profile)
    url = str(args.get("url") or "").strip()
    spec = {
        "agent": "scout",
        "task": task,
        "kind": "checkin",
        "start_url": url if url.startswith(("http://", "https://")) else None,
    }
    title = task.splitlines()[0]
    if opens <= now:  # it's already open: do it now
        job = await brain.jobs.start("scout", task, kind="checkin", start_url=spec["start_url"])
        return (
            f"Check-in is already open, so {job.agent_name} is checking them in now ({job.id}). "
            "Say one short line; the boarding pass comes in a notification."
        )
    r = brain.routines.add(
        title,
        on=opens.date().isoformat(),
        time=opens.strftime("%H:%M"),
        kind="job",
        key=key,
        job=spec,
        now=now,
    )
    await brain.hub.emit("routines", routines=brain.routines.public())
    when = f"{opens:%a} {opens.day} {opens:%b} at {opens:%H:%M}"
    chip = await brain.jobs.track("Pyxi", f"{title} when it opens")
    await brain.jobs.settle(chip, ok=True, summary=f"Scheduled for {when}, 24 hours before departure.")
    return (
        f"Saved ({r.id}): Scout checks them in on {when}, exactly {hours} hours before departure, "
        "skips paid seats and upgrades, and sends the boarding pass link. It runs while Pyxi is "
        "open. Say that in one short sentence."
    )


async def start_routine_job(brain: Brain, r: Routine) -> None:
    """A routine that is a teammate's job (a check-in): start it straight away, no turn."""
    spec = r.job or {}
    await brain.jobs.start(
        str(spec.get("agent") or "scout"),
        str(spec.get("task") or r.task),
        kind=str(spec.get("kind") or "task"),
        start_url=spec.get("start_url"),
        params=spec.get("params") or {},
    )


# ---------------------------------------------------------------------------------- results
def notice(job: Job, data: dict[str, Any]) -> dict[str, Any] | None:
    """The notification for a finished check-in or form, or None for the usual one."""
    if job.kind == "checkin":
        flight = job.task.splitlines()[0].removeprefix("Check in: ").strip()
        if job.status == "done" and data.get("checked_in"):
            bits = [
                f"Seat {data['seat']}." if data.get("seat") else "",
                f"Gate {data['gate']}." if data.get("gate") else "",
                f"Boarding {data['boarding']}." if data.get("boarding") else "",
            ]
            link = str(data.get("boarding_pass") or "") or None
            return {
                "ok": True,
                "title": f"You're checked in: {flight}"[:70],
                "body": " ".join(b for b in bits if b) + " Your boarding pass is ready.",
                "link": link,
                "actions": ["link"] if link else [],
            }
        link = str(data.get("link") or "") or None
        need = str(data.get("needs_you") or "").strip()
        return {
            "ok": False,
            "title": f"Check-in needs you: {flight}"[:70],
            "body": (f"It needs {need}. " if need else "") + (job.summary or job.status)[:200],
            "link": link,
            "actions": ["link"] if link else [],
        }
    if job.kind == "booking" and job.params.get("form"):
        what = job.task.splitlines()[0].removeprefix("Fill: ").strip()
        if job.status == "done" and data.get("submitted"):
            left = [str(x) for x in data.get("left_for_you") or []][:4]
            code = str(data.get("confirmation") or "")
            body = " ".join(
                x
                for x in (
                    f"Confirmation {code}." if code else "",
                    ("Left for you: " + ", ".join(left) + ".") if left else "",
                )
                if x
            )
            return {
                "ok": True,
                "title": f"Sent: {what}"[:70],
                "body": body or (job.summary or "Done.")[:220],
                "actions": [],
            }
        return {
            "ok": False,
            "title": f"Not sent: {what}"[:70],
            "body": (job.summary or job.status)[:220],
            "actions": [],
        }
    return None
