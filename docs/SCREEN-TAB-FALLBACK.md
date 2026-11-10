# Pyxi in the tab on screen, with no browser extension

What this is, what was measured, and where it can and can't be relied on. The numbers are
in [SCREEN-TAB-CHECK.md](SCREEN-TAB-CHECK.md), written by `scripts/screen_tab_check.py`.

## What it is

When no browser extension is connected, `read_tab` and `use_tab` can work in the browser tab
that is showing, through Windows UI Automation (the accessibility layer screen readers use).
`src/pyxi/screenlink.py` answers the same requests `companion/tabwork.py` sends the
extension, so the operator is the same code: the card before anything that sends, submits,
buys, deletes or posts, the fence around page text, the page id on every action, the
activity log.

It never moves the mouse and never types on the keyboard. A button or link is pressed with
the element's own action, text is set as the field's value, a box is ticked, an option is
chosen, a page is scrolled. Where an element offers none of these, it says it can't be done
this way. Keys (Enter, Escape, the arrows) are a separate switch: a key is posted to the one
browser window that was read, so it cannot land in another window.

The extension is always used when one is connected, and for 30 seconds after its
connection was last alive nothing takes its place. This is only the fallback.

Switch: `screen.tab_fallback` (off in this branch) and `screen.tab_keys` (on), in
`pyxi.yaml` or as `PYXI_SCREEN__TAB_FALLBACK=true`. There is no switch in Settings yet.

## What the user is asked

- The first time a site would be read: "Let Pyxi read SITE in your browser?" Once per
  site, until Pyxi restarts. With the extension the user picks the sites in the browser;
  here this card is that choice.
- For a task (`use_tab`) the one card is "Let Pyxi read and work in SITE in your browser?":
  it covers reading the site and clicking and typing there for that task.
- A site and its exact "www." form are one place. An address tabwork already asked about
  before opening it is not asked about again when it is read.
- "Always allow on this site" on tabwork's card lasts until Pyxi restarts under the
  fallback, and the card says so. It is kept in memory only: it is not in Settings, and a
  site allowed under the extension is not thereby allowed here, nor the other way.
- Everything that sends, submits, buys, deletes or posts asks again, as with the extension.

## How it was measured

`scripts/screen_tab_check.py` starts each browser itself with a throwaway profile, opens
local test pages, and for every action records: found in the outline Pyxi reads, acted, and
confirmed by the page. Every test page reports its own state (field values, the events its
listeners heard, where it is scrolled) to the script, so "confirmed" is read back from the
page. Chrome 156, Firefox 159 and Edge 157 on Windows 11, three rounds each, on one PC.

The table is from the last full run, on the code as it is after two safety reviews. Two
kinds of row were run again afterwards (three rounds, same browsers) and replaced in it:
the custom dropdowns, after a one-line fix to how a div dropdown is named, and the private
windows, after a correction to what the check itself expected.

Pages: the extension's own test pages, plus a plain form, a React-style controlled input,
a dropdown built from divs and an ARIA one, a single-page app that swaps content at the same
address, same-site and other-site iframes, a long page, a `<dialog>` and a div overlay,
open and closed shadow DOM, rich text boxes, and controls that only listen for mouse-down,
pointer-down or hover. Also: each browser started in a private window (Chrome and Edge in
English and in Japanese), an address that sends the browser on to another host, and a
read-only pass on Wikipedia, the Python docs and MDN.

The real model ran four small tasks through `use_tab` in earlier runs, 23 calls in all
(about one cent). Those runs were before the second review changed the cards.

## Per browser

### Works every time (3 of 3 rounds, every case, in Chrome, Edge and Firefox)

- Reading: the text from where the page is scrolled to, including what is below the screen,
  nothing hidden, card numbers masked, the right kind and words for each element, nothing
  from the browser's own toolbar. A look took 0.1 to 0.6 seconds on the local pages and 0.2
  to 0.5 seconds on the public ones.
- Buttons (plain, in a form, far off screen), links, a div that is only clickable, a button
  that only hears mouse-down.
- Text fields: input, email, number, search, textarea. The page hears `input` and `change`.
- A React-style controlled input that ignores a value set without a real event.
- Checkboxes and radio buttons, with `change` heard.
- Native dropdowns, with `change` heard. Custom dropdowns (open, read again, pick).
- A single-page app, same-site iframes, a `<dialog>`, a div overlay, open and closed
  shadow DOM.
- Scrolling (a page down, to the bottom, to the top, a named element into view), Back,
  a link that opens a new tab, opening an address, and an address that sends the browser on
  to another host (the page that really loaded is what is reported).
- Keys with the window in front: 12 of 12 in each browser (Enter in a search box, Enter
  submitting a form, Escape closing an overlay).
- Every refusal: an element the page removed, a button whose words changed, an action meant
  for the previous page (9 of 9 per browser); another site's frame left out of the page and
  its text not read (3 of 3); every private field on the test pages (see below); a private
  window of each browser not read, nothing opened from it.
- Public pages: 7 of 7 per browser (read three sites, search Wikipedia, follow a link in
  the article, go back, follow a navigation link in the Python docs).

### Works sometimes, or only under a condition

- Keys in Chrome and Edge: only while that window is in front. Not in front they are
  refused with a plain line, and Pyxi does not take the keyboard to make it work (seen in
  earlier runs: 3 of 3 refused in Chrome, 5 of 5 in Edge; in the last run the windows were
  in front throughout).
- Keys in Firefox reach it behind other windows (seen in an earlier run), but putting the
  cursor in the element brings the Firefox window to the front.
- Native dropdowns take the keyboard. Chrome and Edge have to open the dropdown to reach
  its options; Firefox is set with the arrow keys, because choosing the option through
  accessibility changes it without telling the page.
- A button that only listens for pointer-down: Chrome and Edge 3 of 3, Firefox 0 of 3.
- A window covered by another window: it depends on whether the browser decides its page
  is hidden. In the last run Firefox did: the page could not be read (0 of 1) and a page
  read earlier could no longer be acted on (0 of 2). Chrome and Edge still called the page
  visible under the check's cover and worked; in an earlier run Edge called it hidden and
  failed the same way. When the page is hidden Pyxi says the window has to be showing.
- Very long pages: a look reads at most 6,000 elements, in about two seconds, starting
  where the page is scrolled to. On Wikipedia's "List of Latin phrases (full)" (418,000
  characters, about 30,000 elements) that is a fifth of the page per look; on "World War II"
  (176,000 characters) a little over half. The rest is reached by scrolling.
- Real model (earlier runs): Chrome did three tasks (two items to a cart, fill a form
  without submitting, pick from a custom dropdown) in 3, 2 and 3 calls; Firefox did the form
  in 5 calls; Edge did it in 3.

### Does not work

- Rich text boxes in Chrome and Edge (a mail body, a chat box, a document): 0 of 6. The
  browser accepts the text and puts it in without telling the page. Pyxi refuses with a
  plain line. One wrinkle: an empty box with the textbox role looks exactly like a text
  area, so the first try puts the words in, then sees what the box is and reports that
  the page was not told. In Firefox a box with the textbox role works (3 of 3); a bare
  contenteditable div is not seen as a box at all.
- A menu that only appears on hover: not reachable in any browser. There is no mouse.
- A minimised window: refused (0 of 1 reading, 0 of 2 acting, in every browser).
- Other tabs and background windows: never. Only the tab that is showing.
- Key combinations (Ctrl+Enter) and typing by keys: refused.
- By design: private windows; frames that belong to another site; frames whose address
  says nothing (about:blank, srcdoc, data:), even when they are the page's own.

## Safety

The same as with the extension:

- Every rule in `tabwork.py` applies, and the layer repeats the refusals itself. Private
  fields are never typed into and their values never leave the browser. Pyxi's own windows
  and browsers a program is driving (Pyxi's own booking browser) are refused. A ref is only
  ever given to an element inside the web document, and before every action the element is
  checked to still be inside the document that was read, with the words it had, those of
  an element with no name of its own included.
- Each read mints a page id from the document's identity in the accessibility tree. A new
  document in the tab, another site, an element with other words, a document that gives no
  identity: refused. A page that moves on while it is being read is read again.
- A key is posted to the window by its handle, only while the cursor is inside the page.
- Stop, Stop everything and a cancelled task end work in flight and work that was waiting.

Fails closed where it can't tell:

- A window it can't tell is private or not is refused until it can. What marks a private
  window does not depend on the display language or on anything a page controls: Chrome and
  Edge put a bracketed word after the browser's name in their root view (checked in English,
  Russian and Japanese); Firefox has a toolbar button whose class says so (checked in
  English; class names are not translated). Other browsers on the same engines are
  refused if those marks can't be seen.
- A frame is the page's own only when its site is known and equal to the page's.
- A read card that nobody answers is not a yes, and not a no.

Weaker than the extension:

1. The page's own `name=` and `autocomplete=` can't be seen. Private fields are recognised
   by password type, by the words of their label, id and hint, and by one more rule: in a
   form or page that has a password field, a text field whose name is not words visible on
   the page is left to the user. With that, all 12 private fields on the test pages were
   refused in every browser, and the labelled fields beside them were still typed into (45
   of 45 checks per browser). What is still missed: a field that is private only by
   `name=` or `autocomplete=`, has an innocent label you can see, and has no password field
   near it. Card numbers themselves are never typed (tabwork checks the digits).
   The price of the rule: on sign-in and sign-up forms, fields named only by a placeholder
   or in the page's code are handed to the user.
2. The tree does not say which button submits a form, so any button inside a form is
   treated as one that may, and asks first. More cards, never fewer.
3. There is no "Pyxi is working here" marker in the page. The job shows in the tray. Esc
   stops the task only while that browser window is in front.
4. Acting can bring the browser window to the front. With the order now used (the older
   default action first) it stayed behind in 12 of 14 tries (Chrome in the last run; Edge
   and Firefox the same in earlier ones); what took the keyboard was the dropdown. With UI
   Automation's own patterns first it stayed behind in only 4 to 7 of 14. Windows refuses to
   let a window come forward while the person is typing or clicking.
5. Reading wakes the browser's accessibility support for that page, which costs it some
   speed for as long as the page is open. Pyxi's screen reading already does the same.

Not measured: Brave, Vivaldi, Opera, LibreWolf, Waterfox and Zen. Signed-in sites such as
mail or shops. A second PC, another Windows version. Firefox in another display language.
Esc with a real key press (a unit test with a pretend keyboard only).

## What changed outside the new files

- `src/pyxi/companion/tabwork.py`, against 0.1.11 (febd670):
  - line 58: the import (`from pyxi import safety, screenlink`);
  - lines 583 to 587, `_same_site`: a site and its exact "www." form are one place
    (this also applies with the extension; any other change of host asks as before);
  - line 766, `_decide`: `system=SYSTEM + getattr(self.link, "note", "") + safety.RULE`;
  - lines 999 to 1001 and 1006 to 1007, `_send`: when the link says the text stayed and
    Enter could not be pressed, the record and the note to the model say that;
  - lines 1106 to 1107 (`tools`), 1114 to 1115 (`prompt`), 1128 (`stop`), 1165 to 1166
    (`handle`): the link comes from `screenlink.choose(brain)`;
  - line 1365, the card: how long "Always allow" lasts comes from the link.
- `src/pyxi/browserlink.py`: untouched.
- `src/pyxi/config.py`: `tab_fallback` and `tab_keys` in `ScreenConfig`.
- `pyxi.example.yaml`: the same two settings.
- `tests/conftest.py`: the `settings` fixture names `tab_fallback: False`, so that turning
  the default on later cannot make an unrelated test reach for real browser windows.

New: `src/pyxi/screenlink.py`, `tests/test_screenlink.py`, `scripts/screen_tab_check.py`,
this file and the table.

## Recommendation

Only when asked, for now: ship it switched off, with the setting, and turn it on by default
after two things.

1. The independent checks come back with nothing left. Two rounds of review found real
   faults that the measurements could not have shown (another site's frame treated as the
   page, reading without the user's yes, private windows read). They are fixed and each
   has a test that fails without the fix, but a layer that needed two rounds should not
   become the default on the strength of its own author's tests.
2. Ten minutes by the founder on his own PC, on two or three of his own signed-in sites (a
   shop cart, a form, a search). Nothing here was measured on a signed-in site.

Why it is still worth turning on after that: for someone with no extension the alternative
Pyxi has today is the real mouse and keyboard, which takes over the PC and has none of the
field-level refusals. On everything that was measured this is more dependable than that:
the core actions were confirmed by the page every time in three browsers, failures are loud
rather than silent, and every action still goes through the same cards.

What to say to people while it is on: it works in the tab you are looking at; for mail
bodies and chat boxes, other tabs, private windows, or a window that isn't in front,
connect the extension.

## Running it again

    python scripts/screen_tab_check.py                    # three browsers, three rounds
    python scripts/screen_tab_check.py --front            # plus keys with the window in front
    python scripts/screen_tab_check.py --public           # plus the public pages, read only
    python scripts/screen_tab_check.py --only "dropdown,private window" --quick --repeat 1

It only ever touches browser windows it started itself. The windows sit in the bottom right
corner; a browser can take the keyboard when it starts.
