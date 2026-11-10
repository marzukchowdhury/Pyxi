# Pyxi in the tab on screen, with no browser extension

What this is, what was measured, and where it can and can't be relied on. The numbers are
in [SCREEN-TAB-CHECK.md](SCREEN-TAB-CHECK.md), written by `scripts/screen_tab_check.py`.

## What it is

When no browser extension is connected, `read_tab` and `use_tab` can work in the browser tab
that is showing, through Windows UI Automation (the accessibility layer screen readers use).
`src/pyxi/screenlink.py` answers the same requests `companion/tabwork.py` sends the
extension, so the operator is the same code: the Allow card per site, the card before
anything that sends, submits, buys, deletes or posts, the fence around page text, the page
id on every action, the activity log.

It never moves the mouse and never types on the keyboard. A button or link is pressed with
the element's own action, text is set as the field's value, a box is ticked, an option is
chosen, a page is scrolled. Where an element offers none of these, it says it can't be done
this way. Keys (Enter, Escape, the arrows) are a separate switch: a key is posted to the one
browser window that was read, so it cannot land in another window.

The extension is always used when one is connected. This is only the fallback.

Switch: `screen.tab_fallback` (off in this branch until the founder decides) and
`screen.tab_keys` (on), in `pyxi.yaml` or as `PYXI_SCREEN__TAB_FALLBACK=true`. There is no
switch in Settings yet: the extension session is changing that screen, and a toggle there
is a small follow-up once both branches are in.

## How it was measured

`scripts/screen_tab_check.py` starts each browser itself with a throwaway profile, opens
local test pages, and for every action records: found in the outline Pyxi reads, acted, and
confirmed by the page. Every test page reports its own state (field values, the events its
listeners heard, where it is scrolled) to the script, so "confirmed" is read back from the
page. Chrome 156, Firefox 159 and Edge 157 on Windows 11, three rounds each, on one PC.

Pages: the extension's own test pages, plus a plain form, a React-style controlled input,
a dropdown built from divs and an ARIA one, a single-page app that swaps content at the same
address, same-site and other-site iframes, a long page, a `<dialog>` and a div overlay,
open and closed shadow DOM, rich text boxes, and controls that only listen for mouse-down,
pointer-down or hover.

Then a read-only pass on Wikipedia, the Python docs and MDN (read, a site search, follow
links, back), and four small tasks with the real model through `use_tab`.

## Per browser

### Works every time (3 of 3 rounds, every case, in Chrome, Edge and Firefox)

- Reading: the page's text including what is past the screen, nothing hidden, card numbers
  masked, the right kind and words for each element, nothing from the browser's own toolbar.
  A look takes 0.2 to 0.7 seconds on the local pages and 0.2 to 1.0 seconds on the public ones.
- Buttons (plain, in a form, far off screen), links, a div that is only clickable.
- Text fields: input, email, number, search, textarea. The page hears `input` and `change`.
- A React-style controlled input that ignores a value set without a real event: 3 of 3.
- Checkboxes and radio buttons, with `change` heard.
- Native dropdowns, with `change` heard. Custom dropdowns (open, read again, pick).
- A single-page app, iframes (same site and another site), a `<dialog>`, a div overlay,
  open and closed shadow DOM.
- Scrolling (a page down, to the bottom, to the top, a named element into view), Back,
  a link that opens a new tab, opening an address.
- Every "tied to what was read" check: an element the page removed, a button whose words
  changed, and an action meant for the previous page were all refused, 9 of 9 per browser.
- Public pages: 7 of 7 per browser (read three sites, search Wikipedia, follow a link in
  the article, go back, follow a navigation link in the Python docs).

### Works sometimes, or only under a condition

- Keys in Chrome and Edge: only while that window is in front. In front: Chrome 12 of 12,
  Edge 4 of 6 (once Edge lost the front between the check and the key and the key was
  refused; once the check could not bring it to the front). Not in front: refused with a
  plain line, every time, and Pyxi does not take the keyboard to make it work.
- Keys in Firefox: 11 of 11, and they work with the window behind others, but putting the
  cursor in the element brings the Firefox window to the front.
- Native dropdowns take the keyboard. Chrome and Edge have to open the dropdown to reach its
  options; Firefox is set with the arrow keys (choosing the option through accessibility
  changes it without telling the page). Counted in the main run: Chrome 1 of 6, Firefox 1 of
  6, Edge 2 of 6.
- A button that only listens for pointer-down: Chrome and Edge 3 of 3, Firefox 0 of 3.
- Real model, 20 calls in all: Chrome did all three tasks (two items to a cart, fill a form
  without submitting, pick from a custom dropdown) in 3, 2 and 3 calls; Firefox did the form
  in 5 calls; Edge had not finished the form when the budget ran out after 6 calls, so
  Edge is not shown to work with the real model yet.
- A window covered by another window: it depends on whether the browser decides its page
  is hidden. When it does (seen in each browser in one run or another; in the last run
  only Edge did), a page loaded in that state gives nothing to read and Pyxi says the
  window has to be showing. A page read before being covered could still be acted on, 2 of
  2 per browser, but in those tries the page still called itself visible.

### Does not work

- Rich text boxes in Chrome and Edge (a mail body, a chat box, a document): 0 of 6. The
  browser accepts the text and puts it in without telling the page. Pyxi now refuses with a
  plain line. One wrinkle: an empty box with the textbox role looks exactly like a text
  area, so the first try puts the words in, then sees what the box is and reports that
  the page was not told. In Firefox a box with the textbox role works (3 of 3); a bare
  contenteditable div is not seen as a box at all.
- A menu that only appears on hover: 0 of 3 in every browser. There is no mouse to hover.
- A page loaded while the window is minimised: 0 of 1 in every browser. Minimised windows
  are refused by rule (Firefox 0 of 2 when minimised after reading; Chrome and Edge still
  acted, which should not be relied on).
- Other tabs and background windows: never. Only the tab that is showing. The tool
  descriptions, the prompt and the error lines all say so.
- Key combinations (Ctrl+Enter) and typing by keys: refused.

## Safety: where it is as safe as the extension, and where it is not

The same:

- Every rule in `tabwork.py` applies unchanged, and the layer repeats the refusals itself:
  password, card, security code, expiry and ID fields and file pickers are never typed into
  and their values never leave the browser; Pyxi's own windows are refused; a ref is only
  ever given to an element inside the web document, and before every action the element is
  checked to still be inside the document that was read.
- Each read mints a page id from the document's identity in the accessibility tree. A new
  document in the tab, another site, an element with other words: refused.
- A key is posted to the window by its handle after the page is checked once more.
- Stop and Stop everything end an action in flight. Esc stops the task when it is pressed
  while that browser window is in front.

Weaker than the extension:

1. Private fields that are private only by their `name=` or `autocomplete=` attribute are
   not recognised: those attributes are not in the accessibility tree. On the test pages
   7 of 12 private fields were recognised and refused (password type, labels in English and
   German, the label by another element, a file picker), 4 were missed (all of them marked
   only by `name=`), and one never became private because the cursor is never put in it.
   Card numbers themselves are still never typed (tabwork checks the digits), and the model
   is told never to type a password. This is the one real safety gap.
2. The tree does not say which button submits a form, so any button inside a form is
   treated as one that may, and asks first. More cards, never fewer.
3. There is no "Pyxi is working here" marker in the page. The job shows in the tray.
4. Acting can bring the browser window to the front. With the order now used (the older
   default action first) it stayed behind in 12 of 14 tries in Edge and in Firefox and 11
   of 14 in Chrome; what took the keyboard was the dropdown, and one checkbox in Chrome.
   With UI Automation's own patterns first it stayed behind in only 4 to 7 of 14. (Two
   runs: in the last one Chrome and Firefox could not be put behind to begin with, so
   their numbers are from the run before.) Windows refuses to let a window come forward
   while the person is typing or clicking, so this mostly happens when they are not.
5. Reading wakes the browser's accessibility support for that page, which costs it some
   speed for as long as the page is open. Pyxi's screen reading already does the same.

## Limits, in one list

Visible tab only. Not minimised, and not covered so far that the browser calls the page
hidden. Keys need the window in front in Chrome and Edge. Rich text boxes can't be typed
into in Chrome and Edge. Hover menus can't be opened. Pointer-down-only controls fail in
Firefox. Private fields known only by `name=` or `autocomplete=` are not recognised.
Iframes and shadow DOM (open and closed) work. Controlled inputs work. A dropdown takes the
keyboard for a moment. `goto` and `open_tab` both open a new tab. `switch_tab` is refused.

Not measured: Brave, Vivaldi, Opera, LibreWolf, Waterfox and Zen (let in because they are the
same two engines; no numbers). Signed-in sites such as mail or shops. Very large pages. A
second PC, another Windows version, another display language. Esc with a real key press
(covered by a unit test with a pretend keyboard only).

## What changed outside the new files

- `src/pyxi/companion/tabwork.py`, six places, each marked with a comment:
  the import (`from pyxi import safety, screenlink`); `system=SYSTEM + getattr(self.link,
  "note", "") + safety.RULE` in `_decide`; the two lines of `tools()`; the two lines of
  `prompt()`; one added line in `stop()`; the first two lines of `handle()`.
- `src/pyxi/browserlink.py`: untouched.
- `src/pyxi/config.py`: `tab_fallback` and `tab_keys` in `ScreenConfig`.
- `pyxi.example.yaml`: the same two settings.
- `tests/conftest.py`: the `settings` fixture names `tab_fallback: False`, so that turning
  the default on later cannot make an unrelated test reach for real browser windows.

New: `src/pyxi/screenlink.py`, `tests/test_screenlink.py`, `scripts/screen_tab_check.py`,
this file and the table.

## Recommendation

Use it by default when no extension is connected, after one more check that this run could
not do: ten minutes by the founder on his own PC, on two or three of his own signed-in
sites (a shop cart, a form, a search). Until then the default stays off in code.

Why default and not "only when asked": for someone with no extension the alternative Pyxi
has today is the real mouse and keyboard, which takes over the PC and has none of the
field-level refusals. On everything that was measured this is more dependable than that
and safer than that: the core actions were confirmed by the page every time in three
browsers, failures are loud rather than silent, and every action still goes through the
same cards.

What to say to people while it is on: it works in the tab you are looking at; for mail
bodies and chat boxes, other tabs, or a window that isn't in front, connect the extension.
The extension session's offer card is the right place for that line.

Two things to do before or soon after turning it on:

1. Close the private-field gap as far as it can be closed: when a page has a password field,
   treat unlabelled text fields in the same form as theirs to fill.
2. Give Edge the real-model run it did not get (about 10 calls).

## Running it again

    python scripts/screen_tab_check.py                    # three browsers, three rounds
    python scripts/screen_tab_check.py --front            # plus keys with the window in front
    python scripts/screen_tab_check.py --public           # plus the public pages, read only
    python scripts/screen_tab_check.py --only dropdown --quick --repeat 1

It only ever touches browser windows it started itself. The windows sit in the bottom right
corner; a browser can take the keyboard when it starts.
