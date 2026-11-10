"""Pyxi in the tab on screen, with no browser extension (pyxi/screenlink.py).

No browser and no UI Automation here: the link is given a pretend driver with an
accessibility tree made by hand, shaped like the ones Chrome and Firefox really give
(scripts/screen_tab_check.py is the real-browser version, with the measurements). What is
pinned: the page comes back in the shape the extension gives; refs last as long as the page
does; an action is only ever done to the page and the element that were read; every refusal
this layer makes itself (private fields, Pyxi's own windows, anything outside the document);
nothing is done by mouse or keyboard; a key is only posted to the window that was read;
stop and Esc end an action in flight; and the extension is used whenever one is connected.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from pyxi import screenlink
from pyxi.browserlink import BrowserError
from pyxi.companion import prompts, tabwork
from pyxi.screenlink import Doc, El, ScreenLink, Snapshot, Window, private_kind
from test_browserlink import Cards, PretendBrowser, Scripted, _brain, act, logged, work

ROOT = Path(__file__).resolve().parents[1]
HWND = 100
URL = "https://mail.example.com/compose"
SITE = "mail.example.com"


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(tabwork, "SETTLE_S", 0.0)


def P(*names: str) -> frozenset[str]:
    return frozenset(names)


def text(words: str) -> El:
    return El("Text", name=words, aria="description")


def compose(rid: tuple[int, ...] = (1, 1), url: str = URL) -> El:
    """A page the way Chrome's tree gives one: a heading, a paragraph with a link, a form
    with ordinary and private fields, a search region, and a div that is only clickable."""
    press = P("Invoke", "LegacyIAccessible", "ScrollItem")
    field = P("Value", "LegacyIAccessible", "ScrollItem")
    box = (0, 0, 80, 20)
    options = [
        El("ListItem", name=name, aria="option", patterns=P("SelectionItem", "LegacyIAccessible"), rect=box)
        for name in ("Normal", "High", "Low")
    ]
    form = El(
        "Group",
        aria="form",
        children=[
            El("Edit", name="To", aria="textbox", patterns=field, rect=box, rid=(1, 10)),
            El("Edit", name="Message", aria="textbox", props={"multiline": "true"}, patterns=field, rect=box, rid=(1, 11)),
            El("Edit", name="Password", password=True, value="hunter2", patterns=field, rect=box, rid=(1, 12)),
            El("Edit", name="Card number", aria="textbox", patterns=field, rect=box, rid=(1, 13)),
            El("Edit", name="SIN", aria="textbox", patterns=field, rect=box, rid=(1, 14)),
            El("Button", name="Choose File: No file chosen", patterns=press | P("Value"), rect=box, rid=(1, 15)),
            El("CheckBox", name="Send me a copy", aria="checkbox", checked=False, patterns=P("Toggle", "LegacyIAccessible"), rid=(1, 16)),
            El(
                "ComboBox",
                name="Priority",
                aria="combobox",
                value="Normal",
                rect=box,
                rid=(1, 17),
                patterns=P("Value", "ExpandCollapse", "LegacyIAccessible"),
                children=[El("List", children=options)],
            ),
            El("Button", name="Send", aria="button", patterns=press, rect=box, rid=(1, 18), children=[text("Send")]),
        ],
    )
    search = El(
        "Group",
        aria="search",
        search_landmark=True,
        children=[
            El("Edit", name="Search mail", aria="searchbox", patterns=field, rect=box, rid=(1, 20)),
            El("Button", name="Go", aria="button", patterns=press, rect=box, rid=(1, 21), children=[text("Go")]),
        ],
    )
    root = El(
        "Document",
        name="Compose - Mail",
        aria="document",
        value=url,
        rid=rid,
        patterns=P("Value", "Scroll"),
        rect=(0, 0, 800, 600),
        children=[
            El("Text", name="Compose", aria="heading", children=[text("Compose")]),
            El(
                "Group",
                children=[
                    text("Write to "),
                    El("Hyperlink", name="anyone", aria="link", value="https://mail.example.com/help", patterns=press | P("Value"), rect=box, rid=(1, 30), children=[text("anyone")]),
                    text(". Card on file: 4111 1111 1111 1111"),
                ],
            ),
            form,
            search,
            El("Group", aria="generic", patterns=press, rect=box, rid=(1, 40), children=[text("Show more")]),
            El("Button", name="Does nothing", aria="button", patterns=P(), rect=box, rid=(1, 41)),
        ],
    )
    for el in _all(root):
        el.handle = el
    return root


def _all(root: El) -> list[El]:
    out, stack = [], [root]
    while stack:
        el = stack.pop()
        out.append(el)
        stack.extend(reversed(el.children))
    return out


def _path(root: El, target: El) -> list[El] | None:
    if root is target:
        return [root]
    for child in root.children:
        below = _path(child, target)
        if below is not None:
            return [root, *below]
    return None


def named(root: El, name: str) -> El:
    return next(el for el in _all(root) if el.name == name and el.role != "Text")


class FakeDriver(screenlink.Driver):
    """A PC with one browser window and one page, and a record of everything asked of it."""

    def __init__(self, exe: str = "chrome.exe") -> None:
        self.wins = [Window(HWND, "Compose - Mail - Google Chrome", exe)]
        self.front = 0
        self.mouse = 0
        self.own: set[int] = set()
        self.esc = False
        self.pages: dict[int, El] = {HWND: compose()}
        self.did: list[tuple[Any, ...]] = []
        self.hold: threading.Event | None = None  # do() waits for it
        self.slow_state: threading.Event | None = None  # state() waits for it
        self.refuse: set[str] = set()  # ways the element turns out not to offer
        self.deaf: set[str] = set()  # ways that are accepted and change nothing
        self.lazy_options = False  # Chrome: a dropdown lists its options only while open
        self.open_now: set[int] = set()
        self.cursor_outside = False  # the keyboard is in the address bar, not in the page
        self.lands: Any = None  # open(): the document that turns up, or None for none at all
        self.asked: list[tuple[int, float]] = []  # (max_nodes, budget_s) of each snapshot
        self.y = 0.0

    @property
    def root(self) -> El:
        return self.pages[HWND]

    def available(self) -> bool:
        return True

    def windows(self) -> list[Window]:
        return list(self.wins)

    def foreground(self) -> int:
        return self.front

    def under_mouse(self) -> int:
        return self.mouse

    def is_own(self, hwnd: int) -> bool:
        return hwnd in self.own

    def esc_down(self) -> bool:
        return self.esc

    def _doc(self, hwnd: int) -> Doc | None:
        root = self.pages.get(hwnd)
        return Doc(root.rid, root.value, root.name, False, root) if root else None

    def document(self, hwnd: int) -> Doc | None:
        return self._doc(hwnd)

    def snapshot(self, hwnd: int, max_nodes: int, budget_s: float) -> Snapshot | None:
        self.asked.append((max_nodes, budget_s))
        doc = self._doc(hwnd)
        return Snapshot(doc, self.pages[hwnd], (self.y, 30.0)) if doc else None

    def state(self, handle: Any, doc: tuple[int, ...]) -> El | None:
        if self.slow_state is not None:
            self.slow_state.wait(5)
        for root in self.pages.values():
            if root.rid == doc and doc:
                path = _path(root, handle)
                if path is not None:
                    frames = tuple(el.value for el in path[1:-1] if el.role == "Document")
                    return dataclasses.replace(handle, children=[], frames=frames)
        return None

    def focused(self, doc: tuple[int, ...]) -> El | None:
        if self.cursor_outside:
            return None
        for root in self.pages.values():
            for el in _all(root):
                if el.focused:
                    return self.state(el, doc)
        return None

    def inner_text(self, handle: Any) -> str:
        return screenlink._inner_text(handle)

    def do(self, handle: Any, way: str, text: str = "") -> None:
        if self.hold is not None:
            self.hold.wait(5)
        if way in self.refuse:
            raise RuntimeError("the element doesn't offer that")
        self.did.append((way, handle.name, text))
        if way in self.deaf:
            return
        if way in ("value", "legacy_value"):
            handle.value = text
        elif way == "toggle" or (way in ("legacy", "invoke") and handle.role == "CheckBox"):
            handle.checked = not handle.checked
        elif way == "select" or (way == "legacy" and handle.role == "ListItem"):
            handle.checked = True
            for el in _all(self.root):
                if el.role == "ComboBox" and any(o is handle for o in _all(el)):
                    el.value = handle.name
        elif way in ("focus", "legacy_focus"):
            for el in _all(self.root):
                el.focused = el is handle
        elif way == "expand":
            self.open_now.add(id(handle))
        elif way == "collapse":
            self.open_now.discard(id(handle))

    def options(self, handle: Any) -> list[El]:
        if self.lazy_options and id(handle) not in self.open_now:
            return []
        return screenlink._options_of(handle)

    def has_children(self, handle: Any) -> bool:
        return bool(handle.children)

    def scroll(self, doc: Doc, direction: str) -> bool | None:
        self.did.append(("scroll", direction))
        self.y = {"down": self.y + 30, "up": max(0.0, self.y - 30), "top": 0.0, "bottom": 100.0}[direction]
        return True

    def scroll_state(self, doc: Doc) -> tuple[float, float] | None:
        return (self.y, 30.0)

    def post_key(self, hwnd: int, key: str) -> None:
        self.did.append(("key", hwnd, key))
        box = next((el for el in _all(self.root) if el.focused and el.role == "ComboBox"), None)
        if box is not None and key in ("ArrowDown", "ArrowUp"):  # what Firefox does with an arrow
            names = [o.name for o in screenlink._options_of(box)]
            at = names.index(box.value) + (1 if key == "ArrowDown" else -1)
            box.value = names[max(0, min(len(names) - 1, at))]

    def post_back(self, hwnd: int) -> None:
        self.did.append(("back", hwnd))

    def open(self, hwnd: int, url: str) -> None:
        self.did.append(("open", hwnd, url))
        if self.lands is None:
            self.pages[hwnd] = compose(rid=(9, 9), url=url)
        elif self.lands:
            self.pages[hwnd] = self.lands

    def acted(self) -> list[tuple[Any, ...]]:
        return [d for d in self.did if d[0] not in ("focus", "legacy_focus")]


def make(exe: str = "chrome.exe", **kw: Any) -> tuple[ScreenLink, FakeDriver]:
    driver = FakeDriver(exe)
    link = ScreenLink(driver, settle_s=0.2, **kw)
    link.open_wait_s = 2.5
    return link, driver


async def read(link: ScreenLink, **more: Any) -> dict[str, Any]:
    return await link.call("read", {"tab": HWND, **more})


def item(page: dict[str, Any], label: str) -> dict[str, Any]:
    return next(i for i in page["items"] if i["label"] == label)


async def do(link: ScreenLink, name: str, page: dict[str, Any], label: str = "", **args: Any) -> dict[str, Any]:
    """An action, sent the way tabwork's operator sends it: with the page, the site and the
    element's words as they were read."""
    seen: dict[str, Any] = {"tab": HWND, "page": page["page"], "host": SITE}
    if label:
        it = item(page, label)
        seen.update(ref=it["ref"], label=it["label"])
    return await link.call(name, {**seen, **args})


async def refused(call: Any) -> BrowserError:
    with pytest.raises(BrowserError) as caught:
        await call
    return caught.value


# ====================================================================== finding the tab
def test_it_works_in_the_browser_in_front_then_under_the_mouse_then_the_nearest():
    link, driver = make()
    driver.wins = [
        Window(100, "Compose - Mail - Google Chrome", "chrome.exe"),
        Window(200, "News - Mozilla Firefox", "firefox.exe"),
        Window(300, "Docs - Microsoft Edge", "msedge.exe", minimised=True),
    ]
    assert link.pick() == "w100"  # the one nearest the top
    driver.mouse = 200
    assert link.pick() == "w200"
    driver.front = 100
    assert link.pick() == "w100"  # in front wins over under the mouse
    driver.front = driver.mouse = 0
    assert link.pick("firefox") == "w200"
    driver.front = 300
    assert link.pick() == "w100"  # a minimised window is never picked
    driver.own = {100, 200}
    assert link.pick() == ""  # Pyxi's own windows don't count, whatever program they are
    driver.wins = []
    assert link.pick() == "" and not link.browsers()


async def test_only_the_tab_that_is_showing_and_it_says_so():
    link, _ = make()
    tab = await link.call("peek", {})
    assert tab == {"id": HWND, "title": "Compose - Mail", "url": URL, "active": True, "window": HWND}
    assert (await link.call("peek", {"tab": "compose mail"}))["id"] == HWND  # named, and showing
    for name in ("peek", "switch"):
        no = await refused(link.call(name, {"tab": "inbox"}))
        assert no.code == "failed" and "only the tab that is showing" in str(no)
    listing = await link.call("tabs", {})
    assert [t["id"] for t in listing["tabs"]] == [HWND]  # other tabs are not offered
    assert "Only that tab" in screenlink.NOTE and "only the tab that is showing" in screenlink.PROMPT


async def test_a_minimised_window_or_a_page_that_gives_nothing_says_what_to_do():
    link, driver = make()
    driver.wins[0].minimised = True
    no = await refused(read(link))
    assert no.code == "failed" and "not minimised, not covered" in str(no)
    driver.wins[0].minimised = False
    driver.pages.clear()  # Chrome, with its window covered: no page to read
    no = await refused(read(link))
    assert no.code == "failed" and "showing on screen" in str(no)
    driver.wins = []
    assert (await refused(read(link))).code == "gone"


# ====================================================================== reading
async def test_a_page_comes_back_in_the_shape_the_extension_gives():
    link, _ = make()
    page = await read(link)
    assert set(page) == {"page", "text", "more_text", "items", "more_items", "scroll", "tab"}
    assert page["tab"] == {"id": HWND, "title": "Compose - Mail", "url": URL, "active": True, "window": HWND}
    assert page["text"].splitlines()[:2] == ["Compose", "Write to anyone. Card on file: [card number hidden]"]
    assert page["scroll"] == {"y": 0, "max": 100} and not page["more_items"]
    kinds = {i["label"]: i["kind"] for i in page["items"]}
    assert kinds == {
        "anyone": "link",
        "To": "text field",
        "Message": "text area",
        "Password": "password field",
        "Card number": "text field",
        "SIN": "text field",
        "Choose File: No file chosen": "file picker",
        "Send me a copy": "checkbox",
        "Priority": "dropdown",
        "Send": "button",
        "Search mail": "search box",
        "Go": "button",
        "Does nothing": "button",
        "Show more": "clickable",
    }
    assert all(re.fullmatch(r"e\d+", i["ref"]) for i in page["items"])
    assert item(page, "anyone")["href"] == "/help"
    assert item(page, "Send me a copy")["checked"] is False
    assert item(page, "Priority")["value"] == "Normal" and item(page, "Priority")["options"] == ["Normal", "High", "Low"]
    assert item(page, "Search mail")["search"] is True and "search" not in item(page, "To")
    # A button inside a form may submit it (the tree doesn't say which one does); one in a
    # search region doesn't count.
    assert item(page, "Send")["submits"] is True and "submits" not in item(page, "Go")
    assert {i["label"]: i["sensitive"] for i in page["items"] if i.get("sensitive")} == {
        "Password": "password",
        "Card number": "card",
        "SIN": "id",
        "Choose File: No file chosen": "file",
    }
    assert "hunter2" not in json.dumps(page) and "4111" not in json.dumps(page)
    shown = tabwork.render(page)  # and tabwork renders it like any page
    assert '[{}] button "Send" (submits the form)'.format(item(page, "Send")["ref"]) in shown
    assert 'password field "Password" (a password field: theirs to fill, never yours)' in shown


async def test_what_is_only_clickable_is_listed_once_and_labels_are_not():
    link, driver = make("firefox.exe")
    press = P("Invoke", "LegacyIAccessible")
    # Firefox: a label is a clickable group around its field, and each word in it is too.
    label = El(
        "Group", name="Nickname", patterns=press, rect=(0, 0, 9, 9), rid=(1, 50),
        children=[El("Text", name="Nickname ", patterns=press), El("Edit", name="Nickname", patterns=P("Value"), rect=(0, 0, 9, 9), rid=(1, 51))],
    )  # fmt: skip
    loose = El("Group", name="Nickname", patterns=press, rect=(0, 0, 9, 9), rid=(1, 52), children=[El("Text", name="Nickname", patterns=press)])
    outer = El(
        "Group", patterns=press, rect=(0, 0, 9, 9), rid=(1, 53),
        children=[El("Group", patterns=press, rect=(0, 0, 9, 9), rid=(1, 54), children=[text("Open the menu")])],
    )  # fmt: skip
    hidden = El("Button", name="No size", patterns=press, rect=(0, 0, 0, 0), rid=(1, 55))
    driver.root.children += [label, loose, outer, hidden]
    for el in _all(driver.root):
        el.handle = el
    page = await read(link)
    got = [(i["kind"], i["label"]) for i in page["items"] if i["label"] in ("Nickname", "Open the menu", "No size")]
    assert got == [("text field", "Nickname"), ("clickable", "Open the menu")]


async def test_refs_last_as_long_as_the_page_does():
    link, driver = make()
    first = await read(link)
    again = await read(link)
    assert again["page"] == first["page"]
    assert [i["ref"] for i in again["items"]] == [i["ref"] for i in first["items"]]
    driver.pages[HWND] = compose(rid=(2, 1))  # another document, same address
    new = await read(link)
    assert new["page"] != first["page"]
    assert not {i["ref"] for i in new["items"]} & {i["ref"] for i in first["items"]}
    # The id can't be worked out from outside: another link gives the same page another one.
    other, _ = make()
    assert (await read(other))["page"] != first["page"]


async def test_the_browsers_own_pages_are_not_read():
    link, driver = make()
    driver.pages[HWND] = compose(url="chrome://settings/")
    assert (await refused(read(link))).code == "browser_page"
    tab = await link.call("peek", {})  # tabwork's guard still sees what it is
    assert tab["url"] == "chrome://settings/"


# ====================================================================== acting, without the mouse
@pytest.mark.parametrize("exe", ["chrome.exe", "msedge.exe", "firefox.exe"])
async def test_it_presses_ticks_and_types_with_the_elements_own_actions(exe):
    # The older default action first (measured: it leaves the browser window where it is).
    pressed, ticked, typed = "legacy", "legacy", "legacy_value"
    link, driver = make(exe)
    page = await read(link)
    out = await do(link, "click", page, "Go", settle=True)
    assert out["tab"]["url"] == URL
    await do(link, "click", page, "Send me a copy")
    out = await do(link, "type", page, "To", text="dana@example.com")
    assert out["value"] == "dana@example.com"
    await do(link, "click", page, "Show more")
    await do(link, "click", page, "anyone")
    assert driver.did == [
        (pressed, "Go", ""),
        (ticked, "Send me a copy", ""),
        (typed, "To", "dana@example.com"),
        (pressed, "", ""),
        (pressed, "anyone", ""),
    ]
    assert named(driver.root, "Send me a copy").checked is True
    out = await do(link, "select", page, "Send me a copy", option="off")
    assert out["checked"] is False


async def test_when_one_way_is_not_taken_the_next_is_tried_and_the_field_is_read_back():
    link, driver = make()
    page = await read(link)
    driver.refuse = {"legacy"}
    await do(link, "click", page, "Go")
    assert driver.did == [("invoke", "Go", "")]
    driver.did.clear()
    driver.deaf = {"legacy_value"}  # accepted, and the field doesn't change
    out = await do(link, "type", page, "To", text="dana@example.com")
    assert [d[0] for d in driver.did] == ["legacy_value", "value"] and out["value"] == "dana@example.com"
    driver.deaf = {"value", "legacy_value"}  # a page that takes no set value at all
    out = await do(link, "type", page, "Message", text="Hello")
    assert out["value"] == ""  # reported as it is: tabwork tells the model it didn't go in


async def test_an_element_with_no_action_of_its_own_is_not_done_some_other_way():
    link, driver = make()
    page = await read(link)
    no = await refused(do(link, "click", page, "Does nothing"))
    assert no.code == "failed" and "can't be done without the mouse" in str(no) and driver.did == []
    driver.refuse = {"invoke", "legacy"}
    assert (await refused(do(link, "click", page, "Go"))).code == "failed"
    rich = El("Group", name="Message body", props={"multiline": "true"}, rect=(0, 0, 9, 9), rid=(1, 60))
    rich.handle = rich
    driver.root.children.append(rich)
    page = await read(link)
    assert item(page, "Message body")["kind"] == "editable text"
    no = await refused(do(link, "type", page, "Message body", text="Hi"))
    assert no.code == "failed" and "rich text box" in str(no) and "Settings, Connect your browser" in str(no)
    # Nothing here ever drives the real mouse or keyboard.
    source = (ROOT / "src" / "pyxi" / "screenlink.py").read_text(encoding="utf-8")
    assert not re.search(r"SendInput|mouse_event|keybd_event|SetCursorPos|Hands\(|pyautogui", source)


def _mail_body(driver: FakeDriver, name: str, words: str = "") -> El:
    """A contenteditable with the textbox role: to the tree, a text area."""
    box = El(
        "Edit",
        name=name,
        aria="textbox",
        props={"multiline": "true"},
        patterns=P("Value", "LegacyIAccessible"),
        rect=(0, 0, 9, 9),
        rid=(1, 60 + len(driver.root.children)),
        children=[text(words)] if words else [],
    )
    driver.root.children.append(box)
    for el in _all(driver.root):
        el.handle = el
    return box


async def test_in_chrome_a_rich_text_box_is_not_taken_for_a_field_that_took_the_text():
    link, driver = make()
    _mail_body(driver, "Message body", "Dear Dana")
    _mail_body(driver, "Reply")
    page = await read(link)
    # Its text is elements inside it, which a real field never has: refused before anything.
    no = await refused(do(link, "type", page, "Message body", text="Hi"))
    assert no.code == "failed" and "rich text box" in str(no) and driver.did == []
    real = driver.do

    def chrome(handle: Any, way: str, words: str = "") -> None:
        real(handle, way, words)
        if handle.name == "Reply":  # measured: the words go in as an element, the page isn't told
            handle.children = [text(words)]

    driver.do = chrome  # type: ignore[method-assign]
    no = await refused(do(link, "type", page, "Reply", text="Hi"))  # empty, so it looked like a field
    assert no.code == "failed" and "the page was not told" in str(no) and "Don't try again" in str(no)
    out = await do(link, "type", page, "To", text="dana@example.com")  # a real field is not affected
    assert out["value"] == "dana@example.com"
    fox, driver2 = make("firefox.exe")  # Firefox tells the page (measured), so there it is typed
    _mail_body(driver2, "Message body", "Dear Dana")
    page = await read(fox)
    assert (await do(fox, "type", page, "Message body", text="Hi"))["value"] == "Hi"


@pytest.mark.parametrize("lazy", [False, True])
async def test_a_dropdown_is_set_by_choosing_its_option(lazy):
    link, driver = make()
    driver.lazy_options = lazy  # Chrome lists the options only while the dropdown is open
    page = await read(link)
    out = await do(link, "select", page, "Priority", option="hi")
    assert out["value"] == "High" and named(driver.root, "Priority").value == "High"
    ways = [d[0] for d in driver.did]
    assert ways == (["expand", "legacy", "collapse"] if lazy else ["legacy"])
    no = await refused(do(link, "select", page, "Priority", option="Urgent"))
    assert "The options are: Normal, High, Low" in str(no)
    assert "isn't a plain dropdown" in str(await refused(do(link, "select", page, "Go", option="x")))


async def test_in_firefox_a_dropdown_is_set_with_the_arrow_keys_when_choosing_is_not_heard():
    link, driver = make("firefox.exe")
    driver.deaf = {"legacy", "select"}  # measured: Firefox takes the choice and tells the page nothing
    page = await read(link)
    out = await do(link, "select", page, "Priority", option="Low")
    assert out["value"] == "Low"
    assert [d for d in driver.did if d[0] == "key"] == [("key", HWND, "ArrowDown"), ("key", HWND, "ArrowDown")]
    link.keys = False  # with keys switched off it says so instead
    driver.did.clear()
    assert "can't be set this way" in str(await refused(do(link, "select", page, "Priority", option="Normal")))
    assert not [d for d in driver.did if d[0] == "key"]


async def test_scrolling_and_going_back():
    link, driver = make()
    page = await read(link)
    out = await do(link, "scroll", page, direction="down")
    assert out["moved"] is True and out["y"] == 30 and out["max"] == 100
    out = await link.call("scroll", {"tab": HWND, "direction": "top"})  # no page needed to scroll
    assert out["y"] == 0
    await do(link, "scroll", page, "Send")
    assert driver.did[-1] == ("into_view", "Send", "")
    await do(link, "press", page, key="PageDown")  # a page key is a scroll, not a key
    assert driver.did[-1] == ("scroll", "down")
    await do(link, "back", page)
    assert driver.did[-1] == ("back", HWND)


# ====================================================================== tied to what was read
async def test_an_action_is_for_the_page_and_the_element_that_were_read():
    link, driver = make()
    page = await read(link)
    send = item(page, "Send")
    base = {"tab": HWND, "ref": send["ref"], "label": "Send"}
    cases = [
        base,  # no page named: it has to have been read
        {**base, "page": "0" * 24},  # some other page
        {**base, "page": page["page"], "host": "evil.example.com"},  # another site
        {**base, "page": page["page"], "host": SITE, "label": "Save draft"},  # other words
        {**base, "page": page["page"], "host": SITE, "ref": "e999"},  # no such element
    ]
    for args in cases:
        assert (await refused(link.call("click", args))).code == "gone", args
    named(driver.root, "Send").name = "Delete everything"  # the page gave the button new words
    assert (await refused(do(link, "click", page, "Send"))).code == "gone"
    named(driver.root, "Delete everything").name = "Send"
    driver.root.children[2].children.pop()  # the page took the button away
    assert (await refused(do(link, "click", page, "Send"))).code == "gone"
    driver.pages[HWND] = compose(rid=(2, 1))  # another document in the tab, same address
    for label in ("Go", "To"):
        assert (await refused(do(link, "click" if label == "Go" else "type", page, label, text="x"))).code == "gone"
    assert (await refused(do(link, "back", page))).code == "gone"
    assert driver.did == []


async def test_nothing_outside_the_document_can_be_acted_on():
    link, driver = make()
    page = await read(link)
    # The browser's own address bar: it is not in the document, so it has no ref. Even if a
    # ref pointed at it, it is not inside the page that was read, and nothing is done.
    bar = El("Edit", name="Address and search bar", patterns=P("Value", "LegacyIAccessible"), rect=(0, 0, 9, 9))
    bar.handle = bar
    link._reads[HWND].refs["e500"] = (bar, {"kind": "text field", "label": "Address and search bar"})
    args = {"tab": HWND, "page": page["page"], "host": SITE, "ref": "e500", "label": "Address and search bar"}
    assert (await refused(link.call("type", {**args, "text": "https://evil.example.com"}))).code == "gone"
    assert (await refused(link.call("click", args))).code == "gone"
    assert driver.did == [] and bar.value == ""
    assert "Address and search bar" not in json.dumps(page)


async def test_pyxis_own_windows_and_windows_that_are_not_browsers_are_never_touched():
    link, driver = make()
    page = await read(link)
    driver.own = {HWND}  # the window turns out to be one of Pyxi's
    for call in (read(link), do(link, "click", page, "Go"), do(link, "type", page, "To", text="x")):
        no = await refused(call)
        assert no.code == "browser_page" and "Pyxi's own windows" in str(no)
    driver.own = set()
    no = await refused(link.call("click", {"tab": 4242, "page": page["page"], "ref": "e1"}))  # not a browser window
    assert no.code == "gone" and driver.did == []


# ====================================================================== private fields
@pytest.mark.parametrize(
    "el, kind",
    [
        (El("Edit", name="Anything", password=True), "password"),
        (El("Edit", name="Password"), "password"),
        (El("Edit", name="Passwort"), "password"),
        (El("Edit", name="Code", auto_id="otpCode"), "password"),
        (El("Edit", name="Enter your PIN"), "password"),
        (El("Edit", name="Card number"), "card"),
        (El("Edit", name="Number", auto_id="ccnum"), "card"),
        (El("Edit", name="Security code"), "card"),
        (El("Edit", name="CVV"), "card"),
        (El("Edit", name="Expiry date"), "card"),
        (El("Edit", name="", help="MM / YY", auto_id="card-expiry"), "card"),
        (El("Edit", name="IBAN"), "card"),
        (El("Edit", name="Kartennummer"), "card"),
        (El("Edit", name="Social insurance number"), "id"),
        (El("Edit", name="SSN"), "id"),
        (El("Edit", name="Passport number"), "id"),
        (El("Spinner", name="Driver's licence number"), "id"),
        (El("Button", name="Choose File: No file chosen", patterns=P("Invoke", "Value")), "file"),
        (El("Button", name="Browse..."), "file"),
        (El("Edit", name="Business name"), ""),  # "ssn" is inside it
        (El("Edit", name="Shipping address"), ""),
        (El("Edit", name="Search"), ""),
        (El("Button", name="Reset password"), ""),  # a button, not a field
        (El("CheckBox", name="Save my card", patterns=P("Toggle", "Value")), ""),
        (El("Hyperlink", name="Forgot password?", patterns=P("Invoke", "Value")), ""),
    ],
)
def test_what_counts_as_a_private_field(el, kind):
    assert private_kind(el) == kind


async def test_private_fields_are_refused_here_whatever_is_asked():
    link, driver = make()
    page = await read(link)
    for label in ("Password", "Card number", "SIN"):
        no = await refused(do(link, "type", page, label, text="hunter2"))
        assert no.code == "sensitive" and "Pyxi leaves those to you" in str(no), label
        assert (await refused(do(link, "press", page, label, key="Enter"))).code == "sensitive"
    assert (await refused(do(link, "click", page, "Choose File: No file chosen"))).code == "sensitive"
    # A card number is never typed, into anything.
    no = await refused(do(link, "type", page, "To", text="4111 1111 1111 1111"))
    assert no.code == "sensitive" and driver.did == []
    assert named(driver.root, "Password").value == "hunter2"  # untouched, and never read out


async def test_a_field_is_still_private_after_the_page_stops_saying_so():
    link, driver = make()
    await read(link)
    secret = named(driver.root, "Password")
    secret.password, secret.name = False, "Choose one"  # "Show password": now a plain text field
    secret.auto_id = ""
    page = await read(link)
    assert "sensitive" not in item(page, "Choose one")  # nothing marks it any more
    fresh, driver2 = make()
    hidden = named(driver2.root, "Password")
    hidden.name = "Choose one"
    await read(fresh)  # read while it was a password field
    hidden.password = False
    page = await read(fresh)
    assert item(page, "Choose one")["sensitive"] == "password"  # remembered for this page
    assert (await refused(do(fresh, "type", page, "Choose one", text="x"))).code == "sensitive"
    # A field that turns private between the reading and the action is caught at the action.
    late, driver3 = make()
    page = await read(late)
    named(driver3.root, "To").password = True
    assert (await refused(do(late, "type", page, "To", text="x"))).code == "sensitive" and driver3.did == []


# ====================================================================== keys
async def test_a_key_is_posted_to_the_window_that_was_read_and_nowhere_else():
    link, driver = make()  # Chrome
    page = await read(link)
    no = await refused(do(link, "press", page, "Search mail", key="Enter"))
    assert "isn't in front" in str(no) and "won't take the keyboard" in str(no) and driver.did == []
    driver.front = HWND
    await do(link, "press", page, "Search mail", key="Enter")
    assert driver.did == [("focus", "Search mail", ""), ("key", HWND, "Enter")]
    out = await do(link, "type", page, "Search mail", text="invoices", enter=True)
    assert out["value"] == "invoices" and driver.did[-1] == ("key", HWND, "Enter")
    driver.did.clear()
    for key in ("ctrl+Enter", "a", "F5"):
        assert "can't be pressed" in str(await refused(do(link, "press", page, "Search mail", key=key)))
    assert "is a click" in str(await refused(do(link, "press", page, "Go", key="Enter")))
    assert driver.did == []


async def test_firefox_takes_a_key_behind_other_windows_and_keys_can_be_switched_off():
    link, driver = make("firefox.exe")
    page = await read(link)
    await do(link, "press", page, "Search mail", key="Escape")
    assert driver.did == [("legacy_focus", "Search mail", ""), ("key", HWND, "Escape")]
    driver.did.clear()
    link.keys = False
    no = await refused(do(link, "type", page, "Search mail", text="x", enter=True))
    assert "Pressing keys in the tab is switched off" in str(no)
    assert driver.did == [] and named(driver.root, "Search mail").value == ""  # nothing typed either
    out = await do(link, "type", page, "Search mail", text="x")  # without Enter it is typed
    assert out["value"] == "x"


async def test_a_key_is_not_posted_once_the_page_is_no_longer_the_one_that_was_read():
    link, driver = make()
    driver.front = HWND
    page = await read(link)
    real = driver.do

    def and_then_the_page_changes(handle: Any, way: str, text: str = "") -> None:
        real(handle, way, text)
        driver.pages[HWND] = compose(rid=(2, 1))  # while the cursor was being put in the field

    driver.do = and_then_the_page_changes  # type: ignore[method-assign]
    no = await refused(do(link, "press", page, "Search mail", key="Enter"))
    assert no.code in ("gone", "failed") and not [d for d in driver.did if d[0] == "key"]


# ====================================================================== stop
async def test_stop_ends_an_action_in_flight_and_nothing_more_starts_for_that_task():
    link, driver = make()
    page = await read(link)
    driver.hold = threading.Event()
    go = item(page, "Go")
    args = {"tab": HWND, "page": page["page"], "host": SITE, "ref": go["ref"], "label": "Go"}
    call = asyncio.ensure_future(link.call("click", args, run="r_1"))
    await asyncio.sleep(0.15)  # the press has been asked of the browser and hasn't come back
    assert await link.stop() == 1
    stopped = await refused(call)
    assert stopped.code == "stopped" and stopped.unanswered is True  # it may have happened
    driver.hold.set()
    driver.hold = None
    assert (await refused(link.call("click", args, run="r_1"))).code == "stopped"
    await link.call("click", args, run="r_2")  # another task is not stopped


async def test_a_stop_before_the_action_was_sent_means_it_is_never_sent():
    link, driver = make()
    page = await read(link)
    driver.slow_state = threading.Event()  # still checking the element when the stop comes
    go = item(page, "Go")
    args = {"tab": HWND, "page": page["page"], "host": SITE, "ref": go["ref"], "label": "Go"}
    call = asyncio.ensure_future(link.call("click", args, run="r_1"))
    await asyncio.sleep(0.15)
    await link.stop()
    stopped = await refused(call)
    assert stopped.code == "stopped" and stopped.unanswered is False
    driver.slow_state.set()
    await asyncio.sleep(0.2)
    assert driver.did == []  # the worker saw the stop before pressing anything


async def test_esc_in_that_browser_window_stops_the_task():
    link, driver = make()
    heard: list[tuple[str, str]] = []
    link.on_stopped.append(lambda run, how: heard.append((run, how)))
    await link.call("marker", {"tab": HWND, "on": True}, run="r_1")
    driver.esc = True  # Esc, while they are in some other window
    driver.front = 555
    await asyncio.sleep(0.2)
    assert heard == []
    driver.front = HWND  # Esc in the browser window Pyxi is working in
    await asyncio.sleep(0.2)
    assert heard == [("r_1", "esc")]
    assert (await refused(link.call("read", {"tab": HWND}, run="r_1"))).code == "stopped"
    assert not link._watch  # and it has stopped listening for Esc
    await link.call("marker", {"tab": HWND, "on": True}, run="r_2")
    await link.call("marker", {"tab": HWND, "on": False}, run="r_2")  # a task that ends by itself
    assert not link._watch and heard == [("r_1", "esc")]


# ====================================================================== opening an address
async def test_an_address_is_checked_here_too_before_the_browser_is_asked_to_open_it():
    link, driver = make()
    out = await link.call("open", {"tab": HWND, "url": "https://shop.example.com/cart", "expect_host": "shop.example.com"})
    assert out["url"] == "https://shop.example.com/cart" and driver.did == [("open", HWND, "https://shop.example.com/cart")]
    driver.did.clear()
    bad = [
        ("https://shop.example.com/", "evil.example.com"),  # not the host that was shown
        ("https://shop.example.com\\@evil.example.com/", "shop.example.com"),
        ("https://user@evil.example.com/", "evil.example.com"),
        ("javascript:alert(1)", None),
        ("file:///C:/Users", None),
        ("--user-data-dir=C:/x https://a.example", None),
        ('https://a.example/" --flag', "a.example"),
    ]
    for url, expect in bad:
        assert (await refused(link.call("goto", {"tab": HWND, "url": url, "expect_host": expect}))).code == "failed", url
    assert driver.did == []


# ====================================================================== the extension first
async def test_the_extension_is_preferred_and_the_setting_decides_the_rest(settings, monkeypatch):
    brain = _brain(settings)
    assert settings.screen.tab_fallback is False  # off unless asked for
    assert screenlink.choose(brain) is None and tabwork.tools(brain) == [] and tabwork.prompt(brain) == ""
    note = await tabwork.handle(brain, "use_tab", {"task": "click it"})
    assert note.startswith("use_tab: Pyxi's browser extension isn't connected")

    settings.screen.tab_fallback = True
    monkeypatch.setattr(screenlink, "available", lambda: False)  # not a Windows PC
    assert screenlink.choose(brain) is None and tabwork.tools(brain) == []

    link, driver = make()
    link.base = brain.browser
    brain._screen_tab = link
    assert screenlink.choose(brain) is link
    offered = {t.name: t for t in tabwork.tools(brain)}
    assert set(offered) == tabwork.TOOLS
    for spec in offered.values():
        assert "no browser extension is connected" in spec.description and "Only the tab that is showing" in spec.description
        assert "through Pyxi's browser extension" not in spec.description
    assert offered["use_tab"].parameters == tabwork.USE_TAB.parameters
    assert tabwork.prompt(brain) == screenlink.PROMPT

    settings.screen.tab_keys = False
    assert screenlink.choose(brain) is link and link.keys is False

    browser = PretendBrowser(brain.browser)  # an extension connects: it is used, not the screen
    assert screenlink.choose(brain) is brain.browser
    assert tabwork.prompt(brain) == prompts.BROWSER_TAB
    assert [t.description for t in tabwork.tools(brain)] == [t.description for t in tabwork.SPECS]
    text_read = await tabwork.handle(brain, "read_tab", {})
    assert [n for n, _ in browser.did] == ["read"] and driver.did == [] and text_read.startswith("Tab: Compose - Mail")

    brain.browser._conns.clear()  # it goes away
    assert screenlink.choose(brain) is None and tabwork.tools(brain) == []  # a blink is not a reason
    brain._tab_extension_seen -= screenlink.EXTENSION_GRACE_S + 1  # it has been gone a while
    assert screenlink.choose(brain) is link
    settings.screen.tab_fallback = False  # and the user turns the fallback off
    assert screenlink.choose(brain) is None and tabwork.tools(brain) == []


READ_CARD = f"Let Pyxi read {SITE} in your browser?"


async def _peek_refs(brain: Any) -> dict[str, Any]:
    """The refs a task will be shown, without going through the card for reading."""
    link = brain._screen_tab
    gate, link.read_gate = link.read_gate, None
    try:
        return await link.call("read", {"tab": HWND})
    finally:
        link.read_gate = gate


def _screen_brain(settings, exe: str = "chrome.exe") -> tuple[Any, FakeDriver]:
    settings.screen.tab_fallback = True
    brain = _brain(settings)
    link, driver = make(exe)
    link.base = brain.browser
    link.on_stopped = brain.browser.on_stopped
    brain._screen_tab = link
    return brain, driver


async def test_read_tab_reads_the_tab_on_screen(settings, quick):
    brain, driver = _screen_brain(settings)
    cards = Cards(brain.hub)
    shown = await tabwork.handle(brain, "read_tab", {})
    assert shown.startswith(f"Tab: Compose - Mail\nAddress: {URL}")
    assert "Other open tabs" not in shown and "[card number hidden]" in shown and "hunter2" not in shown
    assert cards.asked == [READ_CARD] and driver.did == []  # asked once, for the site
    await tabwork.handle(brain, "read_tab", {})
    assert cards.asked == [READ_CARD]
    note = await tabwork.handle(brain, "read_tab", {"tab": "inbox"})
    assert "only the tab that is showing" in note
    driver.pages[HWND] = compose(url="https://www.rbc.example/")
    driver.pages[HWND].name = "Sign in - Online Banking"
    assert "private page" in await tabwork.handle(brain, "read_tab", {})  # tabwork's own guard


async def test_use_tab_works_through_it_with_every_card_and_the_log(settings, quick):
    brain, driver = _screen_brain(settings)
    cards = Cards(brain.hub)
    page = await _peek_refs(brain)
    to, send, secret = (item(page, n)["ref"] for n in ("To", "Send", "Password"))
    job = await work(
        brain,
        "Write to Dana and send it",
        act({"do": "type", "ref": to, "text": "dana@example.com"}, {"do": "type", "ref": secret, "text": "hunter2"}),
        act({"do": "click", "ref": send}),
        ("done", {"ok": True, "summary": "Sent."}),
    )
    assert job.status == "done" and job.summary == "Sent."
    assert cards.asked == [READ_CARD, f"Let Pyxi click and type on {SITE} in your browser?", f'Click "Send" on {SITE}']
    assert "This submits the form on the page." in cards.card()["reason"]
    assert driver.acted() == [("legacy_value", "To", "dana@example.com"), ("legacy", "Send", "")]
    assert logged(brain) == [f'Clicked "Send" on {SITE}', f'Typed "dana@example.com" into "To" on {SITE}']
    assert "No browser extension is connected" in brain._tab_llm.systems[0]  # the model knows the limits
    assert "theirs to fill" in brain._tab_llm.seen[1]  # and why the password wasn't typed


async def test_dont_on_the_card_means_nothing_happens_and_stop_everything_reaches_it(settings, quick):
    brain, driver = _screen_brain(settings)
    Cards(brain.hub, answer=lambda card: card["action"] == READ_CARD)  # may read, may not act
    page = await _peek_refs(brain)
    job = await work(brain, "Tick the box", act({"do": "click", "ref": item(page, "Send me a copy")["ref"]}))
    assert job.summary == "Okay, I left that for you." and driver.did == []
    nobody, driver0 = _screen_brain(settings)
    Cards(nobody.hub, answer=False)  # may not even read
    job = await work(nobody, "Tick the box", act({"do": "click", "ref": "e1"}))
    assert "said no to Pyxi reading" in job.summary and driver0.did == [] and driver0.asked == []
    brain2, driver2 = _screen_brain(settings)
    Cards(brain2.hub)
    driver2.hold = threading.Event()
    page = await _peek_refs(brain2)
    brain2._tab_llm = Scripted(act({"do": "click", "ref": item(page, "Go")["ref"]}))
    assert await tabwork.handle(brain2, "use_tab", {"task": "Press Go"}) is None
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any(j.run for j in brain2._screen_tab._jobs if j.sent):
            break
    await brain2.stop_everything()
    driver2.hold.set()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if brain2._tab_operator is None and not brain2._bg:
            break
    assert brain2._tab_operator is None
    assert [j.status for j in brain2.jobs.jobs.values()] == ["stopped"]


# ====================================================================== the review's findings
def _frame(url: str, *children: El, rid: tuple[int, ...] = (7, 1)) -> El:
    """A frame the way the tree gives one: a document inside the page's document."""
    return El("Document", name="Frame", value=url, rid=rid, patterns=P("Value"), rect=(0, 0, 300, 90), children=list(children))


def _wire(driver: FakeDriver) -> None:
    for el in _all(driver.root):
        el.handle = el


async def test_a_frame_from_another_site_is_not_part_of_the_page():
    link, driver = make()
    press = P("Invoke", "LegacyIAccessible")
    pay = _frame(
        "https://pay.example.net/checkout",
        text("Total: 20 dollars"),
        El("Edit", name="Name on card", patterns=P("Value", "LegacyIAccessible"), rect=(0, 0, 9, 9), rid=(7, 2)),
        El("Button", name="Pay now", patterns=press, rect=(0, 0, 9, 9), rid=(7, 3)),
    )
    own = _frame(
        "https://mail.example.com/notes", El("Button", name="Save note", patterns=press, rect=(0, 0, 9, 9), rid=(8, 2)), rid=(8, 1)
    )
    driver.root.children += [pay, own]
    _wire(driver)
    page = await read(link)
    labels = [i["label"] for i in page["items"]]
    assert "Save note" in labels  # the page's own frame is part of the page
    assert "Pay now" not in labels and "Name on card" not in labels  # another site's is not
    assert "Total: 20 dollars" not in page["text"]
    assert "(a frame from pay.example.net: not part of this page" in page["text"]
    # A frame that was the site's own when the page was read, and belongs to another site by
    # the time of the action: what is in it is no longer acted on under this site's Allow.
    own.value = "https://pay.example.net/checkout"
    no = await refused(do(link, "click", page, "Save note"))
    assert no.code == "gone" and "a frame from pay.example.net" in str(no) and driver.did == []
    # And a password field in another site's frame doesn't make this page's fields suspect.
    pay.children.append(El("Edit", name="PIN", password=True, patterns=P("Value"), rect=(0, 0, 9, 9), rid=(7, 4)))
    assert screenlink.foreign_frame(pay, URL) == "pay.example.net" and not screenlink.foreign_frame(driver.root, URL)


async def test_reading_a_site_needs_their_allow_and_it_is_asked_once():
    asked: list[tuple[str, str]] = []
    answer = [True]

    async def gate(site: str, title: str) -> bool:
        asked.append((site, title))
        return answer[0]

    link, driver = make(read_gate=gate)
    assert (await link.call("peek", {}))["url"] == URL and asked == []  # where the tab is, not what is in it
    page = await read(link)
    assert asked == [(SITE, "Compose - Mail")] and page["text"].startswith("Compose")
    await read(link)
    await do(link, "click", page, "Go")
    assert len(asked) == 1  # once per site, until Pyxi is closed
    driver.pages[HWND] = compose(rid=(3, 1), url="https://bank.example.org/accounts")
    answer[0] = False
    no = await refused(read(link))
    assert no.code == "failed" and "said no to Pyxi reading bank.example.org" in str(no)
    assert asked[-1][0] == "bank.example.org" and driver.asked[-1:] != [] and len(driver.asked) == 2  # nothing was fetched
    await refused(read(link))
    assert len(asked) == 2  # not asked again straight after a no
    # A yes given to the extension is not a yes here, and the other way round.
    class Base:
        on_stopped: list[Any] = []
        allowed = {SITE}

        def site_allowed(self, site: str) -> bool:
            return site in self.allowed

        def allow_site(self, site: str) -> None:
            self.allowed.add(site)

    base = Base()
    other, _ = make(base=base)
    assert not other.site_allowed(SITE)
    other.allow_site("shop.example.com")
    assert other.site_allowed("shop.example.com") and "shop.example.com" not in base.allowed


async def test_a_private_field_is_never_called_by_what_is_in_it():
    link, driver = make()
    box = (0, 0, 9, 9)
    form = driver.root.children[2]
    form.children += [
        El("Edit", auto_id="ssn", patterns=P("Value"), rect=box, rid=(1, 70), children=[text("123-45-6789")]),
        El("Edit", auto_id="cardNumber", patterns=P("Value"), rect=box, rid=(1, 71), children=[text("4111 1111 1111 1111")]),
        El("Edit", patterns=P("Value"), rect=box, rid=(1, 72), children=[text("typed by them")]),
        El("Button", name="Pay with 4111 1111 1111 1111", patterns=P("Invoke"), rect=box, rid=(1, 73)),
    ]
    _wire(driver)
    page = await read(link)
    by_kind = {i["label"]: i.get("sensitive") for i in page["items"]}
    assert by_kind["ssn"] == "id" and by_kind["cardNumber"] == "card"  # called by their ids
    assert "Pay with [card number hidden]" in by_kind
    whole = json.dumps(page)
    assert "123-45-6789" not in whole and "4111" not in whole and "typed by them" not in whole
    shown = tabwork.render(page)
    assert 'text field "ssn" (an ID number field: theirs to fill, never yours)' in shown


async def test_type_and_enter_types_nothing_when_the_key_cannot_follow():
    link, driver = make()  # Chrome, not in front
    page = await read(link)
    no = await refused(do(link, "type", page, "Search mail", text="invoices", enter=True))
    assert "isn't in front" in str(no) and driver.did == [] and named(driver.root, "Search mail").value == ""
    # In front, but the key is refused after the text went in: the field is put back, so
    # that a failure always means nothing was typed.
    driver.front = HWND
    driver.refuse = {"focus", "legacy_focus"}
    no = await refused(do(link, "type", page, "Search mail", text="invoices", enter=True))
    assert no.code == "failed" and str(no).endswith("Nothing was typed.") and not no.unanswered
    assert named(driver.root, "Search mail").value == "" and not [d for d in driver.did if d[0] == "key"]
    # If it can't be put back, it is reported as something that may have happened, which
    # tabwork writes in the activity log.
    real = driver.do
    calls = {"n": 0}

    def only_once(handle: Any, way: str, words: str = "") -> None:
        if way in ("value", "legacy_value"):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("the field is gone")
        real(handle, way, words)

    driver.do = only_once  # type: ignore[method-assign]
    no = await refused(do(link, "type", page, "Search mail", text="invoices", enter=True))
    assert no.unanswered and "The text is in the field, but Enter was not pressed" in str(no)


async def test_an_element_with_no_name_of_its_own_is_checked_by_the_words_inside_it():
    link, driver = make()
    page = await read(link)
    more = next(el for el in _all(driver.root) if el.rid == (1, 40))
    more.children[0].name = "Delete my account"  # the page re-rendered the same element
    no = await refused(do(link, "click", page, "Show more"))
    assert no.code == "gone" and driver.did == []
    more.children[0].name = "Show more"
    await do(link, "click", page, "Show more")
    assert driver.did == [("legacy", "", "")]


async def test_a_key_with_no_element_named_only_goes_into_the_page():
    link, driver = make()
    driver.front = HWND
    page = await read(link)
    driver.cursor_outside = True  # the cursor is in the address bar, or a permission bubble
    for key in ("Escape", "Tab", "Backspace", "Delete", "ArrowDown"):
        no = await refused(do(link, "press", page, key=key))
        assert no.code == "failed" and "cursor isn't in the page" in str(no), key
    assert driver.did == []
    driver.cursor_outside = False  # nothing has the cursor at all
    assert "cursor isn't in the page" in str(await refused(do(link, "press", page, key="Escape")))
    named(driver.root, "To").focused = True  # the cursor is in a field of the page
    await do(link, "press", page, key="Escape")
    assert driver.did == [("key", HWND, "Escape")]
    driver.did.clear()
    named(driver.root, "To").focused = False
    inside = El("Edit", name="Card holder", focused=True, patterns=P("Value"), rect=(0, 0, 9, 9), rid=(7, 2))
    driver.root.children.append(_frame("https://pay.example.net/checkout", inside))
    _wire(driver)
    assert "cursor isn't in the page" in str(await refused(do(link, "press", page, key="Escape")))  # another site's frame
    assert driver.did == []


async def test_private_windows_are_refused_and_never_picked():
    link, driver = make()
    page = await read(link)
    driver.wins[0].private = True
    assert link.pick() == ""
    for call in (read(link), do(link, "click", page, "Go"), link.call("peek", {"tab": HWND})):
        no = await refused(call)
        assert no.code == "browser_page" and "private window" in str(no)
    no = await refused(link.call("open", {"tab": HWND, "url": "https://shop.example.com/", "expect_host": "shop.example.com"}))
    assert "private window" in str(no) and driver.did == []  # and no address is opened from one
    driver.wins.append(Window(200, "News - Mozilla Firefox", "firefox.exe"))
    assert link.pick() == "w200"  # the ordinary window is the one worked in


@pytest.mark.parametrize(
    "names, ids, private",
    [
        (["Ready - Google Chrome", "Ready - Google Chrome (Incognito)"], ["BrowserRootView"], True),
        (["Ready - [InPrivate] - Microsoft Edge"], [], True),
        (["Ready - Microsoft Edge (InPrivate)"], [], True),
        (["Ready \u2014 Mozilla Firefox Private Browsing"], [], True),
        (["Ready"], ["private-browsing-indicator-button toolbarbutton-1"], True),
        (["Bereit - Google Chrome (Inkognito)"], [], True),
        (["Ready - Google Chrome"], ["BrowserRootView", "view_1018"], False),
        (["What is Incognito mode? - Google Chrome"], [], False),  # a page's title is not the window
        (["Private Browsing explained \u2014 Mozilla Firefox"], ["toolbarbutton-1"], False),
    ],
)
def test_what_marks_a_private_window(names, ids, private):
    assert screenlink.private_marks(names, ids) is private


def test_the_real_driver_never_opens_an_address_from_a_private_window(monkeypatch):
    driver = screenlink.UiaDriver()
    monkeypatch.setattr(driver, "_pid", lambda hwnd: 4242)
    monkeypatch.setattr(driver, "_exe_path", lambda pid: r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
    started: list[Any] = []
    monkeypatch.setattr(screenlink.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(screenlink, "_command_line", lambda pid: "chrome.exe --profile-directory=Default")
    driver._private[HWND] = True
    with pytest.raises(BrowserError):
        driver.open(HWND, "https://shop.example.com/")
    assert started == []
    driver._private[HWND] = False
    driver.open(HWND, "https://shop.example.com/")
    assert started[0][0][-2:] == ["--profile-directory=Default", "https://shop.example.com/"]


# ---- the real driver, on a pretend UI Automation
class Raw:
    """One UI Automation element, as far as the driver asks of it."""

    def __init__(self, kind: int = 50026, name: str = "", children: tuple = (), props: dict | None = None) -> None:
        self.props = {30003: kind, 30005: name, 30001: (0, 0, 100, 20), 30010: True, **(props or {})}
        self.kids = list(children)
        self.parent: Raw | None = None
        self.requests: list[Any] = []
        for kid in self.kids:
            kid.parent = self

    def GetCachedPropertyValue(self, prop: int) -> Any:
        return self.props.get(prop)

    def BuildUpdatedCache(self, request: Any) -> Raw:
        REQUESTS.append(request)
        return self

    def GetCachedChildren(self) -> Any:
        kids = self.kids

        class Array:
            Length = len(kids)

            def GetElement(self, i: int) -> Raw:
                return kids[i]

        return Array()


REQUESTS: list[Any] = []
DOC, EDIT, TEXT, BUTTON, GROUP, TAB, TABITEM, PANE = 50030, 50004, 50020, 50000, 50026, 50018, 50019, 50033
VALUE, HAS_VALUE, RID, OFFSCREEN, CLASS, HELP, LABELLED, RECT = 30045, 30043, 30000, 30022, 30012, 30013, 30104, 30001


def uia(monkeypatch, top: Raw | None = None, cursor: Raw | None = None) -> screenlink.UiaDriver:
    driver = screenlink.UiaDriver()

    class Walker:
        def GetParentElementBuildCache(self, raw: Raw, request: Any) -> Any:
            return raw.parent

    class Ui:
        RawViewWalker = Walker()

        def ElementFromHandle(self, hwnd: int) -> Any:
            return top

        def GetFocusedElementBuildCache(self, request: Any) -> Any:
            return cursor

    monkeypatch.setattr(driver, "_uia", lambda: (None, Ui(), "whole", "one", "kids"))
    monkeypatch.setattr(driver, "scroll_state", lambda doc: None)
    REQUESTS.clear()
    return driver


def test_a_page_whose_title_has_a_private_word_still_has_its_address(monkeypatch):
    driver = uia(monkeypatch)
    for title in ("Reset your password", "Pin on Pinterest", "Passport renewal", "Card number help"):
        doc = driver._doc(Raw(DOC, title, props={VALUE: "https://example.com/reset", HAS_VALUE: True, RID: (1, 2)}))
        assert doc.url == "https://example.com/reset" and doc.title == title
    # A field that is private by its words keeps nothing of what is in it.
    field = driver._el(Raw(EDIT, "Card number", props={VALUE: "4111 1111 1111 1111", HAS_VALUE: True}), False)
    assert field.value == "" and driver._el(Raw(EDIT, "City", props={VALUE: "Dhaka", HAS_VALUE: True}), False).value == "Dhaka"
    # Where a name came from, for the rule about unlabelled fields beside a password.
    how = lambda props=None, name="Login": driver._el(Raw(EDIT, name, props=props), False).named_by  # noqa: E731
    assert how({HELP: "Login"}) == "hint" and how({LABELLED: object()}) == "label" and how() == "other" and how(name="") == ""


def test_a_big_page_is_read_in_bounded_pieces_and_never_in_one_request(monkeypatch):
    rows = tuple(Raw(GROUP, children=tuple(Raw(TEXT, f"cell {r}.{c}") for c in range(20))) for r in range(400))
    page = Raw(DOC, "Huge", children=rows, props={VALUE: "https://example.com/huge", HAS_VALUE: True, RID: (1, 2)})
    top = Raw(PANE, "Huge - Google Chrome", children=(page,))
    driver = uia(monkeypatch, top)
    snap = driver.snapshot(HWND, 500, 6.0)
    assert snap is not None and snap.cut and len(_all(snap.root)) <= 501
    assert set(REQUESTS) == {"kids"}  # an element and its children at a time: never the whole page at once
    assert len(REQUESTS) < 80  # and a text is not asked for children it doesn't have
    REQUESTS.clear()
    clock = iter(range(0, 10_000, 4))  # each look at the clock is four seconds later
    monkeypatch.setattr(screenlink.time, "monotonic", lambda: float(next(clock)))
    snap = driver.snapshot(HWND, 100_000, 6.0)
    # The time ran out after the first piece (the page and its rows): it stops there.
    assert snap is not None and snap.cut and not any(row.children for row in snap.root.children)
    assert REQUESTS == ["kids", "kids"]


async def test_a_read_is_given_a_bound_that_ends_before_its_caller_gives_up():
    link, driver = make()
    await read(link)
    nodes, seconds = driver.asked[0]
    assert nodes <= 6000 and seconds <= 6.0  # tabwork waits 20 seconds for a read
    # And a request that does time out is not left to be done later.
    driver.slow_state = threading.Event()
    page = await read(link)
    go = item(page, "Go")
    args = {"tab": HWND, "page": page["page"], "host": SITE, "ref": go["ref"], "label": "Go"}
    timed_out = await refused(link.call("click", args, timeout=0.1))
    assert timed_out.code == "timeout" and not timed_out.unanswered
    driver.slow_state.set()
    driver.slow_state = None
    await asyncio.sleep(0.2)
    assert driver.did == []  # the worker dropped it instead of pressing late
    await link.call("click", args)  # and the next request is served
    assert driver.did == [("legacy", "Go", "")]


def test_the_page_is_the_web_page_that_is_showing_and_the_biggest_if_there_are_two():
    tab, side, hidden, own = object(), object(), object(), object()
    pick = screenlink.pick_document
    assert pick([(side, "https://sidebar.example/", True, 300 * 600), (tab, "https://a.example/", True, 900 * 600)]) is tab
    assert pick([(hidden, "https://b.example/", False, 900 * 600), (tab, "https://a.example/", True, 400 * 300)]) is tab
    assert pick([(hidden, "https://b.example/", False, 900 * 600)]) is None  # a background tab is never the page
    assert pick([(hidden, "https://b.example/", False, 9), (own, "chrome://settings/", True, 9)]) is own
    assert pick([]) is None


def test_hundreds_of_tabs_do_not_hide_the_page(monkeypatch):
    strip = Raw(TAB, children=tuple(Raw(TABITEM, f"Tab {n}", children=(Raw(BUTTON, "Close"),)) for n in range(1500)))
    page = Raw(DOC, "Ready", props={VALUE: "https://example.com/", HAS_VALUE: True, RID: (1, 2), RECT: (0, 0, 800, 600)})
    behind = Raw(DOC, "Other", props={VALUE: "https://other.example/", HAS_VALUE: True, RID: (1, 3), OFFSCREEN: True})
    shell = Raw(DOC, "", props={CLASS: "WebView"})
    top = Raw(PANE, "Ready - Google Chrome", children=(Raw(PANE, children=(strip,)), shell, Raw(PANE, children=(behind, page))))
    driver = uia(monkeypatch, top)
    doc = driver.document(HWND)
    assert doc is not None and doc.url == "https://example.com/" and doc.rid == (1, 2)
    assert len(REQUESTS) < 20  # the tab strip was not walked


def test_the_real_driver_says_which_frames_an_element_is_in_and_what_is_outside(monkeypatch):
    button = Raw(BUTTON, "Pay now", props={RID: (7, 3)})
    frame = Raw(DOC, "Pay", children=(button,), props={VALUE: "https://pay.example.net/checkout", HAS_VALUE: True, RID: (7, 1)})
    plain = Raw(BUTTON, "Send", props={RID: (1, 5)})
    page = Raw(DOC, "Mail", children=(Raw(GROUP, children=(frame,)), plain), props={VALUE: URL, HAS_VALUE: True, RID: (1, 2)})
    bar = Raw(EDIT, "Address and search bar", props={RID: (0, 9)})
    Raw(PANE, "Mail - Google Chrome", children=(Raw(PANE, children=(bar,)), page))
    driver = uia(monkeypatch, cursor=bar)
    assert driver.state(plain, (1, 2)).frames == ()
    assert driver.state(button, (1, 2)).frames == ("https://pay.example.net/checkout",)
    assert driver.state(bar, (1, 2)) is None  # the browser's own address bar is not in the page
    assert driver.state(plain, (5, 5)) is None  # nor is anything in a page that isn't this one
    assert driver.state(plain, ()) is None  # and a page with no identity holds nothing
    assert driver.focused((1, 2)) is None  # the cursor is in the address bar
    assert uia(monkeypatch, cursor=plain).focused((1, 2)).name == "Send"


async def test_open_reports_the_page_that_really_loaded():
    link, driver = make()
    # The bare address sends the browser on to www.: that page is the answer, by its real address.
    driver.lands = compose(rid=(9, 9), url="https://www.shop.example.com/")
    out = await link.call("open", {"tab": HWND, "url": "https://shop.example.com/", "expect_host": "shop.example.com"})
    assert out["url"] == "https://www.shop.example.com/"
    # The tab is already on that site and nothing new loads: the old page is not passed off
    # as the one that was asked for.
    driver.lands = False
    link.open_wait_s = 1.0
    no = await refused(link.call("open", {"tab": HWND, "url": "https://www.shop.example.com/cart", "expect_host": "www.shop.example.com"}))
    assert no.code == "failed" and no.unanswered and "didn't show up" in str(no)
    driver.lands = compose(rid=(9, 10), url="https://www.shop.example.com/cart")  # a new document: accepted at once
    out = await link.call("open", {"tab": HWND, "url": "https://www.shop.example.com/cart", "expect_host": "www.shop.example.com"})
    assert out["url"].endswith("/cart")


async def test_a_cancelled_task_stops_its_work_in_flight_and_what_was_waiting():
    link, driver = make()
    page = await read(link)
    send, go = item(page, "Send"), item(page, "Go")
    base = {"tab": HWND, "page": page["page"], "host": SITE}
    driver.slow_state = threading.Event()  # the first is still checking its element
    first = asyncio.ensure_future(link.call("click", {**base, "ref": send["ref"], "label": "Send"}, run="r_1"))
    second = asyncio.ensure_future(link.call("click", {**base, "ref": go["ref"], "label": "Go"}, run="r_1"))
    await asyncio.sleep(0.15)
    first.cancel()  # the tray's Stop cancels the task; link.stop() is not what runs
    second.cancel()
    await asyncio.sleep(0.05)
    driver.slow_state.set()
    driver.slow_state = None
    await asyncio.sleep(0.3)
    assert driver.did == []  # neither "Send" nor "Go" was pressed afterwards
    await link.call("click", {**base, "ref": go["ref"], "label": "Go"}, run="r_2")
    assert driver.did == [("legacy", "Go", "")]


async def test_a_document_with_no_identity_is_never_a_page():
    link, driver = make()
    driver.pages[HWND] = compose(rid=())
    no = await refused(read(link))
    assert no.code == "failed" and link.last_trouble == "the page gave no identity"
    driver.pages[HWND] = compose()
    page = await read(link)
    driver.pages[HWND] = compose(rid=())  # another document, which gives no identity
    assert (await refused(do(link, "click", page, "Go"))).code in ("failed", "gone") and driver.did == []


def test_a_browser_that_a_program_is_driving_is_never_the_users(monkeypatch):
    assert screenlink.automated(r"chrome.exe --remote-debugging-pipe --user-data-dir=C:\\Temp\\pw")
    assert screenlink.automated("msedge.exe --enable-automation https://example.com")
    assert screenlink.automated("firefox.exe -marionette -profile C:\\\\Temp\\\\x")
    assert not screenlink.automated(r"chrome.exe --profile-directory=Default https://example.com/-marionette-notes")
    assert not screenlink.automated("")
    from pyxi import hands

    monkeypatch.setattr(hands, "is_own", lambda hwnd: False)
    driver = screenlink.UiaDriver()
    monkeypatch.setattr(driver, "_pid", lambda hwnd: hwnd * 10)
    lines = {1000: "chrome.exe --remote-debugging-port=9222", 2000: "chrome.exe"}
    monkeypatch.setattr(screenlink, "_command_line", lambda pid: lines[pid])
    assert driver.is_own(100) is True and driver.is_own(200) is False  # Pyxi's booking browser; theirs


async def test_an_unlabelled_field_beside_a_password_is_left_to_them():
    def page_with(*fields: El, password: bool = True) -> El:
        root = compose()
        form = El("Group", aria="form", children=list(fields))
        if password:
            form.children.append(El("Edit", name="Password", password=True, patterns=P("Value"), rect=(0, 0, 9, 9), rid=(2, 9)))
        root.children = [form]
        for el in _all(root):
            el.handle = el
        return root

    def edit(name: str, how: str, rid: int, **more: Any) -> El:
        return El("Edit", name=name, named_by=how, patterns=P("Value", "LegacyIAccessible"), rect=(0, 0, 9, 9), rid=(2, rid), **more)

    fields = lambda: (  # noqa: E731
        edit("Login", "hint", 1, help="Login"),  # named only by its placeholder
        edit("", "", 2),  # no name at all
        edit("Email address", "label", 3),  # a label on the page
        edit("Display name", "other", 4),  # named some other way (aria-label)
        edit("Find a member", "hint", 5, aria="searchbox", help="Find a member"),
    )
    link, driver = make()  # Chrome says where a name came from
    driver.pages[HWND] = page_with(*fields())
    page = await read(link)
    flagged = {i["label"]: i.get("sensitive") for i in page["items"]}
    assert flagged == {"Login": "unclear", "": "unclear", "Email address": None, "Display name": None, "Find a member": None, "Password": "password"}
    no = await refused(do(link, "type", page, "Login", text="dana"))
    assert no.code == "sensitive" and "no clear label, beside a password field" in str(no) and driver.did == []
    assert (await do(link, "type", page, "Email address", text="dana@example.com"))["value"] == "dana@example.com"
    assert 'text field "Login" (private: theirs to fill, never yours)' in tabwork.render(page)
    fox, driver2 = make("firefox.exe")  # Firefox doesn't: only a label on the page clears a field
    driver2.pages[HWND] = page_with(*fields())
    flagged = {i["label"]: i.get("sensitive") for i in (await read(fox))["items"]}
    assert flagged["Display name"] == "unclear" and flagged["Email address"] is None
    plain, driver3 = make()  # no password field in the form: nothing is suspect
    driver3.pages[HWND] = page_with(*fields(), password=False)
    assert not any(i.get("sensitive") for i in (await read(plain))["items"])


# ====================================================================== what people read
def test_nothing_the_user_reads_has_em_dashes_or_emoji():
    files = [
        ROOT / "src" / "pyxi" / "screenlink.py",
        ROOT / "scripts" / "screen_tab_check.py",
        ROOT / "docs" / "SCREEN-TAB-FALLBACK.md",
        ROOT / "docs" / "SCREEN-TAB-CHECK.md",
        Path(__file__),
    ]
    for path in files:
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8")
        assert "\u2014" not in body and "\u2013" not in body, path.name
        assert not re.search("[\U0001f300-\U0001faff\u2600-\u27bf]", body), path.name
