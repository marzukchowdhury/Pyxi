# Pyxi in your browser

A browser extension that lets Pyxi, running on the same PC, read and work inside the tabs you
choose: your own tabs, where you're already signed in, without taking your mouse. One codebase
for Chrome, Edge and Firefox.

Without it, Pyxi has two ways into a website: Scout, who browses in a separate browser where
you aren't signed in, and the mouse and keyboard, which you can't use while Pyxi does. With
it, "fill this in", "add this to my cart" or "what does this page say about refunds?" happen
in the tab you're looking at, and you keep working.

It is not in the Chrome Web Store or on addons.mozilla.org. Pyxi installs it with you.

## Install it

**The easy way: let Pyxi do it.** With your browser open, go to Pyxi's panel, **Settings**,
**Connect your browser**, and press the button there. Or just say "connect my browser". Pyxi
also offers it by itself, once, the first time you ask for something that would be done in
your own tab ("add this to my cart"). If you say no it doesn't ask again; "connect my browser"
still works.

- **Firefox (140 or newer): Add to Firefox.** Pyxi opens the add-on in Firefox, Firefox asks
  whether to add it, and you press **Add**. It stays installed and updates itself. This needs
  the signed add-on to be published with a Pyxi release (see "Signing the Firefox add-on").
  Until it is, Pyxi doesn't show a dead link: it opens `about:debugging` and tells you to
  load the add-on for now, which lasts until Firefox closes.
- **Chrome and Edge: Set it up for me.** Chrome and Edge only install an extension from
  outside their stores as an "unpacked" folder with Developer mode on (anything else needs a
  PC managed by an organization). Pyxi does the fiddly parts: it keeps the extension in a
  folder that never moves (`%LOCALAPPDATA%\Pyxi\browser-extension`, brought up to date in
  place whenever Pyxi updates), opens the browser's extensions page, copies the folder's
  address, and points at each thing to click on your screen, waiting for you each time:
  **Developer mode**, **Load unpacked**, then paste into the **Folder** box and press
  **Select Folder**. Then it shows the pairing code and points at the extension's icon.
- **Brave, Vivaldi and Opera** get the same steps as Chrome, because they share its
  extensions page. That was not checked in those browsers.

What to expect afterwards in Chrome and Edge, plainly:

- The extension stays loaded when the browser restarts, **as long as Developer mode stays
  on**. Switch Developer mode off and the browser switches the extension off; switch it on
  again and it comes back.
- **Edge asks "Turn off developer mode?" each time it starts.** Press **Not now**. ("Turn off"
  switches the extension off.) Chromium showed no such question at start.

**By hand, Chrome or Edge**

1. Type `chrome://extensions` (Edge: `edge://extensions`) into the address bar.
2. Turn on **Developer mode**. Chrome: top right. Edge: bottom of the menu on the left; in a
   narrow window that menu is behind the three lines at the top left.
3. Press **Load unpacked** (in a narrow Edge window it is a small folder button on the right)
   and pick `%LOCALAPPDATA%\Pyxi\browser-extension`, or this `extension` folder in a
   checkout.
4. Pin it: the puzzle icon in the toolbar, then the pin (Edge: the eye) next to "Pyxi in
   your browser".

**By hand, Firefox, without the signed add-on**

```bash
python extension/build.py firefox
```

Open `about:debugging#/runtime/this-firefox`, press **Load Temporary Add-on** and pick
`extension/dist/firefox/manifest.json`. It is gone when Firefox closes.

## Signing the Firefox add-on (the founder does this once per extension version)

Firefox keeps an add-on only if Mozilla signed it. It does not have to be listed on
addons.mozilla.org: an "unlisted" add-on is checked automatically, signed and handed back,
and Pyxi hosts the file itself. No source upload is needed, because the code is plain
JavaScript exactly as written (nothing is minified or bundled).

1. Create a Mozilla account: go to <https://addons.mozilla.org>, press **Log in**, and
   register. Open **Developer Hub** once; if it shows a distribution agreement, accept it.
2. Make the API credentials: <https://addons.mozilla.org/developers/addon/api/key/>, press
   **Generate new credentials**. It shows a **JWT issuer** and a **JWT secret**.
3. In your own terminal (PowerShell), put them in two variables. They live in that terminal
   only, and the script never prints them or writes them anywhere:

   ```
   $env:WEB_EXT_API_KEY = "<the JWT issuer>"
   $env:WEB_EXT_API_SECRET = "<the JWT secret>"
   ```

4. Run the two commands. The first sends nothing: it builds the package and checks the two
   variables are there. The second sends the package to Mozilla and waits for the signed
   file, usually a minute or two.

   ```bash
   python extension/sign_firefox.py --check
   ```

   ```bash
   python extension/sign_firefox.py
   ```

5. It leaves `extension/signed/pyxi-firefox.xpi` and `extension/signed/pyxi-firefox-updates.json`.
   Commit both. The next `scripts/release.py` uploads them with the release, and from then
   on **Add to Firefox** works in Pyxi.

Where they are hosted, and why: as files of the GitHub release, reached through
`https://github.com/marzukchowdhury/Pyxi/releases/latest/download/`. That address never
changes, it is how Pyxi's own installer already updates, and it needs no extra deploy; the
website has no address of its own yet. The add-on's manifest carries the updates file's
address there, so installed copies find new versions by themselves. Every release uploads
both files again (the same ones, until you sign a new version), so "latest" always has them.

A version can be signed only once. After changing the extension's code, raise `"version"`
in `manifest.json`, then run step 4 again and commit the new files.

What the add-on declares to Mozilla (in `manifest.firefox.json`): it passes website content
and browsing activity (the page's text, its address) to Pyxi on the same PC, and nowhere else.

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

Two tools appear in Pyxi while a browser is connected. While none is, the only difference
is the offer to connect one (above):

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
python -m pytest tests/test_browsersetup.py
```

```bash
python scripts/browser_e2e.py
```

```bash
python scripts/browser_setup_check.py
```

The first two are unit tests (the link and the tools; installing, signing, the release step
and the offer). The third loads the extension into a throwaway browser profile, serves local test pages
and proves the whole path in a real browser (pairing, reading, the cards, the refusals, Esc,
planted instructions). `--browser msedge` and `--browser firefox` do the same in the
installed Edge and Firefox, each in its own temporary profile. Add `--real-model` for one
run with the real model (a few cents).

The last one checks the store-free install in throwaway profiles of Chromium, Edge and
Firefox: that the extensions page can't be opened from the command line but can by typing
its address into a new window, that the controls Pyxi points at exist under the names it
uses, that "Load unpacked" with Pyxi's folder loads the extension, that it survives a restart
and an in-place update, what happens with Developer mode off, what Edge asks at start, and
that Firefox treats the add-on's address as an install. Two steps put a window on the screen
for a few seconds (one types an address into it); `--unseen` skips them.

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
- Chrome and Edge only keep an unpacked extension while Developer mode stays on, and Edge
  asks about it at every start. Publishing to the stores removes that, and is not done.
- Pyxi opens the extensions page by typing its address into a new browser window. If another
  window comes to the front meanwhile it stops typing, and the pointer starts at the address
  bar instead.
- Branded Chrome was not driven in the checks (it can't be started with a test extension);
  Chromium of the same version and Edge were.
