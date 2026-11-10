"""Pyxi in the tab on screen, with no browser extension: measured in real browsers.

pyxi/screenlink.py does what the extension does (read a page, click, type, pick, scroll)
through Windows UI Automation, in the tab that is showing. This script says how dependable
that is. It starts each browser itself with a throwaway profile (the installed Chrome, Edge
and Firefox, and Playwright's Chromium if a copy of it is around), opens local test pages,
and for every kind of action records three things:

  found      the element was in the outline Pyxi reads, with the right kind and words;
  acted      the action was accepted (no refusal, no error);
  confirmed  the page itself says it happened. Every test page reports its own state (field
             values, what its event listeners heard, where it is scrolled) to this script's
             local server, so a result is read back from the page, never assumed.

It also records, for every action, whether the browser window took the keyboard focus, and
runs a few actions with the window covered and minimised. At the end it prints a table of
rates per browser and per action and writes it to docs/SCREEN-TAB-CHECK.md.

    python scripts/screen_tab_check.py                       # Chrome, Edge, Firefox, 3 rounds
    python scripts/screen_tab_check.py --browsers firefox --repeat 1
    python scripts/screen_tab_check.py --front               # also the key presses that need
                                                             # the window in front (takes the
                                                             # keyboard for a few seconds)
    python scripts/screen_tab_check.py --public              # plus a read-only pass on a few
                                                             # public pages (no sign-in)
    python scripts/screen_tab_check.py --real-model --env-dir <folder with .env>
                                                             # plus a few small tasks with the
                                                             # real model (cents)

Safety: the only windows this script can see or touch are those of the browser processes it
started (the driver it gives ScreenLink lists no others), every profile is temporary, the
pages are served from here, and nothing touches the installed Pyxi's data. The windows are
small, sit in the bottom right corner on top of other windows (a covered Chrome window stops
answering for its page) and are put there without taking the keyboard. A browser can still
take it by itself, when it starts and on some actions: Windows only allows that while nobody
is typing or clicking, and every time it happens is counted.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import browser_e2e as e2e  # noqa: E402  (the extension's test pages are used here too)

from pyxi import screenlink  # noqa: E402
from pyxi.browserlink import BrowserError, host_of  # noqa: E402

EXES = {
    "chrome": Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    "edge": Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    "firefox": Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"),
    "chromium": Path(tempfile.gettempdir()) / "pyxi-chromium-1248" / "chrome.exe",
}
SIZE = (820, 640)

# ---------------------------------------------------------------------- the pages
# Put at the end of every page: it tells this script what the page's own state is, a few
# times a second, and goes where it is told (so no page is ever steered from outside).
REPORTER = """<script>
(() => {
  const NAME = "__NAME__";
  const tab = sessionStorage.getItem("__tab") || Math.random().toString(36).slice(2);
  sessionStorage.setItem("__tab", tab);
  const load = Math.random().toString(36).slice(2);
  window.__events = window.__events || [];
  window.__flags = window.__flags || {};
  const snap = () => {
    const fields = {};
    for (const el of document.querySelectorAll("input, select, textarea")) {
      const key = el.name || el.id;
      if (!key) continue;
      if (el.type === "checkbox") fields[key] = el.checked;
      else if (el.type === "radio") { if (el.checked) fields[key] = el.value; else if (!(key in fields)) fields[key] = null; }
      else if (el.type === "file") fields[key] = el.files.length;
      else fields[key] = el.value;
    }
    const text = {};
    for (const el of document.querySelectorAll("[id]")) {
      if (/^(P|DIV|SPAN|H1|H2|OUTPUT|BUTTON)$/.test(el.tagName) && el.children.length === 0) text[el.id] = el.textContent.slice(0, 200);
    }
    const root = document.scrollingElement || document.documentElement;
    const at = document.activeElement;
    return { tab, load, page: NAME, url: location.href, title: document.title, fields, text,
      events: window.__events.slice(-60), flags: window.__flags,
      scrollY: Math.round(root.scrollTop), scrollMax: Math.round(root.scrollHeight - root.clientHeight),
      hidden: document.hidden, focus: document.hasFocus(), active: (at && (at.name || at.id || at.tagName)) || "",
      top: window === window.top };
  };
  const tick = async () => {
    try {
      const res = await fetch("/report", { method: "POST", body: JSON.stringify(snap()), keepalive: true });
      const cmd = await res.json();
      if (cmd.go && window === window.top) location.href = cmd.go;
    } catch (e) { /* the check ended */ }
  };
  setInterval(tick, 150);
  tick();
})();
</script>"""

PLAIN = """<!doctype html><title>Order a cake</title>
<h1>Order a cake</h1>
<form id="f">
 <p><label>Name on the cake <input name="cakename"></label></p>
 <p><label>Message <textarea name="message"></textarea></label></p>
 <p><label>How many <input type="number" name="qty" value="1"></label></p>
 <fieldset><legend>Size</legend>
  <label><input type="radio" name="size" value="small"> Small</label>
  <label><input type="radio" name="size" value="large"> Large</label></fieldset>
 <p><label><input type="checkbox" name="candles"> Add candles</label></p>
 <p><label>Flavour <select name="flavour"><option>Vanilla</option><option>Chocolate</option><option>Lemon</option></select></label></p>
 <p><button type="button" id="preview">Preview</button> <button type="submit">Place order</button></p>
</form>
<p id="state">Nothing yet</p>
<script>
 window.__events = [];
 const f = document.getElementById("f");
 f.addEventListener("input", (e) => __events.push("input:" + e.target.name + (e.isTrusted ? "" : ":untrusted")));
 f.addEventListener("change", (e) => __events.push("change:" + e.target.name + "=" + (e.target.type === "checkbox" ? e.target.checked : e.target.value)));
 document.getElementById("preview").addEventListener("click", () => {
   document.getElementById("state").textContent = "Preview: " + new FormData(f).get("cakename");
 });
 f.addEventListener("submit", (e) => {
   e.preventDefault();
   document.getElementById("state").textContent = "ORDER PLACED for " + new FormData(f).get("cakename");
 });
</script>"""

# A controlled input, the way React does it: the page's state is the truth. The field is put
# back to the state ten times a second, the state only changes on a real (trusted) input
# event, and an input event is ignored when the field holds what the page last put there
# itself (React's value tracker, the thing that makes "set .value and fire an event" fail).
REACT = """<!doctype html><title>Profile</title>
<h1>Profile</h1>
<p><label>Display name <input id="dn"></label></p>
<p id="shown">State: (empty)</p>
<script>
 window.__events = [];
 const el = document.getElementById("dn");
 let state = "", tracked = "";
 const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
 Object.defineProperty(el, "value", {
   get() { return desc.get.call(this); },
   set(v) { tracked = String(v); desc.set.call(this, v); },
 });
 el.addEventListener("input", (e) => {
   if (!e.isTrusted) { __events.push("ignored: not a real event"); return; }
   const now = desc.get.call(el);
   if (now === tracked) { __events.push("ignored: nothing new"); return; }
   tracked = now; state = now;
   document.getElementById("shown").textContent = "State: " + state;
 });
 setInterval(() => { if (desc.get.call(el) !== state) { desc.set.call(el, state); tracked = state; } }, 100);
</script>"""

DROPDOWN = """<!doctype html><title>Shipping</title>
<h1>Shipping</h1>
<div id="trigger" style="cursor:pointer;border:1px solid #888;padding:6px;width:220px">Choose a country</div>
<div id="list" hidden>
 <div class="opt" style="cursor:pointer;padding:4px">Canada</div>
 <div class="opt" style="cursor:pointer;padding:4px">Bangladesh</div>
 <div class="opt" style="cursor:pointer;padding:4px">Japan</div>
</div>
<p id="picked">Picked: (none)</p>
<h2>Size</h2>
<div id="cb" role="combobox" aria-expanded="false" aria-haspopup="listbox" aria-controls="lb" tabindex="0"
     style="border:1px solid #888;padding:6px;width:220px">Choose a size</div>
<div id="lb" role="listbox" hidden>
 <div role="option" style="padding:4px">Small</div>
 <div role="option" style="padding:4px">Medium</div>
 <div role="option" style="padding:4px">Large</div>
</div>
<p id="picked2">Size: (none)</p>
<script>
 const list = document.getElementById("list");
 document.getElementById("trigger").addEventListener("click", () => { list.hidden = !list.hidden; });
 for (const o of list.querySelectorAll(".opt")) o.addEventListener("click", () => {
   document.getElementById("picked").textContent = "Picked: " + o.textContent; list.hidden = true;
 });
 const cb = document.getElementById("cb"), lb = document.getElementById("lb");
 cb.addEventListener("click", () => { lb.hidden = !lb.hidden; cb.setAttribute("aria-expanded", String(!lb.hidden)); });
 for (const o of lb.querySelectorAll("[role=option]")) o.addEventListener("click", () => {
   document.getElementById("picked2").textContent = "Size: " + o.textContent; lb.hidden = true;
   cb.setAttribute("aria-expanded", "false");
 });
</script>"""

SPA = """<!doctype html><title>Set-up wizard</title>
<div id="app"></div>
<p id="state">step 1</p>
<script>
 const app = document.getElementById("app"), state = document.getElementById("state");
 let name = "";
 function step1() {
   app.innerHTML = '<h1>Step one</h1><p>What should we call you?</p>' +
     '<p><input id="n1" aria-label="Your name"></p><p><button id="next1">Next step</button></p>';
   document.getElementById("next1").addEventListener("click", () => {
     name = document.getElementById("n1").value; step2();
   });
 }
 function step2() {
   app.innerHTML = '<h1>Step two</h1><p>All set, ' + name + '?</p>' +
     '<p><button id="finish">Finish set-up</button> <button id="prev">Previous step</button></p>';
   state.textContent = "step 2";
   document.getElementById("finish").addEventListener("click", () => { state.textContent = "FINISHED " + name; });
   document.getElementById("prev").addEventListener("click", () => { state.textContent = "step 1"; step1(); });
 }
 step1();
</script>"""

FRAMES = """<!doctype html><title>Two frames</title>
<h1>Two frames</h1>
<p>The first frame is this site's own; the second is served from another host name.</p>
<iframe src="/inframe?who=same" title="Same site frame" style="width:380px;height:90px"></iframe>
<iframe src="http://localhost:__PORT__/inframe?who=other" title="Other site frame" style="width:380px;height:90px"></iframe>
<p id="state">idle</p>"""

INFRAME = """<!doctype html><title>Frame __WHO__</title>
<p><label>Note in the __WHO__ frame <input name="note"></label>
<button id="fb">Save in the __WHO__ frame</button></p><p id="fstate">idle</p>
<script>
 document.getElementById("fb").addEventListener("click", () => {
   document.getElementById("fstate").textContent = "saved: " + document.querySelector("input").value;
 });
</script>"""

LONG = (
    """<!doctype html><title>A long read</title><h1>A long read</h1>"""
    + "".join(f"<p>Paragraph {n}: the quick brown fox jumps over the lazy dog, again and again.</p>" for n in range(1, 121))
    + """<p><button id="bottom">Bottom button</button></p><p>The very last line.</p><p id="state">idle</p>
<script>
 document.getElementById("bottom").addEventListener("click", () => {
   document.getElementById("state").textContent = "bottom clicked";
 });
</script>"""
)

MODAL = """<!doctype html><title>Dialogs</title>
<h1>Dialogs</h1>
<p><button id="open1">Open the dialog</button> <button id="open2">Open the overlay</button></p>
<dialog id="d1"><form method="dialog">
 <p><label>Nickname <input name="nick"></label></p>
 <p><button value="ok" id="ok1">Save nickname</button> <button value="cancel">Never mind</button></p>
</form></dialog>
<div id="ov" role="dialog" aria-modal="true" aria-label="Are you sure" hidden
     style="position:fixed;inset:0;background:rgba(0,0,0,.5)">
 <div style="background:#fff;margin:80px auto;width:300px;padding:16px">
  <p>Really go ahead?</p><button id="yes">Yes, go ahead</button> <button id="no">No, wait</button></div></div>
<p id="state">idle</p>
<script>
 const state = document.getElementById("state"), d1 = document.getElementById("d1"), ov = document.getElementById("ov");
 document.getElementById("open1").addEventListener("click", () => d1.showModal());
 d1.addEventListener("close", () => {
   state.textContent = "dialog closed: " + d1.returnValue + " nick=" + d1.querySelector("input").value;
 });
 document.getElementById("open2").addEventListener("click", () => { ov.hidden = false; });
 document.getElementById("yes").addEventListener("click", () => { ov.hidden = true; state.textContent = "overlay: yes"; });
 document.getElementById("no").addEventListener("click", () => { ov.hidden = true; state.textContent = "overlay: no"; });
 addEventListener("keydown", (e) => { if (e.key === "Escape" && !ov.hidden) { ov.hidden = true; state.textContent = "overlay: escape"; } });
</script>"""

SHADOW = """<!doctype html><title>Components</title>
<h1>Components</h1>
<open-card></open-card>
<closed-card></closed-card>
<p id="state">idle</p>
<script>
 const state = document.getElementById("state");
 window.__flags = {};
 customElements.define("open-card", class extends HTMLElement {
   connectedCallback() {
     const root = this.attachShadow({ mode: "open" });
     root.innerHTML = '<p><label>Shadow name <input id="sn"></label> <button id="sb">Shadow save</button></p>';
     root.getElementById("sn").addEventListener("input", (e) => { __flags.shadowName = e.target.value; });
     root.getElementById("sb").addEventListener("click", () => {
       state.textContent = "open saved: " + root.getElementById("sn").value;
     });
   }
 });
 customElements.define("closed-card", class extends HTMLElement {
   connectedCallback() {
     const root = this.attachShadow({ mode: "closed" });
     root.innerHTML = '<p><button>Closed shadow button</button></p>';
     root.querySelector("button").addEventListener("click", () => { state.textContent = "closed clicked"; });
   }
 });
</script>"""

RICH = """<!doctype html><title>Write a note</title>
<h1>Write a note</h1>
<p>With a role, the way mail and chat sites mark their boxes:</p>
<div id="ed" contenteditable="true" role="textbox" aria-multiline="true" aria-label="Message body"
     style="border:1px solid #888;min-height:40px;padding:6px"></div>
<p id="mirror">(nothing written)</p>
<p>And a bare one:</p>
<div id="ed2" contenteditable="true" aria-label="Plain note" style="border:1px solid #888;min-height:40px;padding:6px"></div>
<p id="mirror2">(nothing written)</p>
<script>
 for (const [box, out] of [["ed", "mirror"], ["ed2", "mirror2"]]) {
   const ed = document.getElementById(box);
   ed.addEventListener("input", () => { document.getElementById(out).textContent = "Written: " + ed.textContent; });
 }
</script>"""

# Controls that listen for something other than a click, the way some component libraries do
# (a menu that opens on pointerdown, a button on mousedown, a menu that shows on hover).
EVENTS = """<!doctype html><title>Other events</title>
<h1>Other events</h1>
<p><button id="md">Opens on mouse down</button> <button id="pd">Opens on pointer down</button></p>
<div id="hov" style="border:1px solid #888;padding:6px;width:200px">Account menu
 <div id="sub" hidden><button id="so">Sign-in options</button></div></div>
<p id="state">idle</p>
<script>
 const state = document.getElementById("state");
 document.getElementById("md").addEventListener("mousedown", () => { state.textContent = "mouse down heard"; });
 document.getElementById("pd").addEventListener("pointerdown", () => { state.textContent = "pointer down heard"; });
 const hov = document.getElementById("hov"), sub = document.getElementById("sub");
 hov.addEventListener("mouseenter", () => { sub.hidden = false; });
 hov.addEventListener("mouseleave", () => { sub.hidden = true; });
 document.getElementById("so").addEventListener("click", () => { state.textContent = "hover menu used"; });
</script>"""

LINKS = """<!doctype html><title>Links</title>
<h1>Links</h1>
<p><a href="/plain" target="_blank" rel="noopener">Open the cake form in a new tab</a></p>
<p><a href="/long">A long read</a></p>"""

PAGES: dict[str, str] = {
    "/form": e2e.FORM,
    "/list": e2e.LIST,
    "/frame": e2e.FRAME,
    "/signup": e2e.SIGNUP,
    "/private": e2e.PRIVATE,
    "/plain": PLAIN,
    "/react": REACT,
    "/dropdown": DROPDOWN,
    "/spa": SPA,
    "/frames": FRAMES,
    "/inframe": INFRAME,
    "/long": LONG,
    "/modal": MODAL,
    "/shadow": SHADOW,
    "/rich": RICH,
    "/links": LINKS,
    "/events": EVENTS,
    "/start": "<!doctype html><title>Ready</title><h1>Ready</h1><p>The check starts here.</p>",
}


class Site:
    """The local test site, and what each open page last said about itself."""

    def __init__(self) -> None:
        self.tabs: dict[str, dict[str, Any]] = {}  # top-level pages, by tab
        self.frames: dict[str, dict[str, Any]] = {}  # pages inside frames, by name
        self.active = ""  # the tab that last said it is showing
        self.go_to: tuple[str, str] | None = None  # (tab, address) waiting to be handed over
        site = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: bytes, kind: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path, _, query = self.path.partition("?")
                if path == "/slow":
                    time.sleep(3)
                if path == "/hop":  # sends the browser on to another host name, as a bare domain does to www.
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{site.port}/plain")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = PAGES.get(path, "<!doctype html><title>Blank</title><p>Nothing here.</p>")
                who = "other" if "who=other" in query else "same"
                name = path.strip("/") + (f"-{who}" if path == "/inframe" else "")
                body = body.replace("__PORT__", str(site.port)).replace("__WHO__", who)
                body = body.replace("__ATTACK__", "") + REPORTER.replace("__NAME__", name)
                self._send(body.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length") or 0)
                answer: dict[str, Any] = {}
                try:
                    said = json.loads(self.rfile.read(size))
                    said["at"] = time.monotonic()
                    if said.get("top"):
                        site.tabs[said["tab"]] = said
                        if not said.get("hidden") or not site.active:
                            site.active = said["tab"]
                        if site.go_to and site.go_to[0] == said["tab"]:
                            answer = {"go": site.go_to[1]}
                            site.go_to = None
                    else:
                        site.frames[said["page"]] = said
                except (ValueError, KeyError):
                    pass
                self._send(json.dumps(answer).encode(), "application/json")

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def state(self) -> dict[str, Any]:
        """What the page in the tab that is showing last said about itself."""
        return self.tabs.get(self.active) or {}

    async def go(self, path: str, timeout: float = 20.0) -> bool:
        """Send the showing tab to a page (the page goes there itself) and wait for it."""
        old = self.state().get("load")
        self.go_to = (self.active, self.url(path))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            now = self.state()
            if now.get("load") != old and now.get("url", "").split("?")[0].endswith(path):
                return True
            await asyncio.sleep(0.05)
        return False


# ---------------------------------------------------------------------- the browsers
user32 = ctypes.WinDLL("user32")
user32.GetForegroundWindow.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def version_of(exe: Path) -> str:
    if exe.name == "firefox.exe":
        try:
            text = (exe.parent / "application.ini").read_text(encoding="utf-8", errors="replace")
            return re.search(r"^Version=(.+)$", text, re.M).group(1).strip()  # type: ignore[union-attr]
        except (OSError, AttributeError):
            return "?"
    found = sorted(
        (p.name for p in exe.parent.iterdir() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", p.name)),
        key=lambda v: [int(x) for x in v.split(".")],
    )
    return found[-1] if found else "?"


class Browser:
    """One browser this script started, in a profile made for this run."""

    def __init__(self, name: str, tmp: Path, extra: tuple[str, ...] = ()) -> None:
        self.name = name
        self.extra = extra  # more command line, e.g. to start in a private window
        self.exe = EXES[name]
        self.engine = "gecko" if name == "firefox" else "chromium"
        self.profile = tmp / f"profile-{name}{'-private' if extra else ''}"
        self.profile.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen[bytes] | None = None
        self.hwnd = 0
        self.focus_at_start = False
        self.other = 0  # the window that had the keyboard before this browser took it
        self.site: Any = None
        self.version = version_of(self.exe)

    def pids(self) -> set[int]:
        """The processes of this browser: the one started here and its children, and (a
        browser that hands over to a new process) any whose command line names this run's
        profile folder. Never anything else."""
        import psutil

        out: set[int] = set()
        if self.proc is not None:
            out.add(self.proc.pid)
            try:
                out |= {c.pid for c in psutil.Process(self.proc.pid).children(recursive=True)}
            except psutil.Error:
                pass
        mark = str(self.profile).lower()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if mark in " ".join(p.info["cmdline"] or []).lower():
                    out.add(p.info["pid"])
            except (psutil.Error, TypeError):
                pass
        return out

    def windows(self) -> list[int]:
        pids = self.pids()
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def visit(hwnd: Any, _: Any) -> bool:
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
                found.append(int(hwnd))
            return True

        user32.EnumWindows(visit, 0)
        return found

    def start(self, url: str) -> None:
        if self.name == "firefox":
            prefs = {
                "browser.shell.checkDefaultBrowser": False,
                "browser.aboutwelcome.enabled": False,
                "browser.startup.homepage_override.mstone": "ignore",
                "datareporting.policy.dataSubmissionEnabled": False,
                "datareporting.policy.dataSubmissionPolicyBypassNotification": True,
                "datareporting.healthreport.uploadEnabled": False,
                "toolkit.telemetry.reportingpolicy.firstRun": False,
                "app.update.auto": False,
                "browser.sessionstore.resume_from_crash": False,
                "browser.tabs.warnOnClose": False,
                "termsofuse.bypassNotification": True,
                "browser.link.open_newwindow.restriction": 0,
            }
            (self.profile / "user.js").write_text(
                "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in prefs.items()), encoding="utf-8"
            )
            args = [str(self.exe), "-profile", str(self.profile), "-width", str(SIZE[0]), "-height", str(SIZE[1]), *self.extra, url]
        else:
            args = [
                str(self.exe),
                f"--user-data-dir={self.profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
                # A fresh profile's own first-run dialogs (pick a search engine, ad privacy)
                # would sit on top of the test pages.
                "--disable-search-engine-choice-screen",
                "--disable-features=PrivacySandboxSettings4,msEdgeWelcomePage",
                "--hide-crash-restore-bubble",
                f"--window-size={SIZE[0]},{SIZE[1]}",
                *self.extra,
                url,
            ]
        self.other = foreground()
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.monotonic() + 60
        while time.monotonic() < end and not self.hwnd:
            time.sleep(0.05)
            wins = self.windows()
            self.hwnd = wins[0] if wins else 0
        if not self.hwnd:
            raise RuntimeError(f"{self.name} never showed a window")
        self.place()
        end = time.monotonic() + 4  # browsers take the keyboard a moment after the window shows
        while time.monotonic() < end and not self.focus_at_start:
            if foreground() == self.hwnd:
                self.focus_at_start = True
            time.sleep(0.1)
        if self.focus_at_start:
            self.hand_back(force=True)  # once: a browser bounced about while starting misses requests
            time.sleep(1.0)

    def place(self, on_top: bool = True) -> None:
        """Small, bottom right, and (unless being covered on purpose) above other windows,
        without taking the keyboard."""
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        user32.SetWindowPos(
            self.hwnd, wt.HWND(-1 if on_top else -2), width - SIZE[0] - 8, height - SIZE[1] - 56, SIZE[0], SIZE[1],
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )  # fmt: skip
        if not on_top:
            user32.SetWindowPos(self.hwnd, wt.HWND(1), 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

    def hand_back(self, force: bool = False) -> bool:
        """Is this window behind (not holding the keyboard)? In the main runs a browser that
        takes the keyboard is counted and left alone: Windows itself refuses to let a window
        come to the front while the person at the PC is typing or clicking, so it only
        happens when they aren't, and minimising a browser under its own feet makes it miss
        requests. (Marking the window never-activate was tried: Chrome then minimises itself
        when a dropdown opens.) With ``force``, for the few runs where it must start behind,
        it is minimised, which makes Windows give the keyboard to the window behind, and
        brought back without activating."""
        now = foreground()
        if now != self.hwnd:
            self.other = now or self.other
            if user32.IsIconic(self.hwnd):
                self.restore()
            return True
        if not force:
            return False
        user32.ShowWindow(self.hwnd, 6)  # SW_MINIMIZE
        end = time.monotonic() + 1.0
        while not user32.IsIconic(self.hwnd) and time.monotonic() < end:
            time.sleep(0.05)
        time.sleep(0.25)
        self.restore()
        return foreground() != self.hwnd

    def restore(self) -> None:
        user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        end = time.monotonic() + 1.5
        while user32.IsIconic(self.hwnd) and time.monotonic() < end:
            time.sleep(0.05)
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            time.sleep(0.4)
        self.place()
        time.sleep(0.25)

    def show(self) -> bool:
        """Make sure the page is really showing. Chrome and Edge decide for themselves
        whether their window can be seen, and a window that started behind others stays
        "hidden" to them (and gives no page to read) until it is shown afresh."""
        for _ in range(3):
            self.hand_back()
            if self.site is None or not self.site.state().get("hidden"):
                return True
            user32.ShowWindow(self.hwnd, 6)
            time.sleep(0.4)
            self.restore()
            time.sleep(1.4)
        self.hand_back()
        return not self.site.state().get("hidden")

    def to_front(self) -> bool:
        user32.ShowWindow(self.hwnd, 6)
        time.sleep(0.2)
        user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE: comes back as the active window
        time.sleep(0.5)
        return foreground() == self.hwnd

    def stop(self) -> None:
        import psutil

        for pid in self.pids():
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass


class OwnWindows(screenlink.UiaDriver):
    """The real driver, able to see nothing but the windows of the browsers this script
    started. Everything ScreenLink does goes through ``windows()``, so the user's own
    browser windows can't be read or touched from here."""

    def __init__(self, browsers: list[Browser]) -> None:
        super().__init__()
        self.browsers = browsers

    def _mine(self) -> set[int]:
        return {hwnd for b in self.browsers for hwnd in b.windows()}

    def windows(self) -> list[screenlink.Window]:
        mine = self._mine()
        return [w for w in super().windows() if w.hwnd in mine]

    def foreground(self) -> int:
        hwnd = super().foreground()
        return hwnd if hwnd in self._mine() else 0

    def under_mouse(self) -> int:
        return 0

    def is_own(self, hwnd: int) -> bool:
        return False

    def open(self, hwnd: int, url: str) -> None:
        if hwnd not in self._mine():
            raise BrowserError("failed", "not a window of this check")
        super().open(hwnd, url)


# The other order of the ways to act, for the runs that compare the two.
PATTERNS_FIRST = {
    "press": ("invoke", "legacy"),
    "toggle": ("toggle", "legacy", "invoke"),
    "choose": ("select", "invoke", "legacy"),
    "open": ("expand", "legacy"),
    "type": ("value", "legacy_value"),
}
MODEL_CALLS = [0]  # real-model calls made so far, over all browsers


class Cover:
    """A plain window of this script's own, put over a browser window to cover it: an
    honest "covered by another window" that doesn't depend on what else is on screen."""

    def __init__(self) -> None:
        self.hwnd = 0
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)

    def _run(self) -> None:
        u = ctypes.WinDLL("user32")
        u.CreateWindowExW.restype = wt.HWND
        u.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
        ]  # fmt: skip
        # WS_EX_TOPMOST | WS_EX_NOACTIVATE, a popup with a border: opaque, never takes the keyboard.
        self.hwnd = int(u.CreateWindowExW(0x00000008 | 0x08000000, "STATIC", "Pyxi check: cover", 0x80800000, 0, 0, 10, 10, None, None, None, None) or 0)
        self.ready.set()
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    def over(self, hwnd: int) -> None:
        rect = wt.RECT()
        user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
        user32.SetWindowPos(
            self.hwnd, wt.HWND(-1), rect.left - 20, rect.top - 20, rect.right - rect.left + 40, rect.bottom - rect.top + 40,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )  # fmt: skip

    def away(self) -> None:
        user32.ShowWindow(self.hwnd, 0)

# ---------------------------------------------------------------------- measuring
@dataclass
class Row:
    browser: str
    group: str  # the kind of action
    case: str
    found: bool | None = None
    acted: bool | None = None
    confirmed: bool | None = None
    focus: bool = False  # the browser window took the keyboard
    ms: float = 0.0
    note: str = ""


ROWS: list[Row] = []
RESTORED: list[str] = []  # each time a window was found minimised when it shouldn't be


async def until(test: Any, timeout: float = 3.0, every: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while True:
        try:
            if test():
                return True
        except (KeyError, TypeError, AttributeError):
            pass
        if time.monotonic() > end:
            return False
        await asyncio.sleep(every)


def item_of(page: dict[str, Any], words: str, kind: str = "") -> dict[str, Any] | None:
    for item in page.get("items") or []:
        if words.lower() in str(item.get("label") or "").lower() and (not kind or item.get("kind") == kind):
            return item
    return None


class Check:
    """One browser being measured."""

    def __init__(self, browser: Browser, site: Site, link: screenlink.ScreenLink, opts: argparse.Namespace) -> None:
        self.b, self.site, self.link, self.opts = browser, site, link, opts
        self.hint = f"w{browser.hwnd}"
        self.steady = True  # False while a run minimises or covers the window on purpose

    # ---- the link, called the way tabwork's operator calls it
    def upright(self) -> None:
        """The window is meant to be showing for everything but the runs that minimise it on
        purpose. If something else minimised it (the person at the PC, or the browser still
        settling after this check moved it), it is put back and counted."""
        if self.steady and user32.IsIconic(self.b.hwnd):
            RESTORED.append(self.b.name)
            self.b.restore()

    async def read(self, **more: Any) -> dict[str, Any]:
        self.upright()
        return await self.link.call(
            "read", {"tab": self.b.hwnd, "max_chars": 6000, "max_items": 150, **more}, browser=self.hint
        )

    async def do(self, name: str, page: dict[str, Any], item: dict[str, Any] | None = None, **args: Any) -> dict[str, Any]:
        seen: dict[str, Any] = {"tab": self.b.hwnd, "page": page["page"], "host": host_of(page["tab"]["url"])}
        if item is not None:
            seen.update(ref=item["ref"], label=item["label"])
        self.upright()
        return await self.link.call(name, {**seen, **args}, browser=self.hint, timeout=30)

    def wanted(self, group: str, name: str = "") -> bool:
        words = [w.strip().lower() for w in self.opts.only.split(",") if w.strip()]
        return not words or any(w in f"{group} {name}".lower() for w in words)

    def seen(self) -> str:
        """For a failed read: what the window looked like to the check."""
        state = self.site.state()
        return (
            f"({self.link.last_trouble}; page says: {state.get('page')}, hidden={state.get('hidden')}; "
            f"window minimised={bool(user32.IsIconic(self.b.hwnd))}, in front={foreground() == self.b.hwnd})"
        )

    def row(self, group: str, case: str, **values: Any) -> Row:
        row = Row(self.b.name, group, case, **values)
        ROWS.append(row)
        mark = "ok  " if row.confirmed else ("--  " if row.confirmed is None else "FAIL")
        print(
            f"  {mark} {self.b.name:8s} {group:22s} {case:44s} found={_yn(row.found)} acted={_yn(row.acted)} "
            f"confirmed={_yn(row.confirmed)}{' FOCUS' if row.focus else ''}{'  [' + row.note[:200] + ']' if row.note else ''}",
            flush=True,
        )
        return row

    async def case(
        self,
        group: str,
        name: str,
        path: str | None,
        pick: Any,
        act: Any,
        confirm: Any,
        *,
        wait: float = 3.0,
        refuses: str = "",
        page: dict[str, Any] | None = None,
    ) -> Row:
        """One measured action: load the page fresh, look for the element in what Pyxi
        reads, act on it, and ask the page whether it happened. ``refuses`` names the
        refusal that is the right outcome (then "confirmed" means the page did not change)."""
        if not self.wanted(group, name):
            return Row(self.b.name, group, name)  # not asked for in this run
        self.b.hand_back()
        try:
            if path is not None:
                if not await self.site.go(path):
                    return self.row(group, name, found=False, note="the page didn't load")
                if self.site.state().get("hidden"):
                    self.b.show()
            page = page or await self.read()
            item = pick(page)
        except BrowserError as exc:
            return self.row(group, name, found=False, note=f"read {exc.code}: {exc} {self.seen()}")
        if item is None:
            return self.row(group, name, found=False, note="not in the outline")
        started = time.monotonic()
        acted, note = True, ""
        front_before = foreground() == self.b.hwnd
        try:
            await act(page, item)
        except BrowserError as exc:
            acted, note = False, f"{exc.code}: {exc}"
            if "isn't answering" in note:
                note = f"{exc.code}: the page isn't answering {self.seen()}"
        took = (time.monotonic() - started) * 1000
        focus = foreground() == self.b.hwnd and not front_before
        if refuses:
            right = not acted and note.startswith(refuses)
            await asyncio.sleep(0.5)
            unchanged = bool(confirm(self.site.state()))
            return self.row(
                group, name, found=True, acted=right, confirmed=right and unchanged, focus=focus, ms=took,
                note="" if right and unchanged else (note or "it was not refused"),
            )  # fmt: skip
        ok = acted and await until(lambda: confirm(self.site.state()), wait)
        return self.row(group, name, found=True, acted=acted, confirmed=bool(ok), focus=focus, ms=took, note=note)

    # ---- the cases
    async def round(self) -> None:
        s, link = self.site, self.link
        text = lambda key, want: lambda st: want in str(st["text"].get(key, ""))  # noqa: E731
        fld = lambda key, want: lambda st: st["fields"].get(key) == want  # noqa: E731

        # -- reading
        self.b.show()
        await s.go("/form")
        started = time.monotonic()
        try:
            page = await self.read(max_chars=12000)
            words = page["text"]
            ok = (
                "We meet on the first Tuesday" in words
                and "HIDDEN-NOTE-42" not in words
                and "4111" not in words
                and "COOKIE-SECRET" not in words
                and "STORAGE-SECRET" not in words
            )
            kinds = {i["label"]: i["kind"] for i in page["items"]}
            self.row(
                "read", "text, nothing hidden, card number masked", found=True, acted=True, confirmed=ok,
                ms=(time.monotonic() - started) * 1000, note="" if ok else words[:200],
            )  # fmt: skip
            right = (
                kinds.get("Full name") == "text field"
                and kinds.get("Country") == "dropdown"
                and kinds.get("Send me the newsletter") == "checkbox"
                and kinds.get("Join now") == "button"
            )
            self.row("read", "outline: kinds and labels", found=True, acted=True, confirmed=right, note="" if right else str(kinds)[:300])
            join = item_of(page, "Join now") or {}
            self.row("read", "a form's button is marked as submitting", found=bool(join), acted=True, confirmed=bool(join.get("submits")))
            shell = [i["label"] for i in page["items"] if re.search(r"address and search|^back$|^reload$|search with|bookmark", i["label"], re.I)]
            self.row("refuse", "nothing outside the page is listed (toolbar, address bar)", found=True, acted=True, confirmed=not shell, note=str(shell))
        except BrowserError as exc:
            self.row("read", "text, nothing hidden, card number masked", found=False, note=f"{exc.code}: {exc}")

        await s.go("/long")
        try:
            started = time.monotonic()
            page = await self.read(max_chars=20000)
            whole = "Paragraph 120" in page["text"] and "The very last line." in page["text"]
            self.row("read", "a long page: text past the screen too", found=True, acted=True, confirmed=whole, ms=(time.monotonic() - started) * 1000)
        except BrowserError as exc:
            self.row("read", "a long page: text past the screen too", found=False, note=f"{exc.code}: {exc}")

        # -- buttons, links, things that are only clickable
        click = lambda page, item: self.do("click", page, item, settle=True)  # noqa: E731
        await self.case("button", "Add Milk (a plain button)", "/list", lambda p: item_of(p, "Add Milk", "button"), click, text("cart", "Cart: Milk"))
        await self.case("button", "Preview (type=button in a form)", "/plain", lambda p: item_of(p, "Preview", "button"), click, text("state", "Preview:"))
        await self.case("button", "Join now (a form's submit button)", "/form", lambda p: item_of(p, "Join now", "button"), click, text("result", "Joined as"))
        await self.case("button", "Bottom button, far off screen", "/long", lambda p: item_of(p, "Bottom button", "button"), click, text("state", "bottom clicked"))
        await self.case("clickable div", "Show opening hours (a div with a listener)", "/list", lambda p: item_of(p, "Show opening hours"), click, text("hours", "Open 7 to 5."))
        await self.case("link", "Go to the sign-up form", "/list", lambda p: item_of(p, "Go to the sign-up form", "link"), click, lambda st: st["page"] == "form", wait=8)

        # -- controls that don't listen for a click
        await self.case("other events", "a button that only hears mouse down", "/events", lambda p: item_of(p, "Opens on mouse down"), click, text("state", "mouse down heard"))
        await self.case("other events", "a button that only hears pointer down", "/events", lambda p: item_of(p, "Opens on pointer down"), click, text("state", "pointer down heard"))
        await self.case("other events", "a menu that only shows on hover", "/events", lambda p: item_of(p, "Sign-in options"), click, text("state", "hover menu used"))

        # -- text
        typed = lambda text, **more: lambda page, item: self.do("type", page, item, text=text, **more)  # noqa: E731
        await self.case("text field", "Full name", "/form", lambda p: item_of(p, "Full name", "text field"), typed("Dana Li"), fld("fullname", "Dana Li"))
        await self.case("text field", "Email (type=email)", "/form", lambda p: item_of(p, "Email", "text field"), typed("dana@example.com"), fld("email", "dana@example.com"))
        await self.case("text field", "Message (a textarea)", "/plain", lambda p: item_of(p, "Message"), typed("Happy birthday, Mum"), fld("message", "Happy birthday, Mum"))
        await self.case("text field", "How many (type=number)", "/plain", lambda p: item_of(p, "How many"), typed("3"), fld("qty", "3"))
        await self.case("text field", "Search groceries (type=search)", "/list", lambda p: item_of(p, "Search groceries"), typed("milk"), fld("q", "milk"))
        await self.case(
            "text field", "the page hears input and change events", "/plain", lambda p: item_of(p, "Name on the cake"), typed("Mina"),
            lambda st: "input:cakename" in st["events"] and st["fields"].get("cakename") == "Mina",
        )  # fmt: skip
        await self.case(
            "controlled input", "React-style field (state only from real input events)", "/react",
            lambda p: item_of(p, "Display name"), typed("Dana Li"),
            lambda st: st["text"].get("shown") == "State: Dana Li" and st["fields"].get("dn") == "Dana Li", wait=2.5,
        )  # fmt: skip
        await self.case(
            "rich text box", "contenteditable with role=textbox (a mail body)", "/rich",
            lambda p: item_of(p, "Message body"), typed("Hello there"), text("mirror", "Written: Hello there"),
        )  # fmt: skip
        await self.case(
            "rich text box", "a bare contenteditable div", "/rich",
            lambda p: item_of(p, "Plain note"), typed("Hello there"), text("mirror2", "Written: Hello there"),
        )  # fmt: skip

        # -- boxes, radio buttons, dropdowns
        await self.case("checkbox", "Send me the newsletter", "/form", lambda p: item_of(p, "newsletter", "checkbox"), click, fld("news", True))
        await self.case(
            "checkbox", "Add candles, and the page hears change", "/plain", lambda p: item_of(p, "Add candles", "checkbox"), click,
            lambda st: st["fields"].get("candles") is True and "change:candles=true" in st["events"],
        )  # fmt: skip
        await self.case(
            "radio button", "Large", "/plain", lambda p: item_of(p, "Large", "radio button"), click,
            lambda st: st["fields"].get("size") == "large" and "change:size=large" in st["events"],
        )  # fmt: skip
        pick = lambda option: lambda page, item: self.do("select", page, item, option=option)  # noqa: E731
        await self.case("dropdown", "Country: pick Canada (value)", "/form", lambda p: item_of(p, "Country", "dropdown"), pick("Canada"), fld("country", "Canada"))
        await self.case(
            "dropdown", "Flavour: pick Lemon, and the page hears change", "/plain", lambda p: item_of(p, "Flavour", "dropdown"), pick("Lemon"),
            lambda st: st["fields"].get("flavour") == "Lemon" and "change:flavour=Lemon" in st["events"],
        )  # fmt: skip

        async def open_then(page: dict[str, Any], item: dict[str, Any], words: str) -> None:
            await self.do("click", page, item, settle=True)
            await asyncio.sleep(0.3)
            again = await self.read()
            option = item_of(again, words)
            if option is None:
                raise BrowserError("failed", f"after opening it, {words!r} wasn't in the outline")
            await self.do("click", again, option, settle=True)

        await self.case(
            "custom dropdown", "made of divs: open it, pick Bangladesh", "/dropdown", lambda p: item_of(p, "Choose a country"),
            lambda page, item: open_then(page, item, "Bangladesh"), text("picked", "Picked: Bangladesh"),
        )  # fmt: skip
        await self.case(
            "custom dropdown", "ARIA combobox: open it, pick Large", "/dropdown", lambda p: item_of(p, "Choose a size"),
            lambda page, item: open_then(page, item, "Large"), text("picked2", "Size: Large"),
        )  # fmt: skip

        # -- a page that swaps its content without changing address
        async def wizard(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("type", page, item, text="Dana")
            await self.do("click", page, item_of(page, "Next step"), settle=True)
            await asyncio.sleep(0.3)
            again = await self.read()
            finish = item_of(again, "Finish set-up")
            if finish is None:
                raise BrowserError("failed", "the second step wasn't in the outline")
            await self.do("click", again, finish, settle=True)

        await self.case("single-page app", "two steps, same address", "/spa", lambda p: item_of(p, "Your name"), wizard, text("state", "FINISHED Dana"))

        async def stale(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("click", page, item, settle=True)  # the content is swapped
            await asyncio.sleep(0.4)
            await self.do("click", page, item)  # the same button again, which is gone now

        await self.case(
            "tied to what was read", "an element the page removed is refused", "/spa", lambda p: item_of(p, "Next step"), stale,
            text("state", "step 2"), refuses="gone",
        )  # fmt: skip

        async def new_words(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("click", page, item, settle=True)  # "Continue" becomes "Place order"
            await asyncio.sleep(0.4)
            await self.do("click", page, item)  # still asked for as "Continue"

        await self.case(
            "tied to what was read", "a button whose words changed is refused", "/signup", lambda p: item_of(p, "Continue", "button"), new_words,
            text("state", "Nothing sent"), refuses="gone",
        )  # fmt: skip

        async def other_page(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("click", page, item_of(page, "Go to the sign-up form"), settle=True)
            await until(lambda: s.state()["page"] == "form", 8)
            await self.do("click", page, item)  # "Add Milk", read on the page that is gone

        await self.case(
            "tied to what was read", "an action for the page before this one is refused", "/list", lambda p: item_of(p, "Add Milk"), other_page,
            lambda st: st["page"] == "form" and not st["text"].get("result"), refuses="gone",
        )  # fmt: skip

        # -- frames, dialogs, shadow trees
        frame = lambda key, want: lambda st: want in str(s.frames.get(key, {}).get("text", {}).get("fstate", ""))  # noqa: E731
        await self.case(
            "iframe", "same site: type in the frame's field", "/list", lambda p: item_of(p, "Note to self"), typed("buy stamps"),
            lambda st: s.frames.get("frame", {}).get("fields", {}).get("note") == "buy stamps",
        )  # fmt: skip
        await self.case("iframe", "same site: press the frame's button", "/frames", lambda p: item_of(p, "Save in the same frame"), click, frame("inframe-same", "saved:"))
        if self.wanted("iframe", "another site's frame"):
            await s.go("/frames")
            try:
                page = await self.read()
                left_out = item_of(page, "Save in the other frame") is None and item_of(page, "Note in the other frame") is None
                said = f"(a frame from http://localhost:{s.port}" in page["text"] and "Note in the other frame" not in page["text"]
                own = item_of(page, "Save in the same frame") is not None
                await asyncio.sleep(0.4)
                untouched = "saved" not in str(s.frames.get("inframe-other", {}).get("text", {}).get("fstate", ""))
                self.row(
                    "iframe", "another site's frame: left out of the page, its text not read", found=own, acted=left_out,
                    confirmed=left_out and said and untouched, note="" if said else page["text"][-160:],
                )  # fmt: skip
            except BrowserError as exc:
                self.row("iframe", "another site's frame: left out of the page, its text not read", found=False, note=f"{exc.code}: {exc}")

        async def dialog(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("click", page, item, settle=True)
            await asyncio.sleep(0.4)
            again = await self.read()
            nick = item_of(again, "Nickname")
            save = item_of(again, "Save nickname")
            if nick is None or save is None:
                raise BrowserError("failed", "the dialog's field or button wasn't in the outline")
            await self.do("type", again, nick, text="Dee")
            await self.do("click", again, save, settle=True)

        await self.case("modal dialog", "<dialog>: open, type, save", "/modal", lambda p: item_of(p, "Open the dialog"), dialog, text("state", "dialog closed: ok nick=Dee"))

        async def overlay(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("click", page, item, settle=True)
            await asyncio.sleep(0.4)
            again = await self.read()
            yes = item_of(again, "Yes, go ahead")
            if yes is None:
                raise BrowserError("failed", "the overlay's button wasn't in the outline")
            await self.do("click", again, yes, settle=True)

        await self.case("modal dialog", "an overlay made of divs: open, say yes", "/modal", lambda p: item_of(p, "Open the overlay"), overlay, text("state", "overlay: yes"))
        await self.case(
            "shadow DOM", "open shadow tree: type in its field", "/shadow", lambda p: item_of(p, "Shadow name"), typed("Ray"),
            lambda st: st["flags"].get("shadowName") == "Ray",
        )  # fmt: skip
        await self.case("shadow DOM", "open shadow tree: press its button", "/shadow", lambda p: item_of(p, "Shadow save"), click, text("state", "open saved:"))
        await self.case("shadow DOM", "closed shadow tree: press its button", "/shadow", lambda p: item_of(p, "Closed shadow button"), click, text("state", "closed clicked"))

        # -- scrolling
        scroll = lambda direction: lambda page, item: self.do("scroll", page, direction=direction)  # noqa: E731
        whole = lambda p: {"ref": "", "label": ""}  # noqa: E731  (the page itself)

        async def no_ref(act: Any) -> Any:
            return act

        async def scroll_page(page: dict[str, Any], _item: Any, direction: str) -> None:
            await self.link.call(
                "scroll", {"tab": self.b.hwnd, "page": page["page"], "host": host_of(page["tab"]["url"]), "direction": direction},
                browser=self.hint,
            )  # fmt: skip

        await self.case("scroll", "down a page", "/long", whole, lambda p, i: scroll_page(p, i, "down"), lambda st: st["scrollY"] > 150)
        await self.case("scroll", "to the bottom", "/long", whole, lambda p, i: scroll_page(p, i, "bottom"), lambda st: st["scrollY"] >= st["scrollMax"] - 60)

        async def down_then_top(page: dict[str, Any], item: Any) -> None:
            await scroll_page(page, item, "bottom")
            await until(lambda: s.state()["scrollY"] > 300, 3)
            await scroll_page(page, item, "top")

        await self.case("scroll", "to the bottom, then back to the top", "/long", whole, down_then_top, lambda st: st["scrollY"] < 40 and st["scrollMax"] > 300)
        await self.case(
            "scroll", "bring a named element into view", "/long", lambda p: item_of(p, "Bottom button"),
            lambda page, item: self.do("scroll", page, item), lambda st: st["scrollY"] > 300,
        )  # fmt: skip
        del scroll, no_ref

        # -- keys (posted to the window; Chrome and Edge only hear them in front)
        await self.keys(in_front=False)

        # -- going back
        async def there_and_back(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("click", page, item, settle=True)
            await until(lambda: s.state()["page"] == "form", 8)
            again = await self.read()
            await self.link.call(
                "back", {"tab": self.b.hwnd, "page": again["page"], "host": host_of(again["tab"]["url"])}, browser=self.hint, timeout=30
            )

        await self.case("back", "follow a link, then go back a page", "/list", lambda p: item_of(p, "Go to the sign-up form"), there_and_back, lambda st: st["page"] == "list", wait=8)

        # -- private fields
        await self.private()
        del link

    async def keys(self, in_front: bool) -> None:
        s = self.site
        typed_enter = lambda page, item: self.do("type", page, item, text="milk", enter=True, settle=True)  # noqa: E731
        front = lambda: self.b.to_front() if in_front else True  # noqa: E731

        async def run(name: str, path: str, pick: Any, act: Any, confirm: Any) -> None:
            self.b.hand_back()
            await s.go(path)
            if in_front and not front():
                self.row("keys (window in front)", name, found=None, note="the check couldn't bring the window to the front")
                return
            # Named for where the window really is when the key is pressed.
            group = f"keys (window {'in front' if foreground() == self.b.hwnd else 'not in front'})"
            try:
                await self.case(group, name, None, pick, act, confirm)
            finally:
                if in_front:
                    self.b.hand_back(force=True)

        await run("Enter in a search box", "/list", lambda p: item_of(p, "Search groceries"), typed_enter, lambda st: st["text"].get("found") == "Searched: milk")
        await run(
            "Enter in a form's field submits it", "/form", lambda p: item_of(p, "Full name"),
            lambda page, item: self.do("type", page, item, text="Dana Li", enter=True, settle=True),
            lambda st: "Joined as Dana Li" in str(st["text"].get("result")),
        )  # fmt: skip

        async def escape(page: dict[str, Any], item: dict[str, Any]) -> None:
            await self.do("click", page, item, settle=True)
            await asyncio.sleep(0.4)
            again = await self.read()
            await self.do("press", again, item_of(again, "No, wait"), key="Escape")

        await run("Escape closes an overlay", "/modal", lambda p: item_of(p, "Open the overlay"), escape, lambda st: st["text"].get("state") == "overlay: escape")

    async def private(self) -> None:
        """Every private field on the extension's two test pages: is it recognised (listed
        as theirs to fill), and is typing into it refused? A field that isn't recognised is
        recorded as a miss, with what then happened when it was typed into."""
        s = self.site
        fields = [
            # (page, words in its label, the field's name on the page, why it is private, what it is)
            ("/form", "Password", "pw", "type=password", "password"),
            ("/form", "Card number", "ccnum", "its label, and autocomplete=cc-number", "card"),
            ("/form", "Social insurance number", "sin", "its label", "id"),
            ("/form", "Photo", "photo", "a file picker", "file"),
            ("/private", "Passwort", "field9", "labelled by other text, in German", "password"),
            ("/private", "Kartennummer", "k1", "its label, in German", "card"),
            ("/private", "Choose one", "pw2", "type=password, no label", "password"),
            # Private only by name=, which can't be seen here: caught as fields with no label
            # on the page, beside a password field.
            ("/private", "Login", "loginpassword", "only its name= says password", "password"),
            ("/private", "Number", "cardnum", "only its name= says card", "card"),
            ("/private", "Code", "otpcode", "only its name= says one-time code", "password"),
            ("/private", "3 digits", "verification_value", "only its name= says security code", "card"),
            ("/private", "Box 1", "b1", "one of four boxes for a card number, no label", "card"),
        ]
        for path, words, name, why, kind in fields:
            self.b.hand_back()
            await s.go(path)
            try:
                page = await self.read()
            except BrowserError as exc:
                self.row("private field", f"{words}: {why}", found=False, note=f"read {exc.code}")
                continue
            item = item_of(page, words)
            if item is None and kind == "file":
                item = next((i for i in page["items"] if i.get("sensitive") == "file"), None)
            if item is None:
                self.row("private field", f"{words}: {why}", found=False, note="not in the outline")
                continue
            flagged = item.get("sensitive") == kind or (bool(item.get("sensitive")) and kind != "file")
            note = "" if flagged else f"listed as an ordinary {item['kind']}"
            refused = False
            try:
                await self.do("click" if kind == "file" else "type", page, item, **({} if kind == "file" else {"text": "hunter2"}))
            except BrowserError as exc:
                refused = exc.code == "sensitive"
                note = note or ("" if refused else f"{exc.code}: {exc}")
            await asyncio.sleep(0.4)
            value = s.state().get("fields", {}).get(name)
            untouched = value in ("", 0, None)
            if not refused:
                note += "; it was typed into" if not untouched else "; not refused, but nothing went in"
            self.row("private field", f"{words}: {why}", found=flagged, acted=refused, confirmed=refused and untouched, note=note.strip("; "))
        # "Show password" swaps the password field for a text field with the same hint.
        self.b.hand_back()
        await s.go("/private")
        try:
            page = await self.read()
            await self.do("click", page, item_of(page, "Show", "button"), settle=True)
            await asyncio.sleep(0.4)
            again = await self.read()
            item = item_of(again, "Choose one")
            flagged = bool(item and item.get("sensitive") == "password")
            refused = False
            try:
                await self.do("type", again, item, text="hunter2")
            except BrowserError as exc:
                refused = exc.code == "sensitive"
            await asyncio.sleep(0.3)
            untouched = s.state()["fields"].get("pw2") == ""
            self.row("private field", "after Show password it is still the password", found=flagged, acted=refused, confirmed=refused and untouched)
        except (BrowserError, TypeError) as exc:
            self.row("private field", "after Show password it is still the password", found=False, note=str(exc)[:100])
        # Ordinary, labelled fields next to them must still work. (The second would turn into
        # a password field if the cursor were put in it; the value is set without doing that.)
        await self.case(
            "private field", "Nickname, a labelled field beside password fields, is typed into", "/private", lambda p: item_of(p, "Nickname"),
            lambda page, item: self.do("type", page, item, text="Dee"), lambda st: st["fields"].get("sneak") == "Dee",
        )  # fmt: skip
        await self.case(
            "private field", "Business name is NOT private (has 'ssn' inside it)", "/private", lambda p: item_of(p, "Business name"),
            lambda page, item: self.do("type", page, item, text="Corner Bakery"), lambda st: st["fields"].get("businessname") == "Corner Bakery",
        )  # fmt: skip

    async def takes_keyboard(self, legacy_first: bool) -> None:
        """Does acting bring the browser window to the front? The window is allowed to be
        activated for these few actions only, starts each one behind, and "confirmed" means
        it stayed behind. Run with each of the two orders in which the ways to act can be
        tried (UI Automation's patterns first, or the older default action first): the
        order screenlink uses for an engine is the one that takes the keyboard least.
        Windows itself only lets a window come to the front when the person at the PC hasn't
        just typed or clicked, so the same action can come out either way."""
        s = self.site
        group = f"takes the keyboard? ({'older default action first' if legacy_first else 'UI Automation patterns first'})"
        saved = screenlink.WAYS[self.b.engine]
        if not legacy_first:
            screenlink.WAYS[self.b.engine] = {**saved, **PATTERNS_FIRST}
        todo = [
            ("reading the page", "/form", None, None),
            ("pressing a button", "/list", "Add Milk", lambda p, i: self.do("click", p, i)),
            ("typing in a field", "/form", "Full name", lambda p, i: self.do("type", p, i, text="Dana Li")),
            ("ticking a box", "/form", "newsletter", lambda p, i: self.do("click", p, i)),
            ("picking from a dropdown", "/form", "Country", lambda p, i: self.do("select", p, i, option="Canada")),
            ("bringing an element into view", "/long", "Bottom button", lambda p, i: self.do("scroll", p, i)),
            ("following a link", "/list", "Go to the sign-up form", lambda p, i: self.do("click", p, i, settle=True)),
        ]
        try:
            for name, path, words, act in todo:
                await s.go(path)
                behind = self.b.hand_back(force=True)
                await asyncio.sleep(0.3)
                note = "" if behind else "the window couldn't be put behind first"
                try:
                    page = await self.read()
                    if act is not None:
                        await act(page, item_of(page, words))
                except (BrowserError, TypeError) as exc:
                    note = str(exc)[:80]
                await asyncio.sleep(0.5)
                took = foreground() == self.b.hwnd
                self.row(group, name, found=behind, acted=not note, confirmed=behind and not took, focus=took and behind, note=note)
        finally:
            screenlink.WAYS[self.b.engine] = saved
            self.b.hand_back(force=True)

    async def conditions(self) -> None:
        """The same three things (read a page, press a button, fill a field) with the
        window showing, covered by other windows, and minimised."""
        s = self.site
        self.steady = False
        cover = Cover()

        def covered() -> None:
            self.b.place(False)
            cover.over(self.b.hwnd)

        for label, arrange in (
            ("showing, not in front", lambda: self.b.place(True)),
            ("covered by another window", covered),
            ("minimised", lambda: user32.ShowWindow(self.b.hwnd, 7)),  # SW_SHOWMINNOACTIVE
        ):
            self.b.hand_back()
            await s.go("/start")
            arrange()
            await asyncio.sleep(2.5)
            hidden = bool(s.state().get("hidden"))
            says = f"the page says it is {'hidden' if hidden else 'visible'}"
            loaded = await s.go("/list", timeout=25)
            group = f"window {label}"
            try:
                page = await self.read()
                self.row(group, "read a page loaded in that state", found=True, acted=True, confirmed=bool(page["items"]) and "Groceries" in page["text"], note=says)
            except BrowserError as exc:
                self.row(group, "read a page loaded in that state", found=loaded, acted=False, confirmed=False, note=f"{says}; {exc.code}: {str(exc)[:70]}")
                page = None
            if page is not None:
                await self.case(group, "press a button", None, lambda p: item_of(p, "Add Milk"), lambda p, i: self.do("click", p, i), lambda st: "Milk" in st["text"].get("cart", ""), page=page, wait=5)
                await self.case(group, "type in a field", None, lambda p: item_of(p, "Search groceries"), lambda p, i: self.do("type", p, i, text="eggs"), lambda st: st["fields"].get("q") == "eggs", page=page, wait=5)
            cover.away()
            self.b.restore()
            await asyncio.sleep(1.0)
        # Read while showing, then act once the window is covered or minimised.
        for label, arrange in (
            ("read while showing, then covered", covered),
            ("read while showing, then minimised", lambda: user32.ShowWindow(self.b.hwnd, 7)),
        ):
            self.b.hand_back()
            self.b.place(True)
            await s.go("/list")
            try:
                page = await self.read()
            except BrowserError as exc:
                self.row(f"window {label}", "press a button", found=False, note=f"{exc.code}")
                continue
            arrange()
            await asyncio.sleep(2.5)
            says = f"the page says it is {'hidden' if s.state().get('hidden') else 'visible'}"
            self.row(f"window {label}", "(what the page says of itself)", note=says)
            await self.case(f"window {label}", "press a button", None, lambda p: item_of(p, "Add Milk"), lambda p, i: self.do("click", p, i), lambda st: "Milk" in st["text"].get("cart", ""), page=page, wait=5)
            await self.case(f"window {label}", "type in a field", None, lambda p: item_of(p, "Search groceries"), lambda p, i: self.do("type", p, i, text="eggs"), lambda st: st["fields"].get("q") == "eggs", page=page, wait=5)
            cover.away()
            self.b.restore()
            await asyncio.sleep(1.0)
        self.steady = True

    async def new_tabs(self) -> None:
        """Things that open another tab. Run last: afterwards the window has more tabs."""
        s = self.site
        if not (self.wanted("new tab") or self.wanted("open an address")):
            return
        self.b.hand_back()
        await s.go("/links")
        before = set(s.tabs)
        await self.case(
            "new tab", "a link that opens a new tab: Pyxi carries on there", None, lambda p: item_of(p, "Open the cake form in a new tab"),
            lambda page, item: self.do("click", page, item, settle=True),
            lambda st: st["page"] == "plain" and st["tab"] not in before, wait=10,
        )  # fmt: skip
        try:
            page = await self.read()
            self.row("new tab", "and the tab now showing is the one that is read", found=True, acted=True, confirmed=page["tab"]["url"].endswith("/plain"), note=page["tab"]["url"])
        except BrowserError as exc:
            self.row("new tab", "and the tab now showing is the one that is read", found=False, note=f"{exc.code}: {exc} {self.seen()}")
        self.b.hand_back()
        before = set(s.tabs)
        started = time.monotonic()
        try:
            url = s.url("/dropdown")
            out = await self.link.call("open", {"tab": self.b.hwnd, "url": url, "expect_host": host_of(url)}, browser=self.hint, timeout=40)
            ok = await until(lambda: s.state()["page"] == "dropdown" and s.state()["tab"] not in before, 10)
            self.row(
                "open an address", "handed to the browser's own program, opens as a new tab", found=True, acted=True, confirmed=ok and out["id"] == self.b.hwnd,
                focus=foreground() == self.b.hwnd, ms=(time.monotonic() - started) * 1000,
            )  # fmt: skip
        except BrowserError as exc:
            self.row("open an address", "handed to the browser's own program, opens as a new tab", found=True, acted=False, confirmed=False, note=f"{exc.code}: {exc}")
        self.b.hand_back()
        before = set(s.tabs)
        try:
            url = s.url("/hop")
            out = await self.link.call("open", {"tab": self.b.hwnd, "url": url, "expect_host": host_of(url)}, browser=self.hint, timeout=40)
            ok = await until(lambda: s.state()["page"] == "plain" and s.state()["tab"] not in before, 10)
            real = host_of(out["url"]) == f"localhost:{s.port}"
            self.row(
                "open an address", "one that sends the browser on to another host: the real page is reported", found=True, acted=True,
                confirmed=ok and real, note=out["url"],
            )  # fmt: skip
        except BrowserError as exc:
            self.row("open an address", "one that sends the browser on to another host: the real page is reported", found=True, acted=False, confirmed=False, note=f"{exc.code}: {exc}")
        self.b.hand_back()


async def private_window(name: str, tmp: Path, site: Site, driver: OwnWindows, link: screenlink.ScreenLink) -> None:
    """The same browser, started in a private window (Incognito, InPrivate, Private
    Browsing): Pyxi must not pick it, read it or open anything from it."""
    extra = {"firefox": ("-private-window",), "edge": ("--inprivate",)}.get(name, ("--incognito",))
    b = Browser(name, tmp, extra)
    b.site = site
    driver.browsers.append(b)
    site.tabs.clear()
    site.active = ""
    row = Row(name, "private window", "not picked, not read, nothing opened from it")
    try:
        b.start(site.url("/form"))
        loaded = await until(lambda: site.state().get("page") == "form", 30)
        b.show()
        hint = f"w{b.hwnd}"
        picked = link.pick()
        refusals = []
        for call, args in (("read", {"tab": b.hwnd}), ("open", {"tab": b.hwnd, "url": site.url("/plain"), "expect_host": host_of(site.url("/plain"))})):
            try:
                await link.call(call, args, browser=hint, timeout=30)
                refusals.append(f"{call} was not refused")
            except BrowserError as exc:
                refusals.append("" if "private window" in str(exc) else f"{call}: {exc.code}: {exc}")
        await asyncio.sleep(0.5)
        still = site.state().get("page") == "form" and len(site.tabs) == 1
        row.found, row.acted = loaded, picked == ""
        row.confirmed = loaded and picked == "" and not any(refusals) and still
        row.note = "; ".join(r for r in refusals if r) + ("" if picked == "" else f" it was picked ({picked})")
    except Exception as exc:
        row.note = str(exc)[:120]
    finally:
        b.stop()
        driver.browsers.remove(b)
    ROWS.append(row)
    print(f"  {'ok  ' if row.confirmed else 'FAIL'} {name:8s} private window         {row.case}  {row.note}", flush=True)


def _yn(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "NO")


# ---------------------------------------------------------------------- public pages, read only
async def public_pass(check: Check) -> None:
    """A few public pages that need no sign-in: reading, typing into a search box and
    following links. Nothing is submitted beyond a site's own search, no banner is accepted.
    Without this script's reporter in the page, "confirmed" here is what the browser itself
    says the address and the title are afterwards."""
    link, b = check.link, check.b

    async def opened(url: str) -> dict[str, Any] | None:
        b.hand_back()
        try:
            await link.call("open", {"tab": b.hwnd, "url": url, "expect_host": host_of(url)}, browser=check.hint, timeout=60)
            await asyncio.sleep(1.5)
            started = time.monotonic()
            page = await link.call("read", {"tab": b.hwnd, "max_chars": 12000, "max_items": 200}, browser=check.hint, timeout=60)
            page["_ms"] = (time.monotonic() - started) * 1000
            return page
        except BrowserError as exc:
            check.row("public page", f"open and read {host_of(url)}", found=False, note=f"{exc.code}: {exc}")
            return None
        finally:
            b.hand_back()

    async def now(test: Any, timeout: float = 15.0) -> dict[str, Any] | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            await asyncio.sleep(0.6)
            try:
                tab = await link.call("peek", {"tab": b.hwnd}, browser=check.hint)
            except BrowserError:
                continue
            if test(tab):
                return tab
        return None

    # Wikipedia: read the front page, search, follow a link, go back.
    page = await opened("https://en.wikipedia.org/wiki/Main_Page")
    if page is not None:
        check.row(
            "public page", "Wikipedia: read the main page", found=True, acted=True, confirmed="Wikipedia" in page["text"] and len(page["items"]) > 20,
            ms=page["_ms"], note=f"{len(page['text'])} characters, {len(page['items'])} elements{', more' if page['more_items'] else ''}",
        )  # fmt: skip
        def search_box(p: dict[str, Any]) -> dict[str, Any] | None:
            kinds = ("search box", "text field", "dropdown box")
            return next((i for i in p["items"] if i["kind"] in kinds and "search" in i["label"].lower()), None)

        box = search_box(page)
        if box is None:
            # In a narrow window the box is behind a "Search" button.
            opener = next((i for i in page["items"] if i["label"].strip().lower() == "search" and i["kind"] in ("button", "link")), None)
            if opener is not None:
                try:
                    await check.do("click", page, opener, settle=True)
                    await asyncio.sleep(1.0)
                    page = await check.read()
                    box = search_box(page)
                except BrowserError:
                    box = None
        if box is None:
            check.row("public page", "Wikipedia: find the search box", found=False, note=str([i["label"] for i in page["items"] if "earch" in i["label"]])[:200])
        else:
            try:
                await check.do("type", page, box, text="Ada Lovelace")
                again = await check.read()
                button = next((i for i in again["items"] if i["kind"] == "button" and i["label"].strip().lower() == "search"), None)
                how = "its Search button"
                if button is not None:
                    await check.do("click", again, button, settle=True)
                else:
                    how = "Enter"
                    await check.do("press", again, box, key="Enter", settle=True)
                tab = await now(lambda t: "Ada_Lovelace" in t["url"] or "search=Ada" in t["url"])
                check.row("public page", f"Wikipedia: search for Ada Lovelace ({how})", found=True, acted=True, confirmed=tab is not None, focus=foreground() == b.hwnd, note=(tab or {}).get("url", "the address didn't change")[:90])
            except BrowserError as exc:
                check.row("public page", "Wikipedia: search for Ada Lovelace", found=True, acted=False, confirmed=False, note=f"{exc.code}: {exc}")
            b.hand_back()
            try:
                article = await link.call("read", {"tab": b.hwnd, "max_chars": 8000, "max_items": 200}, browser=check.hint, timeout=60)
                target = next((i for i in article["items"] if i["kind"] == "link" and i["label"] == "Charles Babbage"), None)
                if target is None:
                    check.row("public page", "Wikipedia: follow a link in the article", found=False, note="no 'Charles Babbage' link among the first 200 elements")
                else:
                    await check.do("click", article, target, settle=True)
                    tab = await now(lambda t: "Charles_Babbage" in t["url"])
                    check.row("public page", "Wikipedia: follow a link in the article", found=True, acted=True, confirmed=tab is not None, focus=foreground() == b.hwnd)
                    b.hand_back()
                    here = await link.call("read", {"tab": b.hwnd, "max_chars": 500, "max_items": 5}, browser=check.hint, timeout=60)
                    await link.call("back", {"tab": b.hwnd, "page": here["page"], "host": host_of(here["tab"]["url"])}, browser=check.hint, timeout=30)
                    tab = await now(lambda t: "Ada_Lovelace" in t["url"])
                    check.row("public page", "Wikipedia: go back a page", found=True, acted=True, confirmed=tab is not None)
            except BrowserError as exc:
                check.row("public page", "Wikipedia: follow a link, go back", found=True, acted=False, confirmed=False, note=f"{exc.code}: {exc}")
    # A documentation site: read, use its navigation.
    page = await opened("https://docs.python.org/3/")
    if page is not None:
        check.row(
            "public page", "Python docs: read the front page", found=True, acted=True, confirmed="Python" in page["text"] and len(page["items"]) > 10,
            ms=page["_ms"], note=f"{len(page['text'])} characters, {len(page['items'])} elements",
        )  # fmt: skip
        target = next((i for i in page["items"] if i["kind"] == "link" and i["label"].lower().startswith("library reference")), None)
        if target is None:
            check.row("public page", "Python docs: follow a navigation link", found=False, note="no 'Library reference' link in the outline")
        else:
            try:
                await check.do("click", page, target, settle=True)
                tab = await now(lambda t: "/library/" in t["url"])
                check.row("public page", "Python docs: follow a navigation link", found=True, acted=True, confirmed=tab is not None, focus=foreground() == b.hwnd)
            except BrowserError as exc:
                check.row("public page", "Python docs: follow a navigation link", found=True, acted=False, confirmed=False, note=f"{exc.code}: {exc}")
    b.hand_back()
    page = await opened("https://developer.mozilla.org/en-US/docs/Web/HTML")
    if page is not None:
        check.row(
            "public page", "MDN: read a reference page", found=True, acted=True, confirmed="HTML" in page["text"] and len(page["items"]) > 10,
            ms=page["_ms"], note=f"{len(page['text'])} characters, {len(page['items'])} elements",
        )  # fmt: skip
    b.hand_back()


# ---------------------------------------------------------------------- the real model
async def real_model(check: Check, opts: argparse.Namespace, tmp: Path) -> None:
    """use_tab, through the brain's own handler and tabwork's operator, with the real model,
    on the local pages. At most ``opts.model_calls`` model calls in all."""
    from otto.config import load_dotenv

    from conftest import FakeScreen
    from pyxi.companion.brain import Brain
    from pyxi.config import PyxiSettings
    from pyxi.demo import DemoLLM
    from pyxi.hub import Hub
    from pyxi.llm import make_llm

    if opts.env_dir:  # where the .env with the model key is (it is loaded, never shown)
        os.chdir(opts.env_dir)
    load_dotenv()
    real = PyxiSettings()
    settings = PyxiSettings(
        _env_file=None, data_dir=tmp / ".pyxi", voice={"tts": "client"},
        screen={"ambient_memory": False, "tab_fallback": True},
    )  # fmt: skip
    brain = Brain(settings, Hub(), llm=DemoLLM(), screen=FakeScreen())
    brain._speak = lambda text, **kw: None  # type: ignore[method-assign]
    brain._screen_tab = check.link  # the link that can only see this check's own windows
    cards = e2e.Cards(brain.hub)  # answers Allow, and keeps every card
    llm = make_llm(real, model=real.model_hands) if real.model_hands else make_llm(real)
    class Counted:
        async def complete(self, **kw: Any) -> Any:
            if MODEL_CALLS[0] >= opts.model_calls:
                raise RuntimeError("the check's model budget is used up")
            MODEL_CALLS[0] += 1
            return await llm.complete(**kw)

    brain._tab_llm = Counted()
    s = check.site
    tasks = [
        ("/list", "Add Milk and Eggs to the cart on this page, then stop.", lambda st: "Milk" in st["text"].get("cart", "") and "Eggs" in st["text"].get("cart", "")),
        (
            "/form",
            "In the form on this page put Dana Li in Full name and dana@example.com in Email, pick Canada as the country and tick the newsletter box. Do not submit it.",
            lambda st: st["fields"].get("fullname") == "Dana Li" and st["fields"].get("email") == "dana@example.com"
            and st["fields"].get("country") == "Canada" and st["fields"].get("news") is True and not st["text"].get("result"),
        ),
        ("/dropdown", "On this page choose Bangladesh as the country.", lambda st: st["text"].get("picked") == "Picked: Bangladesh"),
    ]  # fmt: skip
    # The budget is for all the browsers: the first gets the tasks named by --model-tasks
    # (by number), the others the form only.
    first = check.b.name == opts.browsers.split(",")[0].strip()
    tasks = [tasks[int(n) - 1] for n in opts.model_tasks.split(",")] if first else tasks[1:2]
    for path, task, done in tasks:
        if MODEL_CALLS[0] >= opts.model_calls:
            break
        check.b.hand_back()
        await s.go(path)
        cards.clear()
        before = MODEL_CALLS[0]
        try:
            job = await e2e.run_task(brain, task)
            ok = bool(done(s.state()))
            asked = [c["action"] for c in cards.cards()]
            check.row(
                "real model", task[:44], found=True, acted=job.status == "done", confirmed=ok, focus=foreground() == check.b.hwnd,
                note=f"{MODEL_CALLS[0] - before} model calls, ${job.cost_usd:.4f}; said: {job.summary[:60]!r}; cards: {len(asked)}",
            )  # fmt: skip
        except Exception as exc:
            check.row("real model", task[:44], found=True, acted=False, confirmed=False, note=str(exc)[:120])
    check.link.read_gate = None  # the brain's card for reading was for the brain's own tasks
    print(f"  (real model: {MODEL_CALLS[0]} calls so far, over all browsers)")


# ---------------------------------------------------------------------- the table
def rate(rows: list[Row], what: str) -> str:
    values = [getattr(r, what) for r in rows if getattr(r, what) is not None]
    return f"{sum(1 for v in values if v)}/{len(values)}" if values else "-"


def table(rows: list[Row], browsers: list[Browser], opts: argparse.Namespace) -> str:
    names = [b.name for b in browsers]
    out = [
        "# Pyxi in the tab on screen (no extension): measured",
        "",
        f"Written by `scripts/screen_tab_check.py` on {time.strftime('%Y-%m-%d %H:%M')}. "
        f"{opts.repeat} round(s) per browser; every \"confirmed\" is the page's own report of its state.",
        "",
        "Browsers: " + ", ".join(f"{b.name} {b.version}" for b in browsers) + ".",
        "",
        "Each cell is found / acted / confirmed (times out of times tried). \"F\" after a cell: the",
        "browser window took the keyboard focus that many times.",
        "",
        "## By kind of action",
        "",
        "| Kind of action | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]

    def cell(group_rows: list[Row]) -> str:
        if not group_rows:
            return "not run"
        focus = sum(1 for r in group_rows if r.focus)
        return f"{rate(group_rows, 'found')} / {rate(group_rows, 'acted')} / {rate(group_rows, 'confirmed')}" + (f" F{focus}" if focus else "")

    groups: list[str] = []
    for r in rows:
        if r.group not in groups:
            groups.append(r.group)
    for group in groups:
        out.append(f"| {group} | " + " | ".join(cell([r for r in rows if r.group == group and r.browser == n]) for n in names) + " |")
    out += ["", "## Every case", "", "| Kind | Case | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    cases: list[tuple[str, str]] = []
    for r in rows:
        if (r.group, r.case) not in cases:
            cases.append((r.group, r.case))
    for group, case in cases:
        out.append(
            f"| {group} | {case} | "
            + " | ".join(cell([r for r in rows if r.group == group and r.case == case and r.browser == n]) for n in names)
            + " |"
        )
    notes: list[str] = []
    for r in rows:
        if r.note and (not r.confirmed or r.group.startswith(("window", "public", "real", "private"))):
            line = f"- {r.browser}, {r.group}, {r.case}: {r.note}"
            if line not in notes:
                notes.append(line)
    if notes:
        out += ["", "## Notes from the runs (failures, and what the page said)", "", *notes[:120]]
    if RESTORED:
        out += [
            "",
            f"The check found its window minimised {len(RESTORED)} time(s) in the main runs and put it back "
            f"({', '.join(f'{n}: {RESTORED.count(n)}' for n in dict.fromkeys(RESTORED))}): somebody at the PC, or the browser "
            "settling after the check itself had moved it. The runs that minimise the window on purpose are the \"window\" rows.",
        ]
    speed = [r for r in rows if r.group == "read" and r.ms]
    if speed:
        out += ["", "## How long a look takes", ""]
        for n in names:
            mine = sorted(r.ms for r in speed if r.browser == n)
            if mine:
                out.append(f"- {n}: reading a local page took {mine[0]:.0f} to {mine[-1]:.0f} ms (median {mine[len(mine) // 2]:.0f} ms).")
    return "\n".join(out) + "\n"


async def main(opts: argparse.Namespace) -> int:
    if opts.rows:  # write the table again from a finished run's rows, without running anything
        rows = [Row(**r) for r in json.loads(Path(opts.rows).read_text(encoding="utf-8"))]
        names = list(dict.fromkeys(r.browser for r in rows))
        text = table(rows, [Browser(n, Path(tempfile.gettempdir()) / "pyxi-screen-tab-table") for n in names], opts)
        Path(opts.out).write_text(text, encoding="utf-8")
        print(f"(written to {opts.out})")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="pyxi-screen-tab-"))
    site = Site()
    names = [n.strip() for n in opts.browsers.split(",") if n.strip()]
    browsers: list[Browser] = []
    for name in names:
        if name not in EXES or not EXES[name].exists():
            print(f"({name} isn't installed here: skipped)")
            continue
        browsers.append(Browser(name, tmp))
    driver = OwnWindows(browsers)
    link = screenlink.ScreenLink(driver)
    try:
        for b in browsers:
            print(f"\n== {b.name} {b.version} ==", flush=True)
            site.tabs.clear()
            site.frames.clear()
            site.active = ""
            b.site = site
            b.start(site.url("/start"))
            if not await until(lambda: site.state().get("page") == "start", 30):
                print("  the first page never reported: skipped")
                b.stop()
                continue
            showing = b.show()
            print(
                f"  (window {b.hwnd}; it took the keyboard when it started: {b.focus_at_start}; "
                f"given back: {foreground() != b.hwnd}; the page says it is showing: {showing})"
            )
            check = Check(b, site, link, opts)
            for n in range(opts.repeat):
                print(f"  -- round {n + 1}")
                await check.round()
            if not opts.quick:
                for _ in range(2):
                    await check.takes_keyboard(legacy_first=False)
                    await check.takes_keyboard(legacy_first=True)
                await check.conditions()
            if opts.front:
                await check.keys(in_front=True)
            if opts.real_model:
                await real_model(check, opts, tmp)
            await check.new_tabs()
            if opts.public:
                await public_pass(check)
            b.stop()
            if check.wanted("private window"):
                await private_window(b.name, tmp, site, driver, link)
    finally:
        for b in browsers:
            b.stop()
        site.server.shutdown()
    text = table(ROWS, browsers, opts)
    print("\n" + text)
    if opts.out:
        Path(opts.out).write_text(text, encoding="utf-8")
        print(f"(written to {opts.out})")
    (tmp / "rows.json").write_text(json.dumps([r.__dict__ for r in ROWS], indent=1), encoding="utf-8")
    print(f"(every row is in {tmp / 'rows.json'})")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--browsers", default="chrome,edge,firefox")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--quick", action="store_true", help="skip the covered and minimised window runs")
    parser.add_argument("--only", default="", help="only the cases whose kind or name has these words")
    parser.add_argument("--rows", default="", help="write the table again from a finished run's rows.json")
    parser.add_argument("--front", action="store_true", help="also press keys with the window in front (takes the keyboard briefly)")
    parser.add_argument("--public", action="store_true", help="also a read-only pass on a few public pages")
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--model-calls", type=int, default=20, help="most real-model calls in all")
    parser.add_argument("--model-tasks", default="1,2,3", help="which of the three tasks the first browser gets")
    parser.add_argument("--env-dir", default="", help="folder whose .env holds the model key, for --real-model")
    parser.add_argument("--out", default=str(ROOT / "docs" / "SCREEN-TAB-CHECK.md"))
    sys.exit(asyncio.run(main(parser.parse_args())))
