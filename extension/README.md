# Pyxi in your browser

A browser extension that lets Pyxi, running on the same PC, read and work inside the tabs you
choose: your own tabs, where you're already signed in, without taking your mouse. One codebase
for Chrome, Edge and Firefox.

Without it, Pyxi has two ways into a website: Scout, who browses in a separate browser where
you aren't signed in, and the mouse and keyboard, which you can't use while Pyxi does. With
it, "fill this in", "add this to my cart" or "what does this page say about refunds?" happen
in the tab you're looking at, and you keep working.

Not published anywhere. You load it yourself, as below.

## Load it

**Chrome**

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Press **Load unpacked** and pick this `extension` folder.
4. Pin it: the puzzle icon in the toolbar, then the pin next to "Pyxi in your browser".

**Edge**

1. Open `edge://extensions`.
2. Turn on **Developer mode** (left side).
3. Press **Load unpacked** and pick this `extension` folder.
4. Show it in the toolbar: the puzzle icon, then the eye next to "Pyxi in your browser".

**Firefox** (128 or newer)

Firefox needs its own manifest, made from the same code:

```bash
python extension/build.py firefox
```

1. Open `about:debugging#/runtime/this-firefox`.
2. Press **Load Temporary Add-on** and pick `extension/dist/firefox/manifest.json`.

A temporary add-on is gone when Firefox closes, and normal Firefox only keeps add-ons that
Mozilla has signed. Keeping it installed therefore needs one of: signing it through
addons.mozilla.org (it can stay unlisted, but it is still a submission to Mozilla, so that is
the founder's call), or Firefox Developer Edition or Nightly with
`xpinstall.signatures.required` set to `false`. Mozilla's signing also asks new add-ons to
declare what data leaves the browser; this one sends page text to Pyxi on the same PC, which
would be declared there.

## Pair it with Pyxi

The extension only talks to a Pyxi it has been paired with.

1. In Pyxi, open the panel, go to **Settings**, find **Connect your browser** and press
   **Connect a browser**. Pyxi shows a code like `WHYR-TMJU`. It works once and lasts five
   minutes.
2. In the browser, click the Pyxi icon, type the code and press **Connect**.

The popup says **Connected**, and Settings lists the browser. To unpair: **Disconnect** in
Pyxi's Settings, or **Disconnect this browser from Pyxi** in the popup. Either cuts it off at
once.

The code you type never leaves the browser, and no password or token is ever sent to Pyxi.
The two sides do a key exchange and each proves, with a check made from the code, that it saw
the same exchange; Pyxi goes first. On every later connection Pyxi again has to prove it holds
the key from that pairing before the extension tells it anything or does anything it asks.
So a program that listens on Pyxi's port while Pyxi isn't running learns an id and a random
number, and can't drive your tabs or unpair the browser.

Without the desktop app's Settings (a brain started by hand), ask the brain for a code:

```bash
curl -X POST -H "x-pyxi-key: <the key in .pyxi/brain.key>" http://127.0.0.1:8766/api/browser/pair
```

A brain on another port: open **Advanced** in the popup and set the port before connecting.

## Let it into a site

When installed, the extension can't touch any site. You decide where:

- **Click the Pyxi icon in a tab.** That lets Pyxi work in that tab until you leave the site.
  When Pyxi needs a tab it can't enter, it tells you to do this and waits about half a minute.
- **Always allow on this site**, in the popup, keeps the site allowed. The browser shows its
  own "Allow?" prompt. **Remove** takes it away again, at once.
- **Every site**, in the popup, if you'd rather not allow sites one by one.
- **See the names of all my open tabs**, in the popup, lets Pyxi find and switch to any tab
  by name. Without it, Pyxi only sees the names of tabs on sites you've allowed.

Private (incognito) windows are never available to it.

## What Pyxi does with it

Two tools appear in Pyxi while a browser is connected, and nothing changes when none is:

- **read_tab** reads the tab you're on (or one you name): the page's visible text and what
  can be clicked or typed into. Reading changes nothing and needs no card.
- **use_tab** does a task in the tab: click, type, pick from a dropdown, press a key, scroll,
  go to an address, open or switch tab. A small "Pyxi is working in this tab" marker and an
  amber frame show while it works.

## Safety

- **You allow each site, twice over.** The browser must let the extension into the site (the
  section above), and Pyxi asks on its own card before it acts on a site ("Let Pyxi click and
  type on shop.example.com in your browser?"). **Allow** covers that one task. **Always allow
  on this site** keeps the site for 30 days; Settings lists those sites with a Remove button.
- **A task stays on the site it started on.** Going on to another site (a link that leads
  there, another tab, an address) asks first, every time, with the address on the card and
  before that site is read. So does anything right after Pyxi read an email, a page or a
  document in the same request.
- **An address Pyxi wrote itself asks first**, even on a site you allow, unless you said that
  address: opening an address can do something by itself, and can carry what Pyxi read to
  that site. The extension is told which site Pyxi checked and won't open an address that
  the browser would read as leading somewhere else.
- **After Pyxi has read something, it asks before reading another of your tabs or fetching an
  address it made up.** Text in a page can say "now read the inbox tab and open this link";
  the card is what stands in the way.
- **Sending, submitting, buying, deleting and posting ask first**, on a card that says
  exactly what will happen: the button's own words, the site, and what Pyxi typed. Pressing
  Enter anywhere but a search box asks too (a field that says it is a search box itself, not
  one called "Email address"), and Enter or Space on a button counts as clicking it.
- **Every action is for the page Pyxi read.** Each one names the page, the site and the
  element's own words, and the script in the page refuses if any of them has changed (the
  page was replaced, you went back, a "Continue" button became "Place order"). A click, a
  key or a new address ends a batch, so nothing decided on one page happens on the next.
- **Passwords, card numbers, security codes and ID numbers are yours.** Pyxi never types into
  those fields, never reads what's in them, and never types a card number anywhere. The
  script in the page refuses by itself, whatever Pyxi asks, and Pyxi refuses again on its
  side. Card numbers that appear in a page's text are hidden before the text leaves the page,
  and a card number typed in pieces (four boxes of four) is stopped at the last piece.
- **No cookies, no storage, no code from outside.** The extension has no permission for
  cookies, never touches a page's storage, and can only do the fixed list of things written
  in `background.js` and `content.js`. It can't run a script that Pyxi or a page sends it.
- **It only talks to this PC, and only to the Pyxi it was paired with.** Its content security
  policy allows one kind of connection: a WebSocket to `127.0.0.1`. It can't reach any
  server. Whatever answers there must prove it holds the pairing's key, and after that every
  message both ways is sealed with a key for that one connection.
- **Page text is data.** What a page says reaches Pyxi's model marked as untrusted page
  content, never as instructions.
- **Esc in the tab stops Pyxi**, and so do the marker's Stop button, the Stop on the job in
  Pyxi's tray, and Pyxi's "Stop everything" hotkey. Esc works with the cursor in a frame of
  the same site, and closes a card that was waiting for an answer.
- **Private pages stay private.** Tabs that match Pyxi's private apps list (password
  managers, banking, anything you added in Settings) are never read or touched, and "Pause
  screen sharing" pauses this too.
- **Everything Pyxi does in a tab is in the Activity tab** of Pyxi's panel, including a
  click that was sent and never answered (stopped, or the browser went quiet): the entry
  says it may or may not have happened.

## Permissions, and why

| Permission | Why |
| --- | --- |
| `activeTab` | Clicking the Pyxi icon lets it into that one tab, until you leave the site. |
| `scripting` | To put `content.js` into a tab you've let it into. Only that file, only then. |
| `storage` | To keep the pairing's key and the port, in the extension's own storage. |
| `alarms` | To try again twice a minute when Pyxi isn't running. |
| site access (optional) | Asked for one site at a time, when you press "Always allow on this site", or for every site if you tick that box. Nothing is granted when the extension is installed. |
| `tabs` (optional) | Only if you tick "See the names of all my open tabs". |

Not asked for: cookies, history, downloads, bookmarks, web requests, the debugger, native
messaging, clipboard.

## How it works

```
Pyxi's brain (127.0.0.1:8766)  <-- WebSocket /ws/browser -->  background.js  <-- messages -->  content.js in the tab
```

- `background.js` (a service worker in Chrome and Edge, an event page in Firefox) keeps the
  WebSocket, finds tabs, and puts `content.js` into a tab when Pyxi needs it there.
- `content.js` does the work in the page: reads the visible text, lists what can be clicked
  or typed into (each with a short ref like `e12`), acts on a ref, shows the marker, and
  listens for Esc.
- `popup.html` pairs the browser and shows where Pyxi may work.
- The brain's side is `src/pyxi/browserlink.py` (who may connect, the pairing) and
  `src/pyxi/companion/tabwork.py` (the tools, the cards, the loop that does a task).

The brain refuses a connection that isn't from this PC, that isn't from a browser extension
(a web page can open a socket to 127.0.0.1, but can't fake its Origin), or that hasn't been
paired. Pairing and every later connection are proven both ways (see "Pair it with Pyxi").
The brain keeps the pairing's key in `browser.json` in its data folder, encrypted for this
Windows user, and a pairing only works from the extension that made it.

## Checking it

```bash
python -m pytest tests/test_browserlink.py
```

```bash
python scripts/browser_e2e.py
```

The second one loads the extension into a throwaway browser profile, serves local test pages
and proves the whole path in a real browser (pairing, reading, the cards, the refusals, Esc,
planted instructions). `--browser msedge` and `--browser firefox` do the same in the
installed Edge and Firefox, each in its own temporary profile. Add `--real-model` for one
run with the real model (a few cents).

## Known limits

- Clicks and keys made by an extension are not "trusted" events. Nearly every site accepts
  them, but a few controls only react to a real mouse (some file pickers, some drag and
  drop, a few payment widgets). There Pyxi says it couldn't, and the mouse and keyboard
  route still exists.
- It works in the page itself and in frames from the same site. Frames from another site
  (embedded payment forms, some sign-in boxes) are not read or touched.
- The private-field check goes by the field's type, its autocomplete hint, its name and its
  label (in English and a few other languages, and names written as one word). A site that
  labels a card field as something unrelated would get past the name check, though a typed
  card number is still refused by its digits, in one piece or in pieces.
- A page built to do so can swallow the Esc key or cover the marker, and Esc isn't heard
  with the cursor in a frame from another site. The Stop in Pyxi's tray and the "Stop
  everything" hotkey don't depend on the page.
- The link is safe against another program on the PC pretending to be Pyxi or the extension.
  It is not hidden from one that can already read your browser's or Pyxi's own files: that
  program has your browser's cookies anyway.
- Chrome and Edge only keep an unpacked extension while Developer mode stays on, and may
  remind you about it at start-up. Publishing to the stores removes that, and is not done.
