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
        self.before_key: Any = None
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
        doc = self._doc(hwnd)
        return Snapshot(doc, self.pages[hwnd], (self.y, 30.0)) if doc else None

    def state(self, handle: Any, doc: tuple[int, ...]) -> El | None:
        if self.slow_state is not None:
            self.slow_state.wait(5)
        for root in self.pages.values():
            if root.rid == doc and any(el is handle for el in _all(root)):
                return dataclasses.replace(handle, children=[])
        return None

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
        self.pages[hwnd] = compose(rid=(9, 9), url=url)

    def acted(self) -> list[tuple[Any, ...]]:
        return [d for d in self.did if d[0] not in ("focus", "legacy_focus")]


def make(exe: str = "chrome.exe", **kw: Any) -> tuple[ScreenLink, FakeDriver]:
    driver = FakeDriver(exe)
    return ScreenLink(driver, settle_s=0.2, **kw), driver


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
@pytest.mark.parametrize(
    "exe, pressed, ticked, typed",
    [("chrome.exe", "invoke", "toggle", "value"), ("firefox.exe", "legacy", "legacy", "legacy_value")],
)
async def test_it_presses_ticks_and_types_with_the_elements_own_actions(exe, pressed, ticked, typed):
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
    driver.refuse = {"invoke"}
    await do(link, "click", page, "Go")
    assert driver.did == [("legacy", "Go", "")]
    driver.did.clear()
    driver.deaf = {"value"}  # accepted, and the field doesn't change
    out = await do(link, "type", page, "To", text="dana@example.com")
    assert [d[0] for d in driver.did] == ["value", "legacy_value"] and out["value"] == "dana@example.com"
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
    assert ways == (["expand", "select", "collapse"] if lazy else ["select"])
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
    assert driver.did == [("legacy_value", "Search mail", "x")]  # typed, and no key


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

    brain.browser._conns.clear()  # it goes away again
    assert screenlink.choose(brain) is link
    settings.screen.tab_fallback = False  # and the user turns the fallback off
    assert screenlink.choose(brain) is None and tabwork.tools(brain) == []


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
    assert cards.asked == [] and driver.did == []
    note = await tabwork.handle(brain, "read_tab", {"tab": "inbox"})
    assert "only the tab that is showing" in note
    driver.pages[HWND] = compose(url="https://www.rbc.example/")
    driver.pages[HWND].name = "Sign in - Online Banking"
    assert "private page" in await tabwork.handle(brain, "read_tab", {})  # tabwork's own guard


async def test_use_tab_works_through_it_with_every_card_and_the_log(settings, quick):
    brain, driver = _screen_brain(settings)
    cards = Cards(brain.hub)
    page = await brain._screen_tab.call("read", {"tab": HWND})
    to, send, secret = (item(page, n)["ref"] for n in ("To", "Send", "Password"))
    job = await work(
        brain,
        "Write to Dana and send it",
        act({"do": "type", "ref": to, "text": "dana@example.com"}, {"do": "type", "ref": secret, "text": "hunter2"}),
        act({"do": "click", "ref": send}),
        ("done", {"ok": True, "summary": "Sent."}),
    )
    assert job.status == "done" and job.summary == "Sent."
    assert cards.asked == [f"Let Pyxi click and type on {SITE} in your browser?", f'Click "Send" on {SITE}']
    assert "This submits the form on the page." in cards.card()["reason"]
    assert driver.acted() == [("value", "To", "dana@example.com"), ("invoke", "Send", "")]
    assert logged(brain) == [f'Clicked "Send" on {SITE}', f'Typed "dana@example.com" into "To" on {SITE}']
    assert "No browser extension is connected" in brain._tab_llm.systems[0]  # the model knows the limits
    assert "theirs to fill" in brain._tab_llm.seen[1]  # and why the password wasn't typed


async def test_dont_on_the_card_means_nothing_happens_and_stop_everything_reaches_it(settings, quick):
    brain, driver = _screen_brain(settings)
    Cards(brain.hub, answer=False)
    page = await brain._screen_tab.call("read", {"tab": HWND})
    job = await work(brain, "Tick the box", act({"do": "click", "ref": item(page, "Send me a copy")["ref"]}))
    assert job.summary == "Okay, I left that for you." and driver.did == []
    driver.hold = threading.Event()
    brain2, driver2 = _screen_brain(settings)
    Cards(brain2.hub)
    driver2.hold = threading.Event()
    page = await brain2._screen_tab.call("read", {"tab": HWND})
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
