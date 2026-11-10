"""Pyxi in the tab on screen, with no browser extension: the same job through Windows
UI Automation (the accessibility layer).

``companion/tabwork.py`` talks to a browser through a link: ``pick`` a browser, then ``call``
it with ``peek``, ``read``, ``click``, ``type``, ``select``, ``press``, ``scroll``, ``back``,
``goto``, ``open``, ``switch``, ``tabs`` and ``marker``. :class:`pyxi.browserlink.BrowserLink`
sends those to the extension. :class:`ScreenLink` here answers the same requests itself, from
the browser window's accessibility tree, so ``read_tab`` and ``use_tab`` work when nobody has
installed anything and every rule in tabwork (the consent card, the card before anything is
sent, the fence around page text, the activity log) applies unchanged. The extension is
always preferred: this is used only while no browser is connected (:func:`choose`).

What it can reach, and what it can't:

* Only the tab that is showing, in a browser window that is on screen. Other tabs, a
  minimised window, a window fully covered by another one: not reachable (Chrome and Edge
  stop answering for a page nobody can see). It says so wherever the model or the user
  would expect more.
* It never moves the mouse and never types on the keyboard. Buttons and links are pressed
  with the element's own action (Invoke, or the older "default action"), text is put in with
  the element's Value, boxes are ticked with Toggle, radio buttons and options chosen with
  SelectionItem, dropdowns opened with ExpandCollapse, pages scrolled with Scroll. Where an
  element offers none of these it says it can't be done this way; it does not fall back to
  the mouse.
* Keys (Enter in a field, Escape, the arrows) are a separate capability, ``keys``, which
  Settings can turn off. A key is posted as a message to the one window that was read,
  never typed on the keyboard, so it can't land in another window. Chrome and Edge only
  listen to it while their window is in front: when it isn't, the key is refused, and Pyxi
  does not take the keyboard to make it work.

What it refuses, here, whatever it is asked (tabwork refuses the same again):

* password fields (UI Automation's IsPassword, and the field's words), card number,
  security code, expiry and bank fields, ID number fields, and file pickers: never typed
  into, never read;
* anything in Pyxi's own windows;
* anything outside the web page: a [ref] is only ever given to an element inside the
  document, and before every action the element is checked to still be inside it. The
  address bar, the toolbar, settings, extension popups and permission prompts have no ref;
* acting on anything but the page and the element that were read: every action names the
  page (an id made from the document's own identity in the accessibility tree, which a new
  document doesn't share), the site and the element's words.

Measured, per browser and per kind of action: scripts/screen_tab_check.py and
docs/SCREEN-TAB-FALLBACK.md.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import hashlib
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from pyxi.browserlink import BrowserError, host_of

log = logging.getLogger(__name__)

# The browsers this works in, by program name: (what people call it, its engine). Chrome,
# Edge and Firefox are measured (scripts/screen_tab_check.py). The rest are the same two
# engines under another name and are let in on that basis, unmeasured.
BROWSERS = {
    "chrome.exe": ("Chrome", "chromium"),
    "msedge.exe": ("Edge", "chromium"),
    "firefox.exe": ("Firefox", "gecko"),
    "brave.exe": ("Brave", "chromium"),
    "vivaldi.exe": ("Vivaldi", "chromium"),
    "opera.exe": ("Opera", "chromium"),
    "chromium.exe": ("Chromium", "chromium"),
    "librewolf.exe": ("LibreWolf", "gecko"),
    "waterfox.exe": ("Waterfox", "gecko"),
    "zen.exe": ("Zen", "gecko"),
}
MEASURED = ("chrome.exe", "msedge.exe", "firefox.exe")

VISIBLE_ONLY = (
    "Without Pyxi's browser extension only the tab that is showing can be reached, not "
    "other tabs. Ask them to bring that tab to the front, or to connect the extension "
    "(Settings, Connect your browser)."
)
NOT_SHOWING = (
    "The page isn't answering. Without the browser extension the browser window has to be "
    "showing on screen (not minimised, not covered by another window). Ask them to bring it "
    "to the front, then try again."
)
RICH_TEXT = (
    "That's a rich text box (the kind a mail body or a chat message is written in). Without "
    "Pyxi's browser extension its text can't be set in a way the page notices. Don't try "
    "again: say what is left for them, and that the extension can do it (Settings, Connect "
    "your browser)."
)
NO_WAY = (
    "That can't be done without the mouse here: the page gives this element no action of "
    "its own. Pyxi's browser extension can do it (Settings, Connect your browser)."
)

# What the model that works in the tab is told, on top of tabwork's own instructions.
NOTE = """

No browser extension is connected, so you are working through Windows accessibility, in the \
tab that is showing in their browser window. That changes a few things:
- Only that tab. `switch_tab` doesn't work; `goto` and `open_tab` both open the address in a \
new tab of that window.
- `press` only does Enter, Escape, Tab, Space, Backspace, Delete and the arrow keys, on an \
element you name, and in Chrome and Edge only while that window is in front. If a key is \
refused, click the page's own button instead (the search or submit button).
- Rich text boxes (a mail body, a document) may be listed as "editable text" that can't be \
typed into this way. If the task needs one, say so with `ask_user` or `done` ok=false, and \
tell them Pyxi's browser extension can do it.
- A button inside a form is treated as one that may submit it, so they are asked first.
- If an action says it can't be done this way, don't repeat it: try another element, or stop \
and say what is left."""

# What the chat model is told instead of prompts.BROWSER_TAB.
PROMPT = """

In their browser (no extension connected, so only the tab that is showing):
- `read_tab` reads the page in the browser window in front of them, all of it: use it when \
they ask about that page and what's on screen isn't enough. It can't read other tabs.
- `use_tab` does things in that page for them (click, type, pick, scroll) without their \
mouse: use it instead of `use_computer` for anything inside the page they have open. It \
can't switch tabs, can't work in a minimised or covered window and can't type into rich \
text boxes (a mail body); for those, say so in one line and that Settings has "Connect your \
browser". Scout still does research across sites; `book`, `fill_form`, `check_prices` and \
`edit_document` stay as they are. It asks them before anything is sent, submitted, bought, \
deleted or posted. Say one short line that names the job."""

_EXTENSION_WORDS = "through Pyxi's browser extension"
_SCREEN_WORDS = (
    "in the tab that is showing in their browser (through Windows accessibility: no browser "
    "extension is connected, so other tabs can't be reached)"
)


# ---------------------------------------------------------------------- what a page is made of
@dataclass
class El:
    """One element of the accessibility tree, as it was when the page was read."""

    role: str  # UI Automation's control type: "Button", "Edit", "Hyperlink", "Text"...
    name: str = ""
    aria: str = ""  # the role the browser reports: "searchbox", "form", "heading"...
    props: dict[str, str] = field(default_factory=dict)  # "multiline": "true"...
    auto_id: str = ""  # the element's id on the page
    help: str = ""
    password: bool = False
    offscreen: bool = False
    enabled: bool = True
    search_landmark: bool = False
    patterns: frozenset[str] = frozenset()
    value: str = ""
    checked: bool | None = None
    focused: bool = False  # the page's cursor is in it
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    rid: tuple[int, ...] = ()  # the element's own identity while it lives
    children: list[El] = field(default_factory=list)
    handle: Any = None  # the driver's own reference to the live element


@dataclass
class Window:
    hwnd: int
    title: str
    exe: str  # "chrome.exe"
    minimised: bool = False


@dataclass
class Doc:
    """The document in a window's visible tab, without its contents."""

    rid: tuple[int, ...]
    url: str
    title: str
    busy: bool = False
    handle: Any = None


@dataclass
class Snapshot:
    doc: Doc
    root: El
    scroll: tuple[float, float] | None = None  # (percent of the way down, percent in view)
    cut: bool = False  # the page was too big to read whole


class Driver:
    """What :class:`ScreenLink` needs from the PC. The real one is :class:`UiaDriver`; the
    tests give a pretend one with a tree made by hand."""

    def available(self) -> bool:
        raise NotImplementedError

    def windows(self) -> list[Window]:
        """Browser windows, the one on top first."""
        raise NotImplementedError

    def foreground(self) -> int:
        raise NotImplementedError

    def under_mouse(self) -> int:
        return 0

    def is_own(self, hwnd: int) -> bool:
        return False

    def esc_down(self) -> bool:
        return False

    def document(self, hwnd: int) -> Doc | None:
        raise NotImplementedError

    def snapshot(self, hwnd: int, max_nodes: int, budget_s: float) -> Snapshot | None:
        raise NotImplementedError

    def state(self, handle: Any, doc: tuple[int, ...]) -> El | None:
        """The element as it is now, or None if it is no longer inside that document."""
        raise NotImplementedError

    def do(self, handle: Any, way: str, text: str = "") -> None:
        raise NotImplementedError

    def options(self, handle: Any) -> list[El]:
        return []

    def has_children(self, handle: Any) -> bool:
        return False

    def scroll(self, doc: Doc, direction: str) -> bool | None:
        return None

    def scroll_state(self, doc: Doc) -> tuple[float, float] | None:
        return None

    def post_key(self, hwnd: int, key: str) -> None:
        raise NotImplementedError

    def post_back(self, hwnd: int) -> None:
        raise NotImplementedError

    def open(self, hwnd: int, url: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------- private fields
# The same words as the extension's page script (extension/content.js), looked for in what
# UI Automation gives for a field: its name (the label), its id and its hint. The page's own
# name= and autocomplete= attributes aren't in the accessibility tree, so a field that is
# private only by those is not recognised here: one of the limits in the report.
_PASSWORD = re.compile(
    r"\b(password|passwd|passwort|kennwort|passcode|passphrase|pwd|pin|otp|totp|2fa|mfa|senha)\b"
    r"|one time (code|password|pin)|verification code|security (answer|question)"
    r"|secret (key|word|answer)|mot de passe|contrase(n|ñ)a"
)
_CARD = re.compile(
    r"\b(card|cardholder|cardnumber|ccn|ccnum|ccnumber|cvv2?|cvc2?|csc|cvn|iban|swift|bic|"
    r"kartennummer|kreditkarte|tarjeta)\b|\bcc (num|number|no|exp|csc|cvc|cvv|name)|credit card|"
    r"debit card|carte bancaire|security code|expir(y|ation)|\bexp (date|month|year|mm|yy)|"
    r"routing( number)?|sort code|account (number|no)\b|bank account"
)
_GOV_ID = re.compile(
    r"\b(ssn|sin|nino|tfn|itin|aadhaar|passport|medicare)\b|social (security|insurance)|"
    r"national (id|identity|insurance)|driv(er'?s?|ing) ?licen[cs]e|licen[cs]e (number|no)\b|"
    r"tax (id|number|file)|employer identification|health card|pan (number|card)|"
    r"identity (number|card)|\bid number|personal (id|identity) (number|code)"
)
_PASSWORD_IN = re.compile(
    r"password|passwd|passwort|kennwort|passcode|passphrase|contrasena|motdepasse|wachtwoord|"
    r"onetimecode|onetimepass|otpcode|otptoken|verificationcode|verifycode|twofactor|authcode|pincode"
)
_CARD_IN = re.compile(
    r"cardnumber|cardnum|cardno|creditcard|debitcard|kartennummer|kreditkarte|numerodetarjeta|"
    r"numerotarjeta|numerodecarte|cartaodecredito|ccnumber|ccnum|cardcvv|cardcvc|cvvcode|cvccode|"
    r"securitycode|verificationvalue|cardverification|cardexpir|cardholder|routingnumber|"
    r"accountnumber|bankaccount"
)
_ID_IN = re.compile(
    r"socialsecurity|socialinsurance|passportnumber|passportno|nationalid|nationalinsurance|"
    r"taxid|taxnumber|steuernummer|driverslicen|drivinglicen|identitynumber|personalausweis|"
    r"healthcardnumber"
)
_FILE = re.compile(r"choose files?|browse\b|no files? (chosen|selected)|upload a file", re.I)
_PRIVATE_WORDS = {
    "password": "a password or code field",
    "card": "a card or bank details field",
    "id": "an ID number field",
    "file": "a file picker",
}
_FIELDS = {"Edit", "ComboBox", "Spinner", "Document"}


def _words(*parts: str) -> str:
    joined = " ".join(p for p in parts if p)
    joined = re.sub(r"([a-z])([A-Z])", r"\1 \2", joined)
    return re.sub(r"[_\-.\[\]:/]+", " ", joined).lower()


def _squash(text: str) -> str:
    plain = unicodedata.normalize("NFD", str(text or "").lower())
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in plain if not unicodedata.combining(c)))


def private_kind(el: El, was_password: bool = False) -> str:
    """"password", "card", "id", "file" or "" for an element Pyxi may work with."""
    if el.role == "Button":
        return "file" if "Value" in el.patterns or _FILE.search(el.name or "") else ""
    if el.role not in _FIELDS and "Value" not in el.patterns:
        return ""
    if el.role in ("CheckBox", "RadioButton", "Hyperlink", "Slider", "ProgressBar"):
        return ""
    if el.password or was_password:
        return "password"
    names = [el.name, el.auto_id, el.help]
    hay = _words(*names)
    run = "|".join(_squash(n) for n in names)
    if _PASSWORD.search(hay) or _PASSWORD_IN.search(run):
        return "password"
    if _CARD.search(hay) or _CARD_IN.search(run):
        return "card"
    if _GOV_ID.search(hay) or _ID_IN.search(run):
        return "id"
    return ""


# ---------------------------------------------------------------------- a page as tabwork reads it
def _clean(text: Any, limit: int = 90) -> str:
    return " ".join(str(text or "").split())[:limit]


_INLINE_ARIA = ("", "description", "generic", "text", "strong", "emphasis", "mark", "code")
_KIND = {
    "Hyperlink": "link",
    "CheckBox": "checkbox",
    "RadioButton": "radio button",
    "TabItem": "tab",
    "MenuItem": "menu item",
    "TreeItem": "tree item",
    "Slider": "slider",
    "SplitButton": "button",
}
_GENERIC = {"Group", "Pane", "Image", "Custom", "DataItem", "ListItem", "HeaderItem", "Text"}
_CLICK_PATTERNS = ("Invoke", "Toggle", "SelectionItem", "ExpandCollapse")


def _inner_text(el: El, limit: int = 90) -> str:
    out: list[str] = []
    stack = list(reversed(el.children))
    while stack and sum(len(o) for o in out) < limit:
        node = stack.pop()
        if node.role == "Text" and not node.children and node.name.strip():
            out.append(node.name)
        stack.extend(reversed(node.children))
    return _clean(" ".join(out), limit)


def label_of(el: El, inner: str = "") -> str:
    """What an element is called: its accessible name, else its hint, its id, its own text."""
    return _clean(el.name) or _clean(el.help) or inner or _clean(el.auto_id)


def kind_of(el: El) -> str:
    """The kind of thing an element is, in tabwork's words, or "" if it isn't something to
    act on."""
    role = el.role
    if role == "Edit" or role == "Spinner":
        if el.aria == "searchbox":
            return "search box"
        return "text area" if el.props.get("multiline") == "true" else "text field"
    if role == "Button":
        if "ExpandCollapse" in el.patterns and "Invoke" not in el.patterns and not el.props.get("haspopup"):
            return "expander"
        return "button"
    if role == "CheckBox" and el.aria == "switch":
        return "switch"
    if role == "ComboBox":
        native = el.props.get("haspopup") == "menu" or any(
            c.role == "List" or c.role == "ListItem" for c in el.children
        )
        return "dropdown" if native else "dropdown box"
    if role == "ListItem":
        if el.aria == "option" and ("SelectionItem" in el.patterns or "Invoke" in el.patterns):
            return "option"
        return "clickable" if "Invoke" in el.patterns else ""
    if role in _KIND:
        return _KIND[role]
    if role in _GENERIC:
        if el.aria == "button":
            return "button"
        if el.aria == "link":
            return "link"
        if role == "Text" and not el.children:
            return ""  # a word of a label: its field is what's listed
        if "Invoke" in el.patterns:
            return "clickable"
        if role == "Group" and _editable(el):
            return "editable text"
    return ""


def _editable(el: El) -> bool:
    """A rich text box (contenteditable): a container the browser says can be edited."""
    # Chrome and Edge say "readonly" of everything that can't be edited, and leave it out
    # for what can; Firefox says nothing either way, so there only the role tells.
    return el.aria in ("textbox", "searchbox") or (
        bool(el.props) and "readonly" not in el.props and el.props.get("multiline") == "true"
    )


def _href(value: str, page_url: str) -> str:
    try:
        to = urlsplit(value)
    except ValueError:
        return ""
    if to.scheme not in ("http", "https") or not to.netloc:
        return ""
    return ("" if to.netloc == urlsplit(page_url).netloc else to.netloc) + to.path[:60]


def outline(
    snap: Snapshot,
    *,
    max_chars: int = 4000,
    max_items: int = 90,
    was_password: Callable[[El], bool] = lambda el: False,
) -> tuple[str, bool, list[tuple[El, dict[str, Any]]], bool]:
    """A page's text and the things on it that can be acted on, in the shape the extension
    gives: (text, the text goes on, [(element, item)], there are more items)."""
    from pyxi.companion.tabwork import hide_numbers

    lines: list[list[str]] = []
    owner: Any = None
    size = 0
    more_text = False
    here: list[tuple[El, dict[str, Any]]] = []
    beyond: list[tuple[El, dict[str, Any]]] = []
    field_labels: set[str] = set()
    generic: list[tuple[El, dict[str, Any], bool]] = []

    def say(text: str, block: Any) -> None:
        nonlocal owner, size, more_text
        if size >= max_chars:
            more_text = True
            return
        if block is not owner or not lines:
            lines.append([])
            owner = block
        lines[-1].append(text)
        size += len(text)

    def describe(el: El, kind: str, in_form: bool, in_search: bool) -> dict[str, Any]:
        item: dict[str, Any] = {"kind": kind, "label": label_of(el, _inner_text(el))}
        secret = private_kind(el, was_password(el))
        if secret:
            # Listed so Pyxi knows it's there and leaves it to the user; its value is never read.
            item["sensitive"] = secret
            if secret == "password":
                item["kind"] = "password field"
            elif secret == "file":
                item["kind"] = "file picker"
            return item
        if kind in ("text field", "text area", "search box"):
            if el.value:
                item["value"] = hide_numbers(_clean(el.value, 120))
            if kind == "search box" or in_search:
                item["search"] = True
        elif kind == "dropdown":
            item["value"] = _clean(el.value, 60)
            options = [_clean(o.name, 40) for o in _options_of(el)][:14]
            if options:
                item["options"] = options
        elif kind == "dropdown box":
            if el.value:
                item["value"] = hide_numbers(_clean(el.value, 120))
        elif kind == "editable text":
            item["label"] = _clean(el.name or el.help) or "text box"
            text = _inner_text(el, 120)
            if text:
                item["value"] = hide_numbers(text)
        elif el.checked is not None:
            item["checked"] = el.checked
        if not el.enabled:
            item["disabled"] = True
        if kind == "button" and in_form and not in_search:
            # The accessibility tree doesn't say which button is a form's submit button, so
            # any button inside a form is treated as one that may submit it.
            item["submits"] = True
        if kind == "link" and el.value:
            href = _href(el.value, snap.doc.url)
            if href:
                item["href"] = href
        return item

    def walk(el: El, block: Any, in_form: bool, in_search: bool, inside: bool) -> bool:
        """Returns True if this element or anything in it was listed."""
        if el.aria == "form":
            in_form = True
        if el.aria == "search" or el.search_landmark:
            in_search = True
        kind = kind_of(el)
        listed = False
        slot = -1
        if kind == "clickable":
            if not inside and _has_size(el):
                generic.append((el, describe(el, kind, in_form, in_search), False))
                slot = len(generic) - 1  # decided once what it holds has been seen
        elif kind and (el.role in ("CheckBox", "RadioButton") or _has_size(el)):
            item = describe(el, kind, in_form, in_search)
            (beyond if el.offscreen else here).append((el, item))
            if el.role in _FIELDS:
                field_labels.add(item["label"].lower())
            listed = True
        if el.role in ("Edit", "Spinner", "ComboBox") or kind == "editable text":
            return listed  # what is in a field is its value, not the page's text
        if el.role == "Text" and not el.children:
            if el.name.strip():
                say(el.name, block)
            return listed
        if not el.children and el.name.strip() and el.role in ("Button", "Image", "Hyperlink"):
            say(el.name, el)
            return listed
        inline = el.role == "Hyperlink" or (el.role == "Text" and el.aria in _INLINE_ARIA)
        below = False
        for child in el.children:
            held = walk(child, block if inline else el, in_form, in_search, inside or listed or slot >= 0)
            below = held or below
        if slot >= 0:
            generic[slot] = (generic[slot][0], generic[slot][1], below)
        return listed or below

    walk(snap.root, snap.root, False, False, False)
    for el, item, holds_listed in generic:
        # A container that is only clickable because the page listens on it: listed when it
        # holds nothing that is already listed, and isn't just the label of a field.
        if holds_listed or not item["label"] or item["label"].lower() in field_labels:
            continue
        (beyond if el.offscreen else here).append((el, item))
    text = "\n".join(re.sub(r"\s+", " ", "".join(line)).strip() for line in lines)
    text = hide_numbers(re.sub(r"\n{3,}", "\n\n", text).strip())
    everything = [*here, *beyond]  # what's on screen first
    return (
        text[:max_chars],
        more_text or len(text) > max_chars or snap.cut,
        everything[:max_items],
        len(everything) > max_items,
    )


def _has_size(el: El) -> bool:
    left, top, right, bottom = el.rect
    return right - left > 1 and bottom - top > 1


def _options_of(el: El) -> list[El]:
    out: list[El] = []
    stack = list(reversed(el.children))
    while stack:
        node = stack.pop()
        if node.role == "ListItem":
            out.append(node)
        else:
            stack.extend(reversed(node.children))
    return out


# ---------------------------------------------------------------------- how each thing is done
# The ways to do each thing, in the order they are tried, per engine. Measured
# (scripts/screen_tab_check.py): in Firefox the UI Automation patterns bring its window to
# the front and the older default action doesn't, so that one goes first there.
WAYS: dict[str, dict[str, tuple[str, ...]]] = {
    "chromium": {
        "press": ("invoke", "legacy"),
        "toggle": ("toggle", "legacy", "invoke"),
        "choose": ("select", "invoke", "legacy"),
        "open": ("expand", "legacy"),
        "type": ("value", "legacy_value"),
        "focus": ("focus", "legacy_focus"),
    },
    "gecko": {
        "press": ("legacy", "invoke"),
        "toggle": ("legacy", "toggle"),
        "choose": ("legacy", "select"),
        "open": ("legacy", "expand"),
        "type": ("legacy_value", "value"),
        "focus": ("legacy_focus", "focus"),
    },
}
_NEEDS = {
    "invoke": "Invoke",
    "legacy": "LegacyIAccessible",
    "toggle": "Toggle",
    "select": "SelectionItem",
    "expand": "ExpandCollapse",
    "value": "Value",
    "legacy_value": "LegacyIAccessible",
    "focus": "",
    "legacy_focus": "LegacyIAccessible",
}
# The keys that can be posted to a window, by the names tabwork sends (tabwork.canon_key).
KEYS = {
    "Enter": (0x0D, 0x1C, False),
    "Escape": (0x1B, 0x01, False),
    "Tab": (0x09, 0x0F, False),
    "Space": (0x20, 0x39, False),
    "Backspace": (0x08, 0x0E, False),
    "Delete": (0x2E, 0x53, True),
    "ArrowUp": (0x26, 0x48, True),
    "ArrowDown": (0x28, 0x50, True),
    "ArrowLeft": (0x25, 0x4B, True),
    "ArrowRight": (0x27, 0x4D, True),
}
_SCROLL_KEYS = {"PageDown": "down", "PageUp": "up", "Home": "top", "End": "bottom"}
_BUTTONISH = {"button", "link", "menu item", "tab", "option", "switch", "checkbox", "radio button", "expander", "clickable", "tree item"}


@dataclass
class _Read:
    """What was read in one window: the page's id, and the element behind each [ref]."""

    page: str
    doc: tuple[int, ...]
    url: str
    refs: dict[str, tuple[El, dict[str, Any]]] = field(default_factory=dict)


class _Job:
    def __init__(self, run: str) -> None:
        self.run = run
        self.lock = threading.Lock()
        self.stopped = False
        self.sent = False  # the action itself has been asked of the browser
        self.waiter: asyncio.Future[Any] | None = None

    def about_to_act(self) -> None:
        """Called on the worker right before the one thing that changes the page."""
        with self.lock:
            if self.stopped:
                raise BrowserError("stopped", "Stopped, as you asked.")
            self.sent = True


class ScreenLink:
    """The tab on screen, answering what :class:`pyxi.browserlink.BrowserLink` would ask the
    extension. ``base`` is that link: the sites the user allowed and the stop listeners are
    shared with it, so a yes given with the extension holds without it and the other way."""

    note = NOTE

    def __init__(self, driver: Driver, base: Any = None, *, keys: bool = True, settle_s: float = 8.0) -> None:
        self.driver = driver
        self.base = base
        self.keys = keys
        self.enabled = True
        self.settle_s = settle_s
        self.on_stopped: list[Callable[[str, str], None]] = base.on_stopped if base is not None else []
        self._secret = secrets.token_hex(8)
        self._reads: dict[int, _Read] = {}
        self._ref_of: dict[tuple[int, tuple[int, ...]], str] = {}
        self._counter = 0
        self._passwords: dict[tuple[int, ...], set[str]] = {}
        self._jobs: set[_Job] = set()
        self._halted: list[str] = []
        self._watch: dict[str, asyncio.Task[Any]] = {}
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        self.last_trouble = ""

    # ------------------------------------------------------------------ what tabwork asks of a link
    @property
    def connected(self) -> bool:
        return self.enabled and self.driver.available()

    def site_allowed(self, site: str) -> bool:
        return bool(self.base is not None and self.base.site_allowed(site))

    def allow_site(self, site: str) -> None:
        if self.base is not None:
            self.base.allow_site(site)

    def public(self) -> dict[str, Any]:
        return self.base.public() if self.base is not None else {"connected": False, "browsers": [], "sites": []}

    def browsers(self) -> list[str]:
        return sorted({BROWSERS[w.exe][0] for w in self.driver.windows()})

    def pick(self, prefer: str = "") -> str:
        """The browser window to work in (as "w<handle>"): the one in front if it is a
        browser, else the one under the mouse, else the browser window nearest the top (of
        the browser named by ``prefer``, when there is one). "" when there is none."""
        windows = [w for w in self.driver.windows() if not w.minimised and not self.driver.is_own(w.hwnd)]
        if not windows:
            return ""
        by_handle = {w.hwnd: w for w in windows}
        for hwnd in (self.driver.foreground(), self.driver.under_mouse()):
            if hwnd in by_handle:
                return f"w{hwnd}"
        want = prefer.lower()
        for w in windows:
            name = BROWSERS[w.exe][0].lower()
            if want and (name in want or w.exe.removesuffix(".exe") in want):
                return f"w{w.hwnd}"
        return f"w{windows[0].hwnd}"

    async def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        run: str = "",
        timeout: float = 20.0,
        prefer: str = "",
        browser: str = "",
    ) -> dict[str, Any]:
        """Do one thing in the tab on screen; the answer the extension would give, or
        :class:`BrowserError`."""
        if not self.connected:
            raise BrowserError("not_connected", "Working in the tab on screen is switched off.")
        if run and run in self._halted:
            raise BrowserError("stopped", "You stopped Pyxi in that tab.")
        does = getattr(self, f"_cmd_{name}", None)
        if does is None:
            raise BrowserError("failed", "That can't be done without Pyxi's browser extension.")
        loop = asyncio.get_running_loop()
        job = _Job(run)
        job.waiter = loop.create_future()
        self._jobs.add(job)
        hint = browser or self.pick(prefer)
        if name == "marker":
            self._marker(dict(args or {}), run, hint)
        work = loop.run_in_executor(self._worker(), does, dict(args or {}), job, hint)

        def finished(done: asyncio.Future[Any]) -> None:
            waiter = job.waiter
            if waiter is None or waiter.done():
                done.exception()  # seen: nobody is waiting for it any more
                return
            if done.exception() is not None:
                waiter.set_exception(done.exception())
            else:
                waiter.set_result(done.result())

        work.add_done_callback(finished)
        try:
            return await asyncio.wait_for(job.waiter, timeout)
        except TimeoutError:
            with job.lock:
                job.stopped = True
            raise BrowserError("timeout", "The browser didn't answer in time.", unanswered=job.sent) from None
        except BrowserError:
            raise
        except Exception as exc:  # UI Automation gave up on something mid-way
            log.warning("screen tab: %s failed: %s", name, exc)
            raise BrowserError(
                "failed", "The browser's accessibility layer didn't answer for that.", unanswered=job.sent
            ) from None
        finally:
            self._jobs.discard(job)

    async def stop(self, run: str = "") -> int:
        """Stop what is being done in the tab (one task's, or everything): requests in
        flight fail with ``stopped`` and nothing more is started for those tasks."""
        cut = 0
        for job in list(self._jobs):
            if run and job.run != run:
                continue
            with job.lock:
                job.stopped = True
                sent = job.sent
            if job.run and job.run not in self._halted:
                self._halted.append(job.run)
            waiter = job.waiter
            if waiter is not None and not waiter.done():
                cut += 1
                waiter.set_exception(BrowserError("stopped", "Stopped, as you asked.", unanswered=sent))
        for name in [run] if run else list(self._watch):
            if name and name not in self._halted:
                self._halted.append(name)
            task = self._watch.pop(name, None)
            if task is not None:
                task.cancel()
        del self._halted[:-40]
        return cut

    def _worker(self) -> concurrent.futures.ThreadPoolExecutor:
        # One thread for everything UI Automation: its element references are used on the
        # thread they were made on.
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="pyxi-screen-tab")
        return self._pool

    # ------------------------------------------------------------------ Esc
    def _marker(self, args: dict[str, Any], run: str, hint: str) -> None:
        """There is no marker to draw in the page without the extension. What "the marker is
        on" still means here: Esc, pressed while that browser window is in front, stops the
        task."""
        if not run:
            return
        old = self._watch.pop(run, None)
        if old is not None:
            old.cancel()
        if args.get("on"):
            hwnd = self._handle(args.get("tab"), hint)
            self.driver.esc_down()  # an Esc from before the task doesn't count
            self._watch[run] = asyncio.ensure_future(self._watch_esc(run, hwnd))

    async def _watch_esc(self, run: str, hwnd: int) -> None:
        end = time.monotonic() + 1800
        while time.monotonic() < end:
            await asyncio.sleep(0.06)
            if self.driver.esc_down() and self.driver.foreground() == hwnd:
                log.info("screen tab: stopped with Esc")
                self._watch.pop(run, None)
                await self.stop(run)
                for heard in list(self.on_stopped):
                    try:
                        heard(run, "esc")
                    except Exception:
                        log.exception("screen tab: a stop listener failed")
                return

    # ------------------------------------------------------------------ which window, which page
    def _handle(self, tab: Any, hint: str = "") -> int:
        if isinstance(tab, int) and not isinstance(tab, bool):
            return tab
        if isinstance(tab, str) and tab.strip():
            return self._named(tab)
        if hint.startswith("w") and hint[1:].isdigit():
            return int(hint[1:])
        raise BrowserError("failed", "There's no browser window on screen.")

    def _named(self, words: str) -> int:
        """Another tab, named: only if those words are the tab showing in a browser window."""
        want = [w for w in words.lower().split() if w]
        for w in self.driver.windows():
            if not w.minimised and want and all(part in w.title.lower() for part in want):
                return w.hwnd
        raise BrowserError("failed", f'There\'s no tab like "{_clean(words, 60)}" showing. {VISIBLE_ONLY}')

    def _window(self, hwnd: int) -> Window:
        if self.driver.is_own(hwnd):
            raise BrowserError("browser_page", "That's one of Pyxi's own windows, which it never touches.")
        for w in self.driver.windows():
            if w.hwnd == hwnd:
                if w.minimised:
                    raise self._not_showing("the window is minimised")
                return w
        raise BrowserError("gone", "That browser window was closed.")

    def _not_showing(self, why: str) -> BrowserError:
        self.last_trouble = why  # for the logs and the measuring script
        log.info("screen tab: the page isn't answering (%s)", why)
        return BrowserError("failed", NOT_SHOWING)

    def _engine(self, hwnd: int) -> str:
        return BROWSERS[self._window(hwnd).exe][1]

    def _page_id(self, hwnd: int, doc: Doc) -> str:
        """A name for the document in a window's visible tab. Made from the document's own
        identity in the accessibility tree, which a page that was replaced doesn't share, so
        an action meant for the page that was read is refused on any other."""
        raw = f"{self._secret}|{hwnd}|{doc.rid}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _brief(self, hwnd: int, doc: Doc | None, window: Window | None = None) -> dict[str, Any]:
        title = (doc.title if doc is not None else "") or (window.title if window else "")
        return {"id": hwnd, "title": title, "url": doc.url if doc else "", "active": True, "window": hwnd}

    def _doc(self, hwnd: int, wait_s: float = 3.5) -> Doc:
        """The web page showing in this window, or why there isn't one to work in."""
        self._window(hwnd)
        end = time.monotonic() + wait_s
        while True:
            # A page whose accessibility has only just been woken has no address yet.
            doc = self.driver.document(hwnd)
            if (doc is not None and doc.url) or time.monotonic() > end:
                break
            time.sleep(0.2)
        if doc is None or not doc.url:
            raise self._not_showing("no web page was found in the window" if doc is None else "the page gave no address")
        if not host_of(doc.url):
            raise BrowserError("browser_page", "That's one of the browser's own pages, which Pyxi can't work in.")
        return doc

    def _settle(self, hwnd: int) -> Doc | None:
        """After something that may load a page: wait until there is a document again and it
        has stopped loading."""
        time.sleep(0.25)
        end = time.monotonic() + self.settle_s
        doc = None
        while time.monotonic() < end:
            try:
                doc = self.driver.document(hwnd)
            except Exception:
                doc = None
            if doc is not None and not doc.busy:
                return doc
            time.sleep(0.15)
        return doc

    # ------------------------------------------------------------------ reading
    def _cmd_peek(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd = self._handle(args.get("tab"), hint)
        window = self._window(hwnd)
        try:
            doc = self.driver.document(hwnd)
        except Exception:
            doc = None
        return self._brief(hwnd, doc, window)

    def _cmd_tabs(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        # Only the tab that is showing: the others can't be read, so they aren't offered.
        try:
            return {"tabs": [self._cmd_peek({}, job, hint)], "unnamed": 0}
        except BrowserError:
            return {"tabs": [], "unnamed": 0}

    def _cmd_read(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd = self._handle(args.get("tab"), hint)
        max_chars = min(20000, max(200, int(args.get("max_chars") or 4000)))
        max_items = min(200, max(1, int(args.get("max_items") or 90)))
        self._doc(hwnd)
        snap = None
        end = time.monotonic() + 5.0
        while True:
            # The first look wakes the browser's accessibility for the page: it answers with
            # an empty document, then fills it in.
            snap = self.driver.snapshot(hwnd, 6000, 6.0)
            if (snap is not None and snap.root.children and not snap.doc.busy) or time.monotonic() > end:
                break
            time.sleep(0.3)
        if snap is None or not snap.root.children:
            raise self._not_showing("the page gave no contents")
        if not host_of(snap.doc.url):
            raise BrowserError("browser_page", "That's one of the browser's own pages, which Pyxi can't work in.")
        seen = self._passwords.setdefault(snap.doc.rid, set())
        while len(self._passwords) > 8:
            self._passwords.pop(next(iter(self._passwords)))

        def was_password(el: El) -> bool:
            # "Show password" turns a password field into a text field: still the password.
            key = _field_key(el)
            if el.password and key:
                seen.add(key)
            return bool(key) and key in seen

        text, more_text, listed, more_items = outline(
            snap, max_chars=max_chars, max_items=max_items, was_password=was_password
        )
        page = self._page_id(hwnd, snap.doc)
        last = self._reads.get(hwnd)
        if last is None or last.page != page:
            for key in [k for k in self._ref_of if k[0] == hwnd]:
                self._ref_of.pop(key)
        read = _Read(page, snap.doc.rid, snap.doc.url)
        items: list[dict[str, Any]] = []
        for el, item in listed:
            ref = self._ref_of.get((hwnd, el.rid)) if el.rid else None
            if ref is None:
                self._counter += 1
                ref = f"e{self._counter}"
                if el.rid:
                    self._ref_of[(hwnd, el.rid)] = ref
            read.refs[ref] = (el, item)
            items.append({"ref": ref, **item})
        self._reads[hwnd] = read
        scroll = {"y": 0, "max": 0}
        if snap.scroll is not None and snap.scroll[1] < 99.5:
            scroll = {"y": round(max(0.0, snap.scroll[0])), "max": 100}
        return {
            "page": page,
            "text": text,
            "more_text": more_text,
            "items": items,
            "more_items": more_items,
            "scroll": scroll,
            "tab": self._brief(hwnd, snap.doc),
        }

    # ------------------------------------------------------------------ acting
    def _as_read(self, args: dict[str, Any], hint: str, must_have_read: bool = True) -> tuple[int, Doc, _Read | None]:
        """Is this still what Pyxi read before it decided? The window, the page by its id,
        and the site."""
        hwnd = self._handle(args.get("tab"), hint)
        doc = self._doc(hwnd)
        read = self._reads.get(hwnd)
        if args.get("page") is None:
            if must_have_read:
                raise BrowserError("gone", "Pyxi has to read the page before it acts on it.")
        elif read is None or args["page"] != read.page or self._page_id(hwnd, doc) != read.page:
            raise BrowserError("gone", "This isn't the page Pyxi read: it has changed. Read it again.")
        if args.get("host") is not None and str(args["host"]) != host_of(doc.url):
            raise BrowserError("gone", "This isn't the site Pyxi read. Read the page again.")
        return hwnd, doc, read

    def _element(self, args: dict[str, Any], doc: Doc, read: _Read | None) -> tuple[El, dict[str, Any]]:
        """The element behind a [ref], as it is now: still inside the page that was read,
        still called what it was called, not private, not switched off."""
        found = read.refs.get(str(args.get("ref") or "")) if read is not None else None
        if found is None:
            raise BrowserError("gone", "That element isn't on the page any more.")
        was, item = found
        now = self.driver.state(was.handle, doc.rid)
        if now is None:
            # Outside the document, or gone from it: never acted on.
            raise BrowserError("gone", "That element isn't on the page any more.")
        label = item["label"] if not (now.name or now.help) else label_of(now)
        if item.get("kind") == "editable text":
            label = _clean(now.name or now.help) or "text box"
        if args.get("label") is not None and str(args["label"]) != label:
            raise BrowserError("gone", "That element isn't what it was when the page was read. Read it again.")
        now.handle = was.handle
        secret = str(item.get("sensitive") or "") or private_kind(
            now, _field_key(now) in self._passwords.get(doc.rid, set())
        )
        if secret:
            raise BrowserError("sensitive", f"That's {_PRIVATE_WORDS[secret]}. Pyxi leaves those to you.")
        if not now.enabled:
            raise BrowserError("failed", "That's switched off on the page right now (disabled).")
        return now, item

    def _try(
        self,
        el: El,
        engine: str,
        action: str,
        job: _Job,
        text: str = "",
        check: Callable[[], bool] | None = None,
    ) -> bool:
        """Do one thing to an element, by the first way that works. With ``check``, a way
        only counts when the check says it took; False then means none did. An element that
        offers no way at all is an error: it is never done some other way."""
        offered = [w for w in WAYS[engine][action] if not _NEEDS[w] or _NEEDS[w] in el.patterns]
        if not offered:
            raise BrowserError("failed", NO_WAY)
        refused = 0
        for way in offered:
            job.about_to_act()
            try:
                self.driver.do(el.handle, way, text)
            except BrowserError:
                raise
            except Exception as exc:  # the element doesn't offer this one after all
                log.debug("screen tab: %s by %s refused: %s", action, way, exc)
                refused += 1
                continue
            if check is None or check():
                return True
        if check is None or refused == len(offered):
            raise BrowserError("failed", NO_WAY)
        return False

    def _after(self, hwnd: int, doc: Doc, settle: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self._settle(hwnd) if settle else None
        if now is None:
            try:
                now = self.driver.document(hwnd)
            except Exception:
                now = None
        return {**(extra or {}), "tab": self._brief(hwnd, now or doc)}

    def _cmd_click(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd, doc, read = self._as_read(args, hint)
        el, item = self._element(args, doc, read)
        engine = self._engine(hwnd)
        kind = str(item.get("kind") or "")
        if kind == "editable text":
            raise BrowserError("failed", "That's a text box: there is nothing to press on it.")
        if "Toggle" in el.patterns and kind in ("checkbox", "switch"):
            action = "toggle"
        elif "SelectionItem" in el.patterns and kind in ("radio button", "option", "tab"):
            action = "choose"
        elif "ExpandCollapse" in el.patterns and "Invoke" not in el.patterns:
            action = "open"
        else:
            action = "press"
        self._try(el, engine, action, job)
        return self._after(hwnd, doc, args.get("settle"))

    def _cmd_type(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        from pyxi.companion.tabwork import card_number, hide_numbers

        hwnd, doc, read = self._as_read(args, hint)
        el, item = self._element(args, doc, read)
        text = str(args.get("text") or "")
        if card_number(text):
            raise BrowserError("sensitive", "That looks like a card number. Pyxi never types those.")
        kind = str(item.get("kind") or "")
        if kind == "editable text" or "Value" not in el.patterns:
            if kind in ("editable text", "text field", "text area", "search box", "dropdown box"):
                raise BrowserError("failed", RICH_TEXT)
            raise BrowserError("failed", "That isn't something text can be typed into. Click it instead.")
        if el.props.get("readonly") == "true" and kind not in ("text field", "text area", "search box"):
            raise BrowserError("failed", "That field can't be changed (read only).")
        engine = self._engine(hwnd)
        # Measured: Chrome and Edge take a value for a rich text box (a contenteditable with
        # the textbox role) and put the words in it without telling the page, so the page's
        # own code never learns of them. Such a box looks exactly like a text area until it
        # has text: then, unlike a real field, it has that text as elements inside it.
        rich = engine == "chromium" and el.role == "Edit"
        if rich and self.driver.has_children(el.handle):
            raise BrowserError("failed", RICH_TEXT)

        def took() -> bool:
            end = time.monotonic() + 0.5  # Firefox reports the new value a moment later
            while True:
                now = self.driver.state(el.handle, doc.rid)
                if now is not None and " ".join(now.value.split()) == " ".join(text.split()):
                    return True
                if now is None or time.monotonic() > end:
                    return False
                time.sleep(0.05)

        self._try(el, engine, "type", job, text, check=took)  # what it shows now is reported back
        if rich and text.strip() and self.driver.has_children(el.handle):
            raise BrowserError(
                "failed",
                "The words were put in that box, but it is a rich text box and the page was not "
                "told, so it will most likely ignore them. " + RICH_TEXT,
            )
        now = self.driver.state(el.handle, doc.rid)
        out: dict[str, Any] = {"value": hide_numbers(_clean(now.value if now else "", 200))}
        if args.get("enter"):
            self._key(hwnd, doc, el, "Enter", job)
        return self._after(hwnd, doc, args.get("settle"), out)

    def _cmd_select(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd, doc, read = self._as_read(args, hint)
        el, item = self._element(args, doc, read)
        engine = self._engine(hwnd)
        kind = str(item.get("kind") or "")
        want = _clean(args.get("option")).lower()
        if kind in ("checkbox", "switch", "radio button"):
            on = want not in ("off", "no", "false", "uncheck", "unchecked", "0")
            if bool(el.checked) != on:
                self._try(el, engine, "toggle" if kind != "radio button" else "choose", job)
                time.sleep(0.1)
            now = self.driver.state(el.handle, doc.rid)
            return self._after(hwnd, doc, False, {"checked": bool(now.checked) if now else on})
        if kind != "dropdown":
            raise BrowserError("failed", "That isn't a plain dropdown. Click it, read the page again, then click the option.")
        opened = False
        options = self.driver.options(el.handle)
        if not options and "ExpandCollapse" in el.patterns:
            # Chrome and Edge only list a dropdown's options while it is open.
            opened = True
            for way in ("expand", "legacy"):
                job.about_to_act()
                try:
                    self.driver.do(el.handle, way)
                except Exception:
                    continue
                end = time.monotonic() + 2.0
                while not options and time.monotonic() < end:
                    time.sleep(0.15)
                    options = self.driver.options(el.handle)
                if options:
                    break
        try:
            names = [(_clean(o.name).lower(), o) for o in options]
            hit = (
                next((o for n, o in names if n == want), None)
                or next((o for n, o in names if want and n.startswith(want)), None)
                or next((o for n, o in names if want and want in n), None)
            )
            if hit is None:
                raise BrowserError(
                    "failed",
                    "No option like that. The options are: " + ", ".join(_clean(o.name, 40) for o in options[:14])
                    if options
                    else "That dropdown's options can't be reached this way. " + NO_WAY,
                )
            if not hit.enabled:
                raise BrowserError("failed", "That option is switched off on the page.")

            def chosen() -> bool:
                time.sleep(0.12)
                now = self.driver.state(el.handle, doc.rid)
                return now is not None and _clean(now.value).lower() == _clean(hit.name).lower()

            if not self._try(hit, engine, "choose", job, check=chosen) and not (
                self._choose_by_keys(hwnd, doc, el, options, hit, job) and chosen()
            ):
                raise BrowserError("failed", "That dropdown can't be set this way. " + NO_WAY)
        finally:
            if opened:
                try:
                    self.driver.do(el.handle, "collapse")
                except Exception:
                    pass
        now = self.driver.state(el.handle, doc.rid)
        return self._after(hwnd, doc, False, {"value": _clean(now.value if now else hit.name, 60)})

    def _choose_by_keys(self, hwnd: int, doc: Doc, el: El, options: list[El], hit: El, job: _Job) -> bool:
        """Firefox doesn't tell the page when an option is chosen through accessibility.
        What it does tell it about is the arrow keys: with the cursor in the dropdown, the
        arrow is pressed as many times as it takes to get from the option it shows to the
        one that is wanted."""
        if not self.keys or self._engine(hwnd) != "gecko":
            return False
        live = [o for o in options if o.enabled]
        now = self.driver.state(el.handle, doc.rid)
        shown = _clean(now.value if now else "").lower()
        at = next((i for i, o in enumerate(live) if _clean(o.name).lower() == shown), None)
        to = next((i for i, o in enumerate(live) if o is hit), None)
        if at is None or to is None:
            return False
        self._focus(hwnd, doc, el, job)
        for _ in range(abs(to - at)):
            self._post(hwnd, doc, "ArrowDown" if to > at else "ArrowUp", job)
            time.sleep(0.05)
        return True

    def _focus(self, hwnd: int, doc: Doc, el: El, job: _Job) -> None:
        def has_cursor() -> bool:
            time.sleep(0.08)
            now = self.driver.state(el.handle, doc.rid)
            return now is not None and now.focused

        if not self._try(el, self._engine(hwnd), "focus", job, check=has_cursor):
            raise BrowserError("failed", "The cursor couldn't be put in that element, so the key wasn't pressed.")

    def _post(self, hwnd: int, doc: Doc, key: str, job: _Job) -> None:
        job.about_to_act()
        # Checked again at the last moment: the key goes to that window by its handle, and
        # only if it is still the window whose page was read.
        again = self.driver.document(hwnd)
        if again is None or again.rid != doc.rid:
            raise BrowserError("gone", "This isn't the page Pyxi read: it has changed. Read it again.")
        self.driver.post_key(hwnd, key)

    def _key(self, hwnd: int, doc: Doc, el: El | None, key: str, job: _Job) -> None:
        """One key, posted to the window that was read (never typed on the keyboard)."""
        if not self.keys:
            raise BrowserError(
                "failed",
                "Pressing keys in the tab is switched off (and no browser extension is connected). "
                "Click the page's own button instead.",
            )
        engine = self._engine(hwnd)
        if engine == "chromium" and self.driver.foreground() != hwnd:
            raise BrowserError(
                "failed",
                "That browser window isn't in front, and without the extension a key only reaches "
                "it when it is. Pyxi won't take the keyboard to do it: click the page's own button "
                "instead, or ask them to bring the window to the front.",
            )
        if el is not None:
            self._focus(hwnd, doc, el, job)
        self._post(hwnd, doc, key, job)

    def _cmd_press(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd, doc, read = self._as_read(args, hint)
        combo = str(args.get("key") or "")
        key = combo.split("+")[-1]
        el = item = None
        if args.get("ref"):
            el, item = self._element(args, doc, read)
        if key in _SCROLL_KEYS and "+" not in combo:
            return self._cmd_scroll({**args, "direction": _SCROLL_KEYS[key], "ref": None}, job, hint)
        if "+" in combo or key not in KEYS:
            raise BrowserError(
                "failed",
                f"Without Pyxi's browser extension the key {_clean(combo, 30)!r} can't be pressed "
                "in the tab (only Enter, Escape, Tab, Space, Backspace, Delete and the arrows).",
            )
        if key in ("Enter", "Space") and item is not None and str(item.get("kind")) in _BUTTONISH:
            raise BrowserError("failed", "Pressing that key there is a click. Click it instead.")
        self._key(hwnd, doc, el, key, job)
        return self._after(hwnd, doc, args.get("settle"))

    def _cmd_scroll(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd, doc, read = self._as_read(args, hint, must_have_read=False)
        direction = str(args.get("direction") or "down")
        if args.get("ref"):
            found = read.refs.get(str(args["ref"])) if read is not None else None
            now = self.driver.state(found[0].handle, doc.rid) if found else None
            if found is None or now is None:
                raise BrowserError("gone", "That element isn't on the page any more.")
            self.driver.do(found[0].handle, "into_view")
            moved: bool | None = True
        else:
            before = self.driver.scroll_state(doc)
            moved = self.driver.scroll(doc, direction)
            if moved is None:
                raise BrowserError("failed", "This page can't be scrolled this way. " + NO_WAY)
            time.sleep(0.15)
            after = self.driver.scroll_state(doc)
            if before is not None and after is not None:
                moved = abs(after[0] - before[0]) > 0.2
        state = self.driver.scroll_state(doc)
        out: dict[str, Any] = {"moved": bool(moved)}
        if state is not None:
            out.update(y=round(max(0.0, state[0])), max=100 if state[1] < 99.5 else 0)
        return self._after(hwnd, doc, False, out)

    def _cmd_check(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        self._as_read(args, hint)
        return {}

    def _cmd_back(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd, doc, _ = self._as_read(args, hint, must_have_read=False)
        job.about_to_act()
        self.driver.post_back(hwnd)  # the "Back" command Windows keyboards send, to that window
        now = self._settle(hwnd)
        return self._brief(hwnd, now or doc)

    def _cmd_goto(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        return self._cmd_open(args, job, hint)

    def _cmd_open(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        url = web_address(str(args.get("url") or ""), args.get("expect_host"))
        hwnd = self._handle(args.get("tab"), hint)
        window = self._window(hwnd)
        job.about_to_act()
        self.driver.open(hwnd, url)  # the browser's own program, asked to open the address
        want = host_of(url)
        end = time.monotonic() + 20.0
        while time.monotonic() < end:
            time.sleep(0.3)
            # It opens as a new tab of the window that browser used last: normally this one.
            for candidate in [hwnd, *[w.hwnd for w in self.driver.windows() if w.exe == window.exe and w.hwnd != hwnd]]:
                try:
                    doc = self.driver.document(candidate)
                except Exception:
                    doc = None
                if doc is not None and host_of(doc.url) == want and not doc.busy:
                    return self._brief(candidate, doc)
        raise BrowserError(
            "failed",
            "The address was handed to the browser, but it didn't show up in a tab Pyxi can see. "
            + VISIBLE_ONLY,
            unanswered=True,
        )

    def _cmd_switch(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        hwnd = self._named(str(args.get("tab") or ""))  # only a tab that is already showing
        return self._cmd_peek({"tab": hwnd}, job, hint)

    def _cmd_marker(self, args: dict[str, Any], job: _Job, hint: str) -> dict[str, Any]:
        return {"on": bool(args.get("on"))}


def _field_key(el: El) -> str:
    return (el.auto_id or _clean(el.name)).lower() if el.role in _FIELDS else ""


def web_address(url: str, expect: Any = None) -> str:
    """An address Pyxi was asked to open, checked the way the extension checks it: plain
    http or https, nothing in it that hides where it leads, and leading to the host that
    was shown to the user."""
    if re.search(r"[\\\s\x00-\x1f\x7f\"]", url) or url.startswith("-"):
        raise BrowserError("failed", "That address is written in a way that hides where it leads.")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise BrowserError("failed", "That isn't a web address.") from None
    if parts.scheme not in ("http", "https") or not host_of(url):
        raise BrowserError("failed", "Pyxi only opens http and https addresses.")
    if "@" in parts.netloc:
        raise BrowserError("failed", "That address is written in a way that hides where it leads.")
    if expect is not None and host_of(url) != str(expect):
        raise BrowserError("failed", f"That address leads to {host_of(url)}, not to {expect}.")
    return url


# ---------------------------------------------------------------------- the brain's side
def available() -> bool:
    return sys.platform == "win32"


def fallback(brain: Any) -> ScreenLink | None:
    """The tab on screen, if working there without the extension is switched on (Settings:
    ``screen.tab_fallback``) and this PC can do it."""
    screen = getattr(brain.settings, "screen", None)
    if screen is None or not getattr(screen, "tab_fallback", False):
        return None
    link = getattr(brain, "_screen_tab", None)
    if link is None:
        if not available():
            return None
        link = ScreenLink(UiaDriver(), getattr(brain, "browser", None))
        brain._screen_tab = link
    link.keys = bool(getattr(screen, "tab_keys", True))
    return link if link.connected else None


def choose(brain: Any) -> Any:
    """The link the tab tools use: the extension's while a browser is connected (always
    preferred), else the tab on screen, else None."""
    link = getattr(brain, "browser", None)
    if link is not None and link.connected:
        return link
    return fallback(brain)


def specs(link: Any, extension_specs: list[Any]) -> list[Any]:
    """The tab tools as the model should read them for this link: with the extension as they
    are; without it, saying plainly that only the tab that is showing can be reached."""
    if not isinstance(link, ScreenLink):
        return list(extension_specs)
    return [
        type(spec)(
            spec.name,
            spec.description.replace(_EXTENSION_WORDS, _SCREEN_WORDS)
            + " Only the tab that is showing: other tabs and windows can't be reached this way.",
            spec.parameters,
        )
        for spec in extension_specs
    ]


def prompt(link: Any, extension_prompt: str) -> str:
    return PROMPT if isinstance(link, ScreenLink) else extension_prompt


async def stop(brain: Any) -> None:
    """Part of "Stop everything"."""
    link = getattr(brain, "_screen_tab", None)
    if link is not None:
        await link.stop()


# ---------------------------------------------------------------------- Windows UI Automation
_SHELL_VIEWS = ("WebView", "ContentsWebView", "NewTabFooterWebView")
_ROLES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
    50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu",
    50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar", 50013: "RadioButton",
    50014: "ScrollBar", 50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
    50024: "TreeItem", 50025: "Custom", 50026: "Group", 50027: "Thumb", 50028: "DataGrid",
    50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50032: "Window", 50033: "Pane",
    50034: "Header", 50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
}  # fmt: skip
_P_RID, _P_RECT, _P_TYPE, _P_NAME, _P_FOCUS, _P_ENABLED, _P_AID, _P_CLASS, _P_HELP = (
    30000, 30001, 30003, 30005, 30008, 30010, 30011, 30012, 30013,
)  # fmt: skip
_P_PASSWORD, _P_OFFSCREEN, _P_VALUE, _P_READONLY, _P_TOGGLE, _P_SELECTED = 30019, 30022, 30045, 30046, 30086, 30079
_P_ARIA, _P_ARIA_PROPS, _P_LANDMARK = 30101, 30102, 30157
_P_SCROLL_Y, _P_SCROLL_VIEW, _P_SCROLLABLE = 30055, 30056, 30058
_HAS = {
    30031: "Invoke", 30043: "Value", 30041: "Toggle", 30036: "SelectionItem",
    30028: "ExpandCollapse", 30034: "Scroll", 30035: "ScrollItem", 30090: "LegacyIAccessible",
}  # fmt: skip
_PAT = {
    "Invoke": 10000, "Value": 10002, "Toggle": 10015, "SelectionItem": 10010,
    "ExpandCollapse": 10005, "Scroll": 10004, "ScrollItem": 10017, "LegacyIAccessible": 10018,
}  # fmt: skip
_SEARCH_LANDMARK = 80004
_WM_KEYDOWN, _WM_KEYUP, _WM_APPCOMMAND = 0x0100, 0x0101, 0x0319
_APPCOMMAND_BROWSER_BACKWARD = 1


class UiaDriver(Driver):
    """The real thing. Every method that touches UI Automation is called on ScreenLink's one
    worker thread; the window list and the key state are plain Windows calls."""

    def __init__(self) -> None:
        self._local = threading.local()
        self._user32: Any = None

    # ---- plain Windows
    @property
    def u(self) -> Any:
        if self._user32 is None:
            # A user32 of our own, so argument types set here can't upset other users of
            # ctypes.windll.user32 (see hands.window_at).
            import ctypes.wintypes as wt

            u = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
            u.GetForegroundWindow.restype = wt.HWND
            u.GetAncestor.restype = wt.HWND
            u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
            u.WindowFromPoint.restype = wt.HWND
            u.WindowFromPoint.argtypes = [wt.POINT]
            u.IsIconic.argtypes = [wt.HWND]
            u.IsWindowVisible.argtypes = [wt.HWND]
            u.GetWindowTextLengthW.argtypes = [wt.HWND]
            u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
            u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
            u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            self._user32 = u
        return self._user32

    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import uiautomation  # noqa: F401
        except ImportError:
            return False
        return True

    def _pid(self, hwnd: int) -> int:
        import ctypes.wintypes as wt

        pid = wt.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _exe_path(self, pid: int) -> str:
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            return buf.value if ok else ""
        finally:
            kernel32.CloseHandle(handle)

    def windows(self) -> list[Window]:
        import ctypes.wintypes as wt

        u = self.u
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        found: list[Window] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def visit(hwnd: Any, _: Any) -> bool:
            if not u.IsWindowVisible(hwnd):
                return True
            cloaked = ctypes.c_int(0)
            dwm.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:  # on another virtual desktop
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if not n:
                return True
            exe = os.path.basename(self._exe_path(self._pid(hwnd))).lower()
            if exe not in BROWSERS:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            found.append(Window(int(hwnd), buf.value, exe, bool(u.IsIconic(hwnd))))
            return True

        u.EnumWindows(visit, 0)
        return found

    def foreground(self) -> int:
        hwnd = self.u.GetForegroundWindow()
        return int(self.u.GetAncestor(hwnd, 2) or 0) if hwnd else 0  # GA_ROOT

    def under_mouse(self) -> int:
        import ctypes.wintypes as wt

        point = wt.POINT()
        if not self.u.GetCursorPos(ctypes.byref(point)):
            return 0
        hwnd = self.u.WindowFromPoint(point)
        return int(self.u.GetAncestor(hwnd, 2) or 0) if hwnd else 0

    def is_own(self, hwnd: int) -> bool:
        from pyxi import hands

        return hands.is_own(hwnd)

    def esc_down(self) -> bool:
        return bool(self.u.GetAsyncKeyState(0x1B) & 0x8001)

    def post_key(self, hwnd: int, key: str) -> None:
        vk, scan, extended = KEYS[key]
        lparam = 1 | (scan << 16) | (1 << 24 if extended else 0)
        # Posted to that window's own message queue: it is never typed on the keyboard, so it
        # cannot reach another window whatever is in front by the time it is handled.
        self.u.PostMessageW(hwnd, _WM_KEYDOWN, vk, lparam)
        self.u.PostMessageW(hwnd, _WM_KEYUP, vk, lparam | 0xC0000000)

    def post_back(self, hwnd: int) -> None:
        self.u.PostMessageW(hwnd, _WM_APPCOMMAND, hwnd, _APPCOMMAND_BROWSER_BACKWARD << 16)

    def open(self, hwnd: int, url: str) -> None:
        pid = self._pid(hwnd)
        exe = self._exe_path(pid)
        if os.path.basename(exe).lower() not in BROWSERS:
            raise BrowserError("failed", "That isn't a browser window.")
        args = [exe, *_profile_args(_command_line(pid))]
        if BROWSERS[os.path.basename(exe).lower()][1] == "gecko":
            args += ["-new-tab", url]
        else:
            args += [url]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)

    # ---- UI Automation
    def _uia(self) -> Any:
        """UI Automation for this thread, with what is asked for every element in one go."""
        ready = getattr(self._local, "ready", None)
        if ready is None:
            from uiautomation import uiautomation as u

            self._local.init = u.UIAutomationInitializerInThread(debug=False)
            ui = u._AutomationClient.instance().IUIAutomation
            whole = ui.CreateCacheRequest()
            one = ui.CreateCacheRequest()
            kids = ui.CreateCacheRequest()
            for request in (whole, one, kids):
                for prop in (
                    _P_RID, _P_RECT, _P_TYPE, _P_NAME, _P_FOCUS, _P_ENABLED, _P_AID, _P_CLASS, _P_HELP,
                    _P_PASSWORD, _P_OFFSCREEN, _P_VALUE, _P_TOGGLE, _P_SELECTED, _P_ARIA,
                    _P_ARIA_PROPS, _P_LANDMARK, *_HAS,
                ):  # fmt: skip
                    request.AddProperty(prop)
                request.TreeFilter = ui.RawViewCondition  # groups and forms are only in the raw view
            whole.TreeScope = 7  # the element and everything under it
            one.TreeScope = 1
            kids.TreeScope = 3  # the element and its children
            ready = self._local.ready = (u, ui, whole, one, kids)
        return ready

    def _el(self, raw: Any, deep: bool, budget: list[int] | None = None) -> El:
        def get(prop: int) -> Any:
            try:
                return raw.GetCachedPropertyValue(prop)
            except Exception:
                return None

        role = _ROLES.get(int(get(_P_TYPE) or 0), "Custom")
        rect = get(_P_RECT) or (0, 0, 0, 0)
        try:
            left, top, width, height = (int(v) for v in rect[:4])
        except (TypeError, ValueError):
            left = top = width = height = 0
        props: dict[str, str] = {}
        for part in str(get(_P_ARIA_PROPS) or "").split(";"):
            key, _, value = part.partition("=")
            if key and value:
                props[key] = value
        patterns = frozenset(name for prop, name in _HAS.items() if get(prop))
        password = bool(get(_P_PASSWORD))
        checked: bool | None = None
        if role in ("RadioButton", "TabItem", "ListItem"):
            if "SelectionItem" in patterns:
                checked = bool(get(_P_SELECTED))
        elif role in ("CheckBox", "Button", "MenuItem") and "Toggle" in patterns:
            state = get(_P_TOGGLE)
            checked = state == 1 if state is not None else None
        el = El(
            role=role,
            name=str(get(_P_NAME) or "")[:400],
            aria=str(get(_P_ARIA) or ""),
            props=props,
            auto_id=str(get(_P_AID) or "")[:80],
            help=str(get(_P_HELP) or "")[:120],
            password=password,
            offscreen=bool(get(_P_OFFSCREEN)),
            enabled=get(_P_ENABLED) is not False,
            search_landmark=get(_P_LANDMARK) == _SEARCH_LANDMARK,
            patterns=patterns,
            # A private field's value is never taken out of the browser.
            value="" if password else str(get(_P_VALUE) or "")[:400] if "Value" in patterns else "",
            checked=checked,
            focused=bool(get(_P_FOCUS)),
            rect=(left, top, left + width, top + height),
            rid=tuple(int(v) for v in (get(_P_RID) or ())),
            handle=raw,
        )
        if deep:
            try:
                array = raw.GetCachedChildren()
                count = array.Length if array is not None else 0
            except Exception:
                array, count = None, 0
            for i in range(count):
                if budget is not None:
                    budget[0] -= 1
                    if budget[0] <= 0:
                        break
                el.children.append(self._el(array.GetElement(i), True, budget))
        return el

    def _documents(self, hwnd: int) -> list[tuple[Any, str, bool]]:
        """Every web document in the window, in reading order: (element, address, on
        screen). The browser's own shell is walked only as far as each document; a document
        is never looked into for another one."""
        _, ui, _, _, kids = self._uia()
        top = ui.ElementFromHandle(hwnd)
        found: list[tuple[Any, str, bool]] = []
        stack = [(top, 0)]
        looked = 0
        while stack and looked < 900:
            raw, depth = stack.pop()
            looked += 1
            try:
                node = raw.BuildUpdatedCache(kids)
                array = node.GetCachedChildren()
                count = array.Length if array is not None else 0
            except Exception:
                continue
            below = []
            for i in range(count):
                child = array.GetElement(i)
                try:
                    kind = int(child.GetCachedPropertyValue(_P_TYPE) or 0)
                    if kind == 50030:
                        cls = str(child.GetCachedPropertyValue(_P_CLASS) or "")
                        if cls not in _SHELL_VIEWS and child.GetCachedPropertyValue(30043):
                            address = str(child.GetCachedPropertyValue(_P_VALUE) or "").strip()
                            found.append((child, address, not child.GetCachedPropertyValue(_P_OFFSCREEN)))
                        continue
                except Exception:
                    continue
                if depth < 16:
                    below.append((child, depth + 1))
            stack.extend(reversed(below))
        return found

    def _find_document(self, hwnd: int) -> Any:
        """The web page in the window's visible tab: the first document on screen that is a
        web page. One of the browser's own (a dialog it put over the page, a side panel) is
        only the answer when there is no web page at all."""
        found = self._documents(hwnd)
        for want_web, want_showing in ((True, True), (True, False), (False, True), (False, False)):
            for raw, address, showing in found:
                if (bool(host_of(address)) or not want_web) and (showing or not want_showing):
                    return raw
        return None

    def document(self, hwnd: int) -> Doc | None:
        raw = self._find_document(hwnd)
        if raw is None:
            return None
        return self._doc(raw)

    def _doc(self, raw: Any) -> Doc:
        el = self._el(raw, False)
        return Doc(el.rid, el.value.strip(), el.name, el.props.get("busy") == "true", raw)

    def snapshot(self, hwnd: int, max_nodes: int, budget_s: float) -> Snapshot | None:
        _, _, whole, _, _ = self._uia()
        raw = self._find_document(hwnd)
        if raw is None:
            return None
        full = raw.BuildUpdatedCache(whole)
        budget = [max_nodes]
        root = self._el(full, True, budget)
        doc = Doc(root.rid, root.value.strip(), root.name, root.props.get("busy") == "true", raw)
        return Snapshot(doc, root, self.scroll_state(doc), cut=budget[0] <= 0)

    def state(self, handle: Any, doc: tuple[int, ...]) -> El | None:
        _, ui, _, one, _ = self._uia()
        try:
            now = self._el(handle.BuildUpdatedCache(one), False)
            walker = ui.RawViewWalker
            up = handle
            for _ in range(80):
                up = walker.GetParentElementBuildCache(up, one)
                if not up:
                    return None
                if tuple(int(v) for v in (up.GetCachedPropertyValue(_P_RID) or ())) == doc:
                    return now
        except Exception:
            return None
        return None

    def _pattern(self, handle: Any, name: str) -> Any:
        u = self._uia()[0]
        raw = handle.GetCurrentPattern(_PAT[name])
        if not raw:
            raise LookupError(name)
        return u.CreatePattern(_PAT[name], raw)

    def do(self, handle: Any, way: str, text: str = "") -> None:
        if way == "invoke":
            self._pattern(handle, "Invoke").Invoke(waitTime=0)
        elif way == "legacy":
            self._pattern(handle, "LegacyIAccessible").DoDefaultAction(waitTime=0)
        elif way == "toggle":
            self._pattern(handle, "Toggle").Toggle(waitTime=0)
        elif way == "select":
            self._pattern(handle, "SelectionItem").Select(waitTime=0)
        elif way == "expand":
            self._pattern(handle, "ExpandCollapse").Expand(waitTime=0)
        elif way == "collapse":
            self._pattern(handle, "ExpandCollapse").Collapse(waitTime=0)
        elif way == "value":
            self._pattern(handle, "Value").SetValue(text, waitTime=0)
        elif way == "legacy_value":
            self._pattern(handle, "LegacyIAccessible").SetValue(text, waitTime=0)
        elif way == "focus":
            handle.SetFocus()
        elif way == "legacy_focus":
            self._pattern(handle, "LegacyIAccessible").Select(1, waitTime=0)  # SELFLAG_TAKEFOCUS
        elif way == "into_view":
            self._pattern(handle, "ScrollItem").ScrollIntoView(waitTime=0)
        else:
            raise LookupError(way)

    def options(self, handle: Any) -> list[El]:
        _, _, whole, _, _ = self._uia()
        try:
            return _options_of(self._el(handle.BuildUpdatedCache(whole), True, [400]))
        except Exception:
            return []

    def has_children(self, handle: Any) -> bool:
        _, _, _, _, kids = self._uia()
        try:
            array = handle.BuildUpdatedCache(kids).GetCachedChildren()
            return bool(array is not None and array.Length)
        except Exception:
            return False

    def scroll_state(self, doc: Doc) -> tuple[float, float] | None:
        try:
            pattern = self._pattern(doc.handle, "Scroll")
            if not pattern.VerticallyScrollable:
                return (0.0, 100.0)
            return (float(pattern.VerticalScrollPercent), float(pattern.VerticalViewSize))
        except Exception:
            return None

    def scroll(self, doc: Doc, direction: str) -> bool | None:
        try:
            pattern = self._pattern(doc.handle, "Scroll")
        except Exception:
            return self._scroll_by_element(doc, direction)
        if direction in ("top", "bottom"):
            pattern.SetScrollPercent(-1, 0 if direction == "top" else 100, waitTime=0)
        else:
            pattern.Scroll(2, 3 if direction == "down" else 0, waitTime=0)  # no change across; a page down or up
        return True

    def _scroll_by_element(self, doc: Doc, direction: str) -> bool | None:
        """Where the page has no Scroll of its own (Firefox): bring into view the first
        element past the edge of what is showing."""
        _, _, whole, _, _ = self._uia()
        try:
            root = self._el(doc.handle.BuildUpdatedCache(whole), True, [4000])
        except Exception:
            return None
        flat: list[El] = []
        stack = [root]
        while stack:
            node = stack.pop()
            if node is not root and "ScrollItem" in node.patterns and _has_size(node):
                flat.append(node)
            stack.extend(reversed(node.children))
        if not flat:
            return None
        top, bottom = root.rect[1], root.rect[3]
        if direction == "top":
            target = flat[0]
        elif direction == "bottom":
            target = flat[-1]
        elif direction == "down":
            target = next((e for e in flat if e.rect[1] >= bottom - 4), flat[-1])
        else:
            above = [e for e in flat if e.rect[3] <= top + 4]
            height = bottom - top
            target = next((e for e in above if e.rect[1] >= top - height), above[0] if above else flat[0])
        try:
            self._pattern(target.handle, "ScrollItem").ScrollIntoView(waitTime=0)
        except Exception:
            return None
        return True


def _command_line(pid: int) -> str:
    """The command line a process was started with (to open an address in the same browser
    profile), or ""."""
    try:
        import ctypes.wintypes as wt

        class _UnicodeString(ctypes.Structure):
            _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", wt.LPWSTR)]

        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wt.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = wt.ULONG(0)
            ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
            ntdll.NtQueryInformationProcess(wt.HANDLE(handle), 60, None, 0, ctypes.byref(size))
            if not size.value:
                return ""
            buf = ctypes.create_string_buffer(size.value)
            if ntdll.NtQueryInformationProcess(wt.HANDLE(handle), 60, buf, size, ctypes.byref(size)) != 0:
                return ""
            return ctypes.cast(buf, ctypes.POINTER(_UnicodeString)).contents.Buffer or ""
        finally:
            kernel32.CloseHandle(wt.HANDLE(handle))
    except Exception:
        return ""


def _profile_args(command_line: str) -> list[str]:
    """The part of a browser's command line that says which profile it runs: an address
    handed to the browser's program goes to that same browser, not to another profile."""
    if not command_line:
        return []
    try:
        import ctypes.wintypes as wt

        shell32 = ctypes.WinDLL("shell32")  # type: ignore[attr-defined]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(wt.LPWSTR)
        count = ctypes.c_int(0)
        argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(count))
        args = [argv[i] for i in range(count.value)]
        ctypes.WinDLL("kernel32").LocalFree(argv)  # type: ignore[attr-defined]
    except Exception:
        return []
    keep: list[str] = []
    for i, arg in enumerate(args[1:], start=1):
        low = arg.lower()
        if low.startswith(("--user-data-dir=", "--profile-directory=")):
            keep.append(arg)
        elif low in ("-profile", "--profile", "-p") and i + 1 < len(args):
            keep += [arg, args[i + 1]]
    return keep
