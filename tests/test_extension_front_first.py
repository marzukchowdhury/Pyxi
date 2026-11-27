"""The add-on reads what is open in front of the page first (extension/content.js).

A read carries a limited number of items and characters, in the page's own order. A site's menu
usually opens at the very end of the page, so on a busy page (YouTube's home has several hundred
clickable things) the open menu fell off the end of every read: the click that opened it looked
as if it had done nothing, and Pyxi clicked the same button until it gave up
("the page stopped responding to what I tried").

This runs the real content script in a real browser, with a stand-in for the extension's
messaging, on a page built that way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

SOURCE = Path(__file__).resolve().parents[1] / "extension" / "content.js"
PAGE = """<!doctype html><html><body style="margin:0">
<header style="position:sticky;top:0;z-index:2020;background:#111"><button id="avatar" aria-label="Account menu">A</button></header>
<main id="feed"></main>
<script>
  const feed = document.getElementById('feed');
  for (let i = 0; i < 300; i++) {
    const a = document.createElement('a'); a.href = '/watch?v=' + i;
    a.textContent = 'Video number ' + i + ' with a long title that fills the read up quickly';
    a.style.cssText = 'display:inline-block;width:150px;height:22px;overflow:hidden;font-size:9px';
    feed.append(a);
  }
  const open = (id, how, html) => {
    const pop = document.createElement('div'); pop.id = id; pop.innerHTML = html;
    if (how === 'z') pop.style.cssText = 'position:fixed;right:10px;top:40px;z-index:2202;background:#222;color:#fff';
    else { pop.setAttribute('role', how); pop.style.cssText = 'position:absolute;left:10px;top:60px;background:#222;color:#fff'; }
    document.body.append(pop);
  };
  document.getElementById('avatar').addEventListener('click', () =>
    open('pop', 'z', '<div>Signed in as Sam Rivera</div><a href="#">Switch account</a><a href="#">Sign out</a>'));
  window.openDialog = () => open('dlg', 'dialog', '<p>Leave this page?</p><button>Stay here</button>');
</script></body></html>"""
STUB = """globalThis.__handlers = [];
globalThis.chrome = { runtime: { onMessage: { addListener: (f) => globalThis.__handlers.push(f) }, sendMessage: () => {}, id: 'x' } };"""
READ = (
    "() => new Promise((done) => globalThis.__handlers[0]("
    "{ to: 'pyxi-content', cmd: 'read', args: { max_chars: 3500, max_items: 90 } }, {}, done))"
)


@pytest.fixture(scope="module")
def page():
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # no browser on this machine
            pytest.skip(f"no Chromium to run the content script in: {type(exc).__name__}")
        tab = browser.new_page(viewport={"width": 1280, "height": 720})
        tab.set_content(PAGE)
        tab.evaluate(STUB)
        tab.evaluate(SOURCE.read_text(encoding="utf-8"))
        yield tab
        browser.close()


def read(tab) -> dict:
    answer = tab.evaluate(READ)
    assert answer["ok"], answer
    return answer["data"]


def test_a_menu_opened_at_the_end_of_a_busy_page_is_in_the_read(page):
    before = read(page)
    assert before["more_items"] and len(before["items"]) == 90  # the page is fuller than a read
    assert not any(i["label"] == "Switch account" for i in before["items"])
    page.click("#avatar")
    after = read(page)
    labels = [i["label"] for i in after["items"]]
    # The open menu leads the list and the text, so the click plainly did something...
    assert labels[:2] == ["Switch account", "Sign out"]
    assert after["text"].startswith("Signed in as Sam Rivera")
    assert json.dumps(before["items"]) != json.dumps(after["items"])
    # ...its words are read once, not again where the menu sits in the page...
    assert after["text"].count("Switch account") == 1
    # ...and the page itself still follows in its own order.
    videos = [label for label in labels if label.startswith("Video number")]
    assert videos[:3] == [f"Video number {n} with a long title that fills the read up quickly" for n in range(3)]


def test_a_dialog_is_in_front_by_what_it_is_not_only_by_stacking(page):
    page.evaluate("window.openDialog()")
    got = read(page)
    labels = [i["label"] for i in got["items"]]
    assert "Stay here" in labels[:4]
    assert "Leave this page?" in got["text"][:200]
