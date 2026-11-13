"""What the hands learn about an app or a site is kept for the next task there.

Every task started from nothing: where a dialog hides a choice, that a menu needs a second
click. After a task the hands write down at most two notes about how the place works (never
about the person or what they did), and read them before the next task in the same place.
The names here are made up.
"""

from __future__ import annotations

import asyncio
import json
import time

from otto.types import LLMResponse, ToolCall, Usage
from PIL import Image

from pyxi.companion.learned import PER_PLACE, Learned, alike, fit_to_keep, place_of
from pyxi.companion.operator import Operator
from pyxi.screen.context import ScreenContext, UIElement, WindowInfo
from test_operator import PretendHands


# ------------------------------------------------------------------ the store
def test_a_place_is_the_site_in_a_browser_and_the_app_elsewhere():
    assert place_of("EXCEL.EXE") == "app:excel.exe"
    assert place_of("chrome.exe", "https://www.sheets.example.com/d/abc?x=1") == "site:sheets.example.com"
    assert place_of("firefox.exe", "docs.example.org/edit") == "site:docs.example.org"
    assert place_of("chrome.exe", None) == "app:chrome.exe"  # a new tab, the browser's own pages
    assert place_of("chrome.exe", "about:blank") == "app:chrome.exe"
    assert place_of("excel.exe", "https://example.com") == "app:excel.exe"  # only a browser has a site
    assert place_of("") == ""


def test_only_notes_about_the_place_are_kept_never_about_the_person():
    assert fit_to_keep("  - The PivotTable button is on the Insert tab.  ") == "The PivotTable button is on the Insert tab."
    for personal in (
        "Send the report to sam@example.com from the File menu.",
        "The file budget-2026.xlsx opens from Recent.",
        "Their account number 12345678 goes in the first box.",
        "Open C:\\Users\\Sam\\Desktop first.",
        "Go to https://example.com/login and press Enter.",
        "The key sk-live-abcdefghijklmnopqrstuv goes in Settings.",
    ):
        assert fit_to_keep(personal) is None, personal
    # Their own words from the request, in quotes, are theirs.
    assert fit_to_keep("Type Quarterly review in the title box first.", task='make a slide titled "Quarterly review"') is None
    assert fit_to_keep("Type the title in the title box first.", task='make a slide titled "Quarterly review"')
    assert fit_to_keep("Too short") is None and fit_to_keep("word " * 40) is None and fit_to_keep("") is None


def test_notes_are_kept_by_place_and_the_same_thing_said_twice_is_one_note(tmp_path):
    store = Learned(tmp_path / "learned.json")
    kept = store.add("app:excel.exe", ["The PivotTable button is on the Insert tab.", "Press Enter to confirm the pivot dialog."])
    assert len(kept) == 2
    assert store.add("app:excel.exe", ["PivotTable button is on the insert tab"]) == []  # the same note again
    assert store.recall("app:excel.exe")[0] == "The PivotTable button is on the Insert tab."  # seen twice: first
    assert store.recall("app:winword.exe") == [] and store.recall("") == []
    assert alike("The search box is behind the magnifier icon.", "search box is behind the magnifier") and not alike("Press Enter to confirm.", "The Insert tab holds charts.")
    # It is on disk, readable, and a new start reads it back.
    data = json.loads((tmp_path / "learned.json").read_text(encoding="utf-8"))
    assert data["app:excel.exe"][0]["seen"] == 2
    assert Learned(tmp_path / "learned.json").recall("app:excel.exe") == store.recall("app:excel.exe")
    assert store.places() == {"app:excel.exe": 2}


def test_a_place_keeps_a_few_notes_and_the_proven_ones_stay(tmp_path):
    store = Learned(tmp_path / "learned.json")
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
    first = store.add("app:x.exe", [f"The {words[0]} panel opens from the {words[0]} menu."])
    store.used("app:x.exe", first, ok=True)  # it was there when a task went well
    for w in words[1:]:
        store.add("app:x.exe", [f"The {w} panel opens from the {w} menu."])
    notes = store.recall("app:x.exe")
    assert len(notes) == PER_PLACE and notes[0] == first[0]


def test_a_note_that_keeps_failing_or_is_called_wrong_goes(tmp_path):
    store = Learned(tmp_path / "learned.json")
    note = store.add("site:shop.example.com", ["The basket is behind the bag icon at the top right."])
    store.used("site:shop.example.com", note, ok=False)
    assert store.recall("site:shop.example.com") == note  # once is not proof
    store.used("site:shop.example.com", note, ok=False)
    assert store.recall("site:shop.example.com") == [] and store.places() == {}
    # One that helped twice survives two failures.
    note = store.add("site:shop.example.com", ["The basket is behind the bag icon at the top right."])
    for ok in (True, True, False, False):
        store.used("site:shop.example.com", note, ok=ok)
    assert store.recall("site:shop.example.com") == note
    assert store.drop("site:shop.example.com", ["the basket is behind the bag icon at top right"]) == 1  # called wrong, in other words
    assert store.recall("site:shop.example.com") == []
    store.add("app:a.exe", ["The Save button is in the File menu."])
    store.forget()
    assert store.places() == {}


def test_a_broken_file_is_an_empty_start_not_a_crash(tmp_path):
    (tmp_path / "learned.json").write_text("{not json", encoding="utf-8")
    store = Learned(tmp_path / "learned.json")
    assert store.recall("app:excel.exe") == []
    assert store.add("app:excel.exe", ["The PivotTable button is on the Insert tab."])


# ------------------------------------------------------------------ in the hands
def sheet(color: str) -> ScreenContext:
    ctx = ScreenContext(
        time.time(),
        WindowInfo("Book1", "EXCEL.EXE", (0, 0, 1366, 768)),
        [UIElement(1, "Insert", "TabItem", (100, 40, 60, 24)), UIElement(2, "PivotTable", "Button", (20, 80, 80, 60))],
        Image.new("RGB", (1366, 768), color),
    )
    ctx.model_scale = 1.0
    return ctx


class Model:
    """Answers the hands' looks from a script, and the question afterwards with `notes`."""

    def __init__(self, *calls: tuple[str, dict], notes: dict | None = None) -> None:
        self.calls, self.notes, self.seen, self.asked_notes = list(calls), notes, [], []

    async def complete(self, *, system, messages, tools, **_):
        text = "".join(getattr(p, "text", "") for p in messages[-1].parts)
        if tools[0].name == "notes":
            self.asked_notes.append(text)
            call = ToolCall(id="n", name="notes", arguments=self.notes) if self.notes is not None else None
            return LLMResponse(text="", tool_calls=[call] if call else [], usage=Usage())
        self.seen.append(text)
        name, args = self.calls.pop(0) if self.calls else ("done", {"ok": True, "summary": "Done."})
        return LLMResponse(text="", tool_calls=[ToolCall(id="c", name=name, arguments=args)], usage=Usage())


def hands(llm, store) -> Operator:
    colors = iter(["white", "black", "red", "blue", "green", "gray"] * 4)

    async def cap():
        return sheet(next(colors))

    async def yes(action, reason):
        return True

    async def report(doing, n):
        pass

    return Operator(llm, cap, yes, report, make_hands=PretendHands, aim_llm=llm, learned=store)


TWO_STEPS = (
    ("act", {"actions": [{"do": "click", "element_id": 1}]}),
    ("act", {"actions": [{"do": "click", "element_id": 2}]}),
    ("done", {"ok": True, "summary": "The pivot is made."}),
)


async def settle(op: Operator) -> None:
    await asyncio.gather(*op.learning)


async def test_after_a_task_notes_are_written_and_the_next_task_there_reads_them(tmp_path):
    store = Learned(tmp_path / "learned.json")
    llm = Model(*TWO_STEPS, notes={"keep": ["The PivotTable button is on the Insert tab, at its far left."]})
    op = hands(llm, store)
    out = await op.run("make a pivot table of sales by region")
    assert out.ok
    await settle(op)
    assert "known" not in llm.seen[0] and "found out here" not in llm.seen[0]  # nothing known the first time
    asked = llm.asked_notes[0]
    assert "Place: app:excel.exe" in asked and "1. " in asked and "How it ended: done" in asked and "(none)" in asked
    assert store.recall("app:excel.exe") == ["The PivotTable button is on the Insert tab, at its far left."]

    again = Model(*TWO_STEPS, notes={"keep": []})
    op = hands(again, store)
    await op.run("make a pivot table of costs by month")
    await settle(op)
    assert all("- The PivotTable button is on the Insert tab, at its far left." in look for look in again.seen)
    assert "trust the screen" in again.seen[0]
    assert "- The PivotTable button is on the Insert tab" in again.asked_notes[0]  # told what it already had
    assert json.loads((tmp_path / "learned.json").read_text(encoding="utf-8"))["app:excel.exe"][0]["helped"] == 1


async def test_a_note_found_wrong_is_dropped_and_personal_ones_never_kept(tmp_path):
    store = Learned(tmp_path / "learned.json")
    store.add("app:excel.exe", ["The PivotTable button is on the Data tab."])
    llm = Model(*TWO_STEPS, notes={
        "wrong": ["The PivotTable button is on the Data tab."],
        "keep": ["The PivotTable button is on the Insert tab.", "The workbook sales-figures.xlsx has a sheet for each region."],
    })
    op = hands(llm, store)
    await op.run("make a pivot table")
    await settle(op)
    assert store.recall("app:excel.exe") == ["The PivotTable button is on the Insert tab."]


async def test_nothing_is_written_for_a_one_step_task_a_stopped_one_or_when_it_is_off(tmp_path):
    store = Learned(tmp_path / "learned.json")
    llm = Model(("done", {"ok": True, "summary": "Already done."}), notes={"keep": ["The Insert tab holds the PivotTable button."]})
    op = hands(llm, store)
    await op.run("is the pivot there?")
    await settle(op)
    assert llm.asked_notes == [] and store.places() == {}
    # Off: the hands work exactly as before, and nothing is asked afterwards.
    llm = Model(*TWO_STEPS, notes={"keep": ["The Insert tab holds the PivotTable button."]})
    op = hands(llm, None)
    assert (await op.run("make a pivot table")).ok
    assert not op.learning and llm.asked_notes == []


async def test_a_failed_write_up_never_fails_the_task(tmp_path):
    class Broken(Model):
        async def complete(self, *, system, messages, tools, **kw):
            if tools[0].name == "notes":
                raise RuntimeError("down")
            return await super().complete(system=system, messages=messages, tools=tools, **kw)

    store = Learned(tmp_path / "learned.json")
    op = hands(Broken(*TWO_STEPS), store)
    out = await op.run("make a pivot table")
    await settle(op)
    assert out.ok and store.places() == {}
