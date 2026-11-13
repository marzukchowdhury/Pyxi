"""Ledger building a big workbook: a plan first, then one sheet at a time.

One reply can hold only so much. Live, 13 November: a 15-sheet financial workbook, asked for
in one go, was planned as one JSON reply, the reply hit its length limit and was cut off, and
so was the second try: "my plan came out unreadable, twice", after 75 seconds and with nothing
built. So a big job is split by Ledger itself:

1. A short call for the plan: every sheet's name, its purpose in a line, its columns, and
   which other sheets it reads from.
2. The sheets are made (empty), then one call per sheet for that sheet's cells, formulas and
   formatting. A sheet that reads from others waits for them and is shown what is on them, so
   its formulas point at real cells. Sheets that don't depend on each other are asked for
   side by side, a few at a time, and each one's edits go into the file as they arrive.
3. A last call for what belongs to the workbook as a whole: totals across sheets, a summary's
   formulas, charts, and another try at any edit that failed.

A reply that comes back cut off (it can't be read as JSON) costs only its own part: that sheet
is asked for again in smaller parts, a few rows at a time. If a sheet still can't be had, the
rest of the workbook is built and saved all the same, and the result says exactly which
sheets are done and which are not.

A small edit never comes here (:func:`looks_big`): a pivot or a totals row is one call, as it
always was.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

BIG_BRIEF = 1500  # characters: a brief this long is planned before it is built
SPLIT_SHEETS = 4  # a brief that names this many sheets or more is planned first
MIN_SHEETS = 3  # a plan with fewer sheets than this goes the usual way, in one call
MAX_SHEETS = 40
PARALLEL = 4  # sheets with the model at the same time
MAX_PARTS = 8  # the most parts one sheet is asked for in, once it has to be cut up
PART_ROWS = 15  # rows in one part

Ask = Callable[[str, str], Awaitable[tuple[str, float]]]
Put = Callable[[dict[str, Any]], Awaitable[str]]  # one edit: "" when it worked, else why not

_NUMBER = {
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "dozen": 12,
}  # fmt: skip
_SHEET_COUNT = re.compile(
    rf"(?i)\b(\d{{1,3}}|{'|'.join(_NUMBER)})[- ]?(?:[a-z]+[- ]){{0,2}}?(?:work)?(?:sheets?|tabs?)\b"
)
_LISTED = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|[-*•])\s+\S")


def looks_big(task: str) -> bool:
    """Is this a build to plan first? From the brief alone: it is long, it says how many
    sheets (four or more), or it lists them. "Make a pivot of sales by region" is none of
    these, and stays one fast call."""
    for m in _SHEET_COUNT.finditer(task):
        word = m.group(1).lower()
        if (int(word) if word.isdigit() else _NUMBER[word]) >= SPLIT_SHEETS:
            return True
    return len(task) >= BIG_BRIEF or len(_LISTED.findall(task)) >= 6


PLANNER = (
    "You are {name}, the documents specialist on the user's team. The spreadsheet job below is "
    "too big to write in one reply, so plan it first; each sheet is written afterwards, in its "
    "own reply. Return JSON only:\n"
    '{"sheets": [{"name": "<tab name, 31 characters at most>", "purpose": "<one line>", '
    '"columns": ["<header>", ..], "rows": <about how many rows of data>, '
    '"needs": ["<names of other sheets in this plan whose cells this one reads>"]}]}\n'
    "List every sheet the task asks for, in the order they should appear, and none it doesn't "
    "ask for. A sheet that is already in the file and only needs changing is listed too, under "
    "its real name. Inputs and assumptions come before the sheets that read them. Keep it "
    "short: no data, no formulas, no em dashes. If the task is a small edit to what is already "
    'there and not several sheets to build, return {"sheets": []}.'
)


@dataclass
class Sheet:
    name: str
    purpose: str = ""
    columns: list[str] = field(default_factory=list)
    rows: int = 0
    needs: list[str] = field(default_factory=list)
    state: str = "waiting"  # built, partly, missing
    why: str = ""  # why it is not (all) there
    edits: int = 0
    failed: list[str] = field(default_factory=list)  # edits that didn't work, with why
    reasons: list[str] = field(default_factory=list)  # just why, for the user's card

    def line(self, n: int) -> str:
        cols = f" Columns: {', '.join(self.columns[:30])}." if self.columns else ""
        rows = f" About {self.rows} rows." if self.rows else ""
        needs = f" Reads from: {', '.join(self.needs)}." if self.needs else ""
        return f"{n}. {self.name}: {self.purpose.rstrip('.')}.{cols}{rows}{needs}"


@dataclass
class Built:
    sheets: list[Sheet]
    cost_usd: float = 0.0
    problems: list[str] = field(default_factory=list)  # for the user's card
    finished: bool = True  # the last step (cross-sheet formulas, charts) went in

    @property
    def done(self) -> list[Sheet]:
        return [s for s in self.sheets if s.state == "built"]

    @property
    def not_done(self) -> list[Sheet]:
        return [s for s in self.sheets if s.state != "built"]

    def changes(self) -> list[str]:
        return [f"Built the {s.name} sheet" for s in self.sheets if s.state == "built"] + [
            f"Started the {s.name} sheet" for s in self.sheets if s.state == "partly"
        ]

    def headline(self) -> str:
        """What was built, in words that stand on their own: every sheet by name when some
        are missing, so nobody has to open the file to find out which."""
        done, total = self.done, len(self.sheets)
        if len(done) == total:
            names = ", ".join(s.name for s in done[:6]) + (
                f" and {total - 6} more" if total > 6 else ""
            )
            text = f"Built all {total} sheets: {names}."
        else:
            text = f"Built {len(done)} of {total} sheets."
            if done:
                text += f" Done: {', '.join(s.name for s in done)}."
            partly = [s for s in self.sheets if s.state == "partly"]
            missing = [s for s in self.sheets if s.state not in ("built", "partly")]
            if partly:
                text += f" Only partly filled in: {', '.join(s.name for s in partly)}."
            if missing:
                text += f" Not built: {', '.join(s.name for s in missing)}."
            why = next((s.why for s in self.not_done if s.why), "")
            text += f" {why}" if why else ""
            text += " Ask me to finish those and I'll pick up from here."
        if not self.finished:
            text += " The totals and charts that tie the sheets together didn't go in; ask me for those too."
        return text


def read_json(text: str) -> dict[str, Any] | None:
    """A reply's JSON object, or None when there is none to read: cut off at the length
    limit, most often."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _clean_name(name: Any, most: int) -> str:
    return re.sub(r"[\[\]:*?/\\]", " ", str(name or "")).strip().strip("'")[:most].strip()


def read_plan(data: dict[str, Any] | None, kind: str) -> list[Sheet] | None:
    """The plan from the model's reply: None when it can't be read, [] when it says this is
    not a build after all."""
    if data is None or not isinstance(data.get("sheets"), list):
        return None
    most = 31 if kind == "xlsx" else 100
    sheets: list[Sheet] = []
    seen: set[str] = set()
    for item in data["sheets"][:MAX_SHEETS]:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = _clean_name(item.get("name"), most)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            rows = max(0, int(float(item.get("rows") or 0)))
        except (TypeError, ValueError):
            rows = 0
        cols = item.get("columns") if isinstance(item.get("columns"), list) else []
        needs = item.get("needs") if isinstance(item.get("needs"), list) else []
        sheets.append(
            Sheet(
                name=name,
                purpose=" ".join(str(item.get("purpose") or "").split())[:200],
                columns=[" ".join(str(c).split())[:60] for c in cols if str(c).strip()][:40],
                rows=rows,
                needs=[_clean_name(n, most) for n in needs if str(n).strip()],
            )
        )
    known = {s.name.lower(): s.name for s in sheets}
    for s in sheets:
        s.needs = list(
            dict.fromkeys(
                known[n.lower()] for n in s.needs if n.lower() in known and n.lower() != s.name.lower()
            )
        )
    _no_circles(sheets)
    return sheets


def _no_circles(sheets: list[Sheet]) -> None:
    """A sheet waits for the ones it reads from. Two that read from each other would wait
    for ever, so a need that closes a circle is dropped (the last step ties those up)."""
    by_name = {s.name: s for s in sheets}
    settled: set[str] = set()
    left = [s.name for s in sheets]
    while left:
        ready = [n for n in left if all(m in settled for m in by_name[n].needs)]
        if not ready:  # a circle: the first one left goes ahead without what isn't built yet
            first = by_name[left[0]]
            first.needs = [m for m in first.needs if m in settled]
            ready = [first.name]
        settled.update(ready)
        left = [n for n in left if n not in settled]


def tabs_of(target: Any) -> list[str]:
    if getattr(target, "kind", "") == "xlsx":
        return list(target.wb.sheetnames)
    return [s["properties"]["title"] for s in target.meta.get("sheets", [])]


def _blank_tab(target: Any) -> str | None:
    """The one empty tab of a brand-new file ('Sheet', 'Sheet1'), or None."""
    names = tabs_of(target)
    if len(names) != 1:
        return None
    if getattr(target, "kind", "") == "xlsx":
        ws = target.wb[names[0]]
        empty = ws.max_row <= 1 and ws.max_column <= 1 and ws.cell(1, 1).value in (None, "")
    else:
        empty = not any(any(c not in ("", None) for c in row) for row in target._seen(names[0]))
    return names[0] if empty else None


async def layout(target: Any, names: list[str], rows: int = 30) -> str:
    """What is on these tabs now, the way the outline shows it, read from the file itself
    (never remembered): the cells a formula on another sheet can point at."""
    if not names:
        return ""
    if hasattr(target, "_fresh"):  # a Google Sheet: read these tabs again if they changed
        await target._fresh(names)
    text = "\n" + await asyncio.to_thread(target.describe, rows)
    parts = []
    for name in names:
        m = re.search(
            rf"\n(?:Sheet|Tab) {re.escape(repr(name))}:.*?(?=\n\n(?:Sheet|Tab) |\nNot read yet|\Z)",
            text,
            re.S,
        )
        if m:
            parts.append(m.group(0).strip())
    return "\n\n".join(parts)


_SHEET_RULES = """Rules for this reply:
- Put "sheet": "{name}" on every edit. Never add, rename or delete a sheet: they are all made.
- Write cells with set_cells and explicit addresses, the header row in row 1 unless the brief says otherwise. One set_cells can hold many cells.
- Formulas, not typed-in results, wherever a number follows from other cells, so the file stays live. A formula may read the sheets shown under "Sheets this one reads from", at the addresses shown there, written like ='Sheet name'!B5. Do not point at a sheet that is not shown: leave that cell for the last step.
- Then this sheet's own formatting: a bold header, number formats, column widths, a frozen header row. Charts and totals that span sheets come in the last step.
- Sample or placeholder data: 10 to 12 rows is plenty unless the brief gives a number. Keep the whole reply under about 250 cells; if this sheet needs more, send the first {part_rows} rows and set "more": true.
Return JSON only: {{"edits": [..], "more": false}}."""


class Builder:
    def __init__(
        self,
        task: str,
        *,
        target: Callable[[], Awaitable[Any]],
        put: Put,
        ask: Ask,
        system: str,
        name: str = "Ledger",
        context: str = "",
        progress: Callable[[str], Awaitable[None]] | None = None,
        least: int | None = None,
    ) -> None:
        # The fewest sheets a plan needs to be built this way. One, when the usual single
        # call has already been cut off twice: then anything is better written in parts.
        self.least = MIN_SHEETS if least is None else least
        self.task = task
        self.target = target  # the file as it stands now (reopened if an edit died on it)
        self.put = put
        self._ask = ask
        self.system = system
        self.name = name
        self.context = context
        self.progress = progress
        self.cost = 0.0
        self.gate = asyncio.Semaphore(PARALLEL)
        self.writing = asyncio.Lock()  # one reply's edits go into the file at a time
        self.sheets: list[Sheet] = []
        self.count = 0
        self.late: list[str] = []  # why an edit of the last step didn't work

    async def ask(self, system: str, prompt: str) -> dict[str, Any] | None:
        """One call, a few at a time. None: the reply couldn't be read, or the call failed."""
        async with self.gate:
            began = time.perf_counter()
            try:
                text, cost = await self._ask(system, prompt)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("ledger build: a model call failed", exc_info=True)
                return None
            self.cost += cost
            data = read_json(text)
            log.info(
                "ledger build: a call took %.1fs, %d characters back%s",
                time.perf_counter() - began,
                len(text or ""),
                "" if data is not None else " (cut off or unreadable)",
            )
            return data

    async def say(self, line: str) -> None:
        if self.progress:
            try:
                await self.progress(line)
            except Exception:  # a progress line is never worth the job
                log.debug("ledger build: progress line dropped", exc_info=True)

    def brief(self) -> str:
        head = f"Context from your teammates:\n{self.context[:3000]}\n\n" if self.context else ""
        return f"{head}Task (the whole brief):\n{self.task}"

    def plan_text(self) -> str:
        return "\n".join(s.line(n) for n, s in enumerate(self.sheets, 1))

    # ------------------------------------------------------------------ the plan
    async def plan(self) -> list[Sheet] | None:
        """The sheets to build, or None when this is not a build to split (or no plan could
        be had): the job then goes the usual way, in one call."""
        target = await self.target()
        kind = getattr(target, "kind", "")
        try:
            there = await asyncio.to_thread(target.describe, 6)
        except Exception:
            there = ""
        system = PLANNER.replace("{name}", self.name)
        prompt = f"{self.brief()}\n\nThe file now:\n{there[:6000]}"
        await self.say("planning the sheets")
        for attempt in range(2):
            data = await self.ask(
                system,
                prompt
                if attempt == 0
                else prompt
                + "\n\nYour last reply could not be read: it was cut off or was not the JSON "
                "asked for. Again, shorter: for each sheet only its name, a purpose of five "
                "words, its columns and what it needs.",
            )
            sheets = read_plan(data, kind)
            if sheets is not None:
                return sheets if len(sheets) >= self.least else None
        return None

    # ------------------------------------------------------------------ the sheets
    async def make_tabs(self) -> None:
        """Every planned sheet, made empty and in order, so each can be written on its own
        and a formula can name any of them."""
        target = await self.target()
        have = {t.lower() for t in tabs_of(target)}
        blank = _blank_tab(target)
        for s in self.sheets:
            if s.name.lower() in have:
                continue
            if blank is not None:  # a new file's "Sheet1" becomes the first sheet of the plan
                why = await self.put({"op": "rename_sheet", "sheet": blank, "name": s.name})
                blank = None
            else:
                why = await self.put({"op": "add_sheet", "name": s.name})
            if why:
                s.state, s.why = "missing", why

    async def apply(self, sheet: Sheet, edits: list[Any]) -> int:
        """One reply's edits, in order, onto this sheet. Returns how many worked."""
        worked = 0
        async with self.writing:
            for e in edits:
                if not isinstance(e, dict) or not e.get("op"):
                    continue
                op = str(e["op"])
                if op in ("add_sheet", "delete_sheet", "rename_sheet", "duplicate_sheet"):
                    continue  # the tabs are made: a reply for one sheet doesn't reshape the file
                if "!" not in str(e.get("range") or ""):
                    e = {**e, "sheet": sheet.name}  # this reply is about this sheet, whatever it says
                why = await self.put(e)
                if why:
                    sheet.failed.append(f"{json.dumps(e, default=str)[:200]}: {why}")
                    sheet.reasons.append(why)
                else:
                    worked += 1
        sheet.edits += worked
        return worked

    async def look(self, names: list[str], rows: int = 30) -> str:
        """What is on these sheets now. Never while a reply's edits are going in: the file is
        read whole, between edits."""
        async with self.writing:
            return await layout(await self.target(), names, rows)

    async def sheet_prompt(self, sheet: Sheet, n: int) -> str:
        reads = await self.look(sheet.needs)
        mine = await self.look([sheet.name], 30)
        has_data = bool(mine.count("\n"))  # more than its one-line heading: it was there before
        return (
            f"{self.brief()}\n\nThe workbook is being built one sheet at a time. The plan:\n"
            f"{self.plan_text()}\n\nEvery sheet in the plan is already in the file"
            " (empty until it is written).\n\n"
            + (f"Sheets this one reads from, as they are now:\n{reads}\n\n" if reads else "")
            + (
                f"{sheet.name!r} is not empty. What is on it already:\n{mine}\n\nChange only "
                "what the brief asks for on this sheet: what is there stays, at the addresses "
                "it is at now.\n\n"
                if has_data
                else ""
            )
            + f"Now write ONLY the sheet {sheet.name!r} (number {n} of {len(self.sheets)}).\n"
            + _SHEET_RULES.format(name=sheet.name, part_rows=PART_ROWS)
        )

    async def build_sheet(self, sheet: Sheet, n: int, waits: dict[str, asyncio.Event]) -> None:
        try:
            for need in sheet.needs:  # the sheets it reads from are written first
                await waits[need].wait()
            if sheet.state == "missing":
                return  # its tab couldn't be made
            prompt = await self.sheet_prompt(sheet, n)
            data = await self.ask(self.system, prompt)
            more = False
            if data is not None:
                await self.apply(sheet, data.get("edits") if isinstance(data.get("edits"), list) else [])
                more = bool(data.get("more"))
            if data is None or more:
                # Too much for one reply (or it said so itself): this sheet, in parts.
                await self.in_parts(sheet, n, started=data is not None)
            elif sheet.edits:
                sheet.state = "built"
            else:
                sheet.state = "missing"
                sheet.why = sheet.why or (
                    f"None of the edits for {sheet.name} worked."
                    if sheet.failed
                    else f"No edits came back for {sheet.name}."
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one sheet's trouble is that sheet's, never the workbook's
            log.exception("ledger build: the sheet %s failed", sheet.name)
            sheet.state = "partly" if sheet.edits else "missing"
            sheet.why = sheet.why or f"{sheet.name} hit a problem ({str(exc)[:120]})."
        finally:
            waits[sheet.name].set()
            self.count += 1
            if sheet.state == "built":
                await self.say(f"built {sheet.name} ({self.count} of {len(self.sheets)})")

    async def in_parts(self, sheet: Sheet, n: int, *, started: bool) -> None:
        """A sheet too long for one reply, asked for a few rows at a time. Each part is put in
        the file before the next is asked for, and the next is shown what is there. A part
        that can't be read is asked for once more, smaller; if that fails too, what is in the
        sheet stays and the result says it is only partly there."""
        await self.say(f"{sheet.name} is long, so it goes in in parts")
        rows = PART_ROWS
        misses = 0
        for part in range(1, MAX_PARTS + 1):
            there = await self.look([sheet.name], 14)
            reads = await self.look(sheet.needs)
            first = part == 1 and not started and not sheet.edits
            prompt = (
                f"{self.brief()}\n\nThe workbook is being built one sheet at a time. The plan:\n"
                f"{self.plan_text()}\n\n"
                + (f"Sheets this one reads from, as they are now:\n{reads}\n\n" if reads else "")
                + f"The sheet {sheet.name!r} (number {n} of {len(self.sheets)}) is too long for one "
                "reply, so it is written in parts.\n"
                + (
                    f"This is the first part: the header row and the first {rows} rows only.\n"
                    if first
                    else f"What is on it so far:\n{there}\n\nSend the NEXT part only: the next "
                    f"{rows} rows at most, carrying on right under what is there, or, when the "
                    "rows are all in, its formatting. Do not send again what is already there.\n"
                )
                + _SHEET_RULES.format(name=sheet.name, part_rows=rows)
                + '\n"more" is true while anything of this sheet is still to come.'
            )
            data = await self.ask(self.system, prompt)
            if data is None:
                misses += 1
                if misses >= 2:
                    sheet.state = "partly" if sheet.edits else "missing"
                    sheet.why = (
                        f"The plan for {sheet.name} came back cut off, even in small parts."
                    )
                    return
                rows = max(5, rows // 2)  # asked again, smaller
                continue
            misses = 0
            worked = await self.apply(
                sheet, data.get("edits") if isinstance(data.get("edits"), list) else []
            )
            if not data.get("more"):
                sheet.state = "built" if sheet.edits else "missing"
                if not sheet.edits:
                    sheet.why = f"None of the edits for {sheet.name} worked."
                return
            if not worked:
                break  # it says there is more, and nothing it sent went in: stop asking
        sheet.state = "partly" if sheet.edits else "missing"
        sheet.why = sheet.why or f"{sheet.name} was longer than I could write in {MAX_PARTS} parts."

    # ------------------------------------------------------------------ the last step
    async def finish(self) -> bool:
        """What belongs to the workbook as a whole: formulas across sheets that are still
        missing, summary totals, charts, and another go at the edits that failed. True when
        it went in (or there was nothing to add)."""
        built = [s for s in self.sheets if s.state in ("built", "partly")]
        if not built:
            return True
        await self.say("tying the sheets together")
        there = await self.look([s.name for s in built], 9)
        failed = [f for s in self.sheets for f in s.failed][:12]
        prompt = (
            f"{self.brief()}\n\nThe workbook was built one sheet at a time. The plan:\n"
            f"{self.plan_text()}\n\nThe sheets as they are now (the top and bottom rows of each):\n"
            f"{there[:24000]}\n\n"
            + ("These edits failed and are not in the file:\n" + "\n".join(failed) + "\n\n" if failed else "")
            + "This is the last step. Send ONLY what is still missing for the workbook as a whole: "
            "formulas that read across sheets and are not there yet, the totals of a summary or "
            "dashboard sheet, the charts the brief asks for, and a corrected version of any failed "
            "edit that still matters. Name the sheet on every edit, use the real addresses shown "
            "above, and never add, rename or delete a sheet or write again what is already there. "
            "Thirty edits at most, the ones that matter most first. "
            'Return JSON only: {"edits": [..]}; {"edits": []} when nothing is missing.'
        )
        for attempt in range(2):
            data = await self.ask(
                self.system,
                prompt
                if attempt == 0
                else prompt
                + "\n\nYour last reply was cut off. Again, with the 15 most important edits only.",
            )
            if data is None:
                continue
            edits = data.get("edits") if isinstance(data.get("edits"), list) else []
            async with self.writing:
                for e in edits:
                    if not isinstance(e, dict) or not e.get("op"):
                        continue
                    if str(e["op"]) in ("add_sheet", "delete_sheet", "rename_sheet"):
                        continue
                    why = await self.put(e)
                    if why:
                        self.late.append(why)
            return True
        return False

    async def run(self) -> Built | None:
        """Plan, build, finish. None when the job is not one to split: nothing was changed,
        and the caller does it the usual way."""
        plan = await self.plan()
        if plan is None:
            return None
        self.sheets = plan
        await self.say(f"building {len(plan)} sheets")
        await self.make_tabs()
        waits = {s.name: asyncio.Event() for s in plan}
        tasks = [
            asyncio.ensure_future(self.build_sheet(s, n, waits)) for n, s in enumerate(plan, 1)
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            raise
        finished = await self.finish()
        problems = [s.why for s in plan if s.why and s.state != "built"]
        problems += [why for s in plan if s.state == "built" for why in s.reasons[:2]]
        problems += self.late
        return Built(
            sheets=plan,
            cost_usd=self.cost,
            problems=list(dict.fromkeys(problems)),
            finished=finished,
        )
