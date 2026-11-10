# Pyxi on macOS: the port

Branch `claude/mac-port`. Written 2026-11-09.

**Status: built blind.** There was no Mac available while this was written. Every Mac code path
here has been run only against stand-ins for Apple's frameworks (`tests/macfakes.py`), on Windows.
None of it has run on a real Mac, against real pyobjc, real Accessibility or a real window server.
Expect first-run bugs. The checklist at the end is the way to find them.

The Windows app is unchanged in behaviour: every Mac difference sits behind
`sys.platform == "darwin"` (Python) or `process.platform === "darwin"` (Electron), and the Windows
suites pass as before (pytest, `scripts/scenarios.py`, desktop typecheck and vitest).

## How the platform layer works

Each Windows module stays the Windows implementation. At its end, a short block swaps in the Mac
functions on a Mac only, with the same names and arguments, so no caller changes:

```python
if sys.platform == "darwin":  # the Mac versions, same names and arguments (pyxi/macos/windows.py)
    from pyxi.macos.windows import *  # noqa: F403
```

The Mac modules live in `src/pyxi/macos/`. They import Apple's frameworks lazily
(`pyxi.macos.quartz()`, `appkit()`, `axapi()`), which is what lets the tests put fakes in
`sys.modules`.

**Coordinates.** On a Mac everything is in points with the origin at the main screen's top left:
Accessibility, Quartz events and the window list all use that already. Screenshots from `mss` come
at Retina size and are scaled down to points (`screen/context.py: to_points`). The overlay is told
its scale is 1 (`displayInfo` in `desktop/src/main/index.ts`). On Windows the brain still speaks
physical pixels.

**Window handles.** A window is its Quartz window number (`kCGWindowNumber`). To move, size or
focus one, Pyxi finds the matching Accessibility window by app and frame (`macos/_ax.py:
ax_window`). The two APIs share no id, so a window that changes size at the same moment can be
missed.

## What was ported

| Area | Windows | Mac | Where |
|---|---|---|---|
| Window list, screens, snap, focus | user32, DWM | `CGWindowListCopyWindowInfo`, `NSScreen`, AXUIElement | `macos/windows.py` |
| Reading the screen's controls | UI Automation | Accessibility tree, roles mapped to the UIA names | `macos/uia.py` |
| Taskbar buttons | Shell_TrayWnd via UIA | Dock items via Accessibility | `macos/uia.py: read_taskbar` |
| Browser address | UIA address bar | AX address field, else AppleScript (Safari, Chrome, Edge, Brave, Arc) | `macos/uia.py: browser_url` |
| Mouse and keyboard | SendInput | Quartz `CGEvent`, media keys as `NSSystemDefined` events | `macos/hands.py` |
| Shortcut names | as written | ctrl+key becomes Cmd+key (ctrl+tab stays Control), alt is Option, win is Cmd, alt+tab is Cmd+Tab, alt+f4 is Cmd+Q | `macos/hands.py: mac_combo` |
| Guarding Pyxi's own windows | WindowFromPoint + pid | AX hit test at the point + pid, frontmost app for keys | `macos/hands.py` |
| OCR | Windows.Media.Ocr | Vision `VNRecognizeTextRequest` | `macos/ocr.py` |
| Apps and folders | `os.startfile`, `shell:` folders, `ms-settings:` | `open -a`, `~/Downloads` style folders, `x-apple.systempreferences:` pages; a Mac `APPS` table (Safari, Notes, Calendar, Finder, Music, Spotify and more) | `macos/apps.py` |
| Volume | Core Audio (pycaw) | `osascript` `set volume` | `macos/media.py` |
| Spotify | `spotify:` link + Enter | AppleScript `play track`, then `player state` | `macos/media.py` |
| YouTube | Pyxi's own Edge player, sound meter | `open -g` in the default browser | `macos/media.py` |
| Tokens at rest | DPAPI | AES-GCM under a 256-bit key in the login Keychain (`app.pyxi.desktop` / `secret-store-key`, this device only) | `macos/secretstore.py` |
| File search | Windows Search (ADODB) | Spotlight (`mdfind -onlyin`) | `macos/files.py` |
| Time zone | `GetDynamicTimeZoneInformation` | `/etc/localtime` link | `localzone.py: mac_zone` |
| Brain quits with the app | `WaitForSingleObject` | polls `os.kill(pid, 0)` each second | `cli.py` |
| Opening files | `os.startfile` | `open` | `paperwork.py`, `operator.py: _start` |
| LibreOffice | downloads and unpacks the MSI | uses the user's own `/Applications/LibreOffice.app`, never downloads | `agents/libreoffice.py` |
| Ditto, price checks | UIA focused element and address bar | the Accessibility versions above | `agents/ditto.py`, `companion/pricecheck.py` |
| Operator prompt | Windows know-how | plus a Mac section (Spotlight, Finder, the Dock, Cmd shortcuts); Cmd+W and Cmd+Q count as closing keys | `companion/operator.py` |
| Hold-to-talk, dictation | Ctrl+Alt, Ctrl+Win (+F24 tap) | Control+Option, Control+Command, no F24 tap; either chord configurable | `desktop/src/main/holdToTalk.ts` |
| Paste | Ctrl+V | Cmd+V | `holdToTalk.ts: pasteText` |
| Overlay | full display, always on top | the display's work area (below the menu bar and the MacBook camera notch), `type: "panel"`, all Spaces | `index.ts` |
| Tray | SVG firefly | a template image drawn as pixels for the menu bar; no Dock icon | `index.ts`, `mac.ts` |
| Panel | acrylic | vibrancy | `index.ts` |
| Global shortcuts | ...+Pause for Stop | Control+Option+Escape for Stop (Mac keyboards have no Pause) | `mac.ts: MAC_HOTKEYS` |
| Files that run | `.exe`, `.bat`... shown in Explorer | also `.app`, `.command`, `.sh`, `.pkg`, `.dmg`, `.scpt`... shown in Finder | `mac.ts: MAC_RUNNABLE` |
| Bug reports | "Windows 10.0.x" | "macOS (Darwin x)" | `trayMenu.ts: systemName` |

Microphone and speech needed no port: the microphone is captured in the Electron page, Whisper
and Kokoro run on onnx and CTranslate2, which have Mac wheels. CUDA is never used on a Mac.

### Permissions onboarding

`desktop/src/renderer/permissions.html` with `src/permissions/main.ts`: a small window at first
start, and from the menu bar icon ("Permissions..."). It lists Screen Recording, Accessibility,
Microphone and Input Monitoring, what each is for, and whether it's allowed. Each button opens the
right page of System Settings (`x-apple.systempreferences:com.apple.preference.security?Privacy_...`).
The Microphone button asks macOS for its own prompt first (`askForMediaAccess`), the one
permission an app may ask for itself. Pyxi never changes a setting. After Screen Recording is
granted, the card offers "Restart Pyxi" (macOS only applies it to a fresh process). "Later" stores
`.pyxi/mac-permissions.json` so the card doesn't come back by itself.

Info.plist usage strings are in `desktop/package.json` (`build.mac.extendInfo`): microphone, screen
capture, Apple Events, Documents, Desktop, Downloads.

### Configuring the chords

`PYXI_TALK_CHORD` and `PYXI_DICTATE_CHORD`, or `.pyxi/hotkeys.json` in Pyxi's data folder:

```json
{ "talk": "ctrl+option", "dictate": "ctrl+cmd" }
```

Any two or three of ctrl, alt/option, meta/cmd/win. A chord the same as the other one is moved
off it. Fn can't be used: the keyboard hook (libuiohook) doesn't report Fn on a Mac.

## What's stubbed or reduced on a Mac

- **"Is the song playing?"** A Mac has no per-app sound meter without extra system software, so
  `is_sounding` is always False and YouTube songs are reported as opened, not as playing. Pyxi
  never presses play blind there (it could pause the song).
- **Pyxi's own music player window** (the minimised Edge on Windows) doesn't exist on a Mac.
- **LibreOffice** isn't installed on demand; Pixel uses one the user installed.
- **Pyxi in your browser** (the extension) is set up by hand on a Mac: Settings, under Connect
  your browser, shows the steps and the folder (`~/Library/Application Support/Pyxi/
  browser-extension`). "Set it up for me" says the same steps in words instead of typing into
  the browser, and Pyxi doesn't offer the setup unasked. Once connected, tab work is the same
  as on Windows.
- **Automatic updates are off** on the Mac build (`MAC_UPDATES = false` in `updates.ts`):
  Squirrel.Mac only installs updates signed by the same Developer ID, and there's no
  `latest-mac.yml` in the releases yet. The build already makes the zip electron-updater needs.
- **The bundle isn't slimmed** (no zipped stdlib, no separate voice pack) and keeps Playwright's
  own Node. Expect a dmg of several hundred MB.
- **Esc to stop** is "Esc held down right now" (`CGEventSourceKeyState`): a very quick tap between
  two checks can be missed. Grabbing the mouse always stops it.
- **The lock** for the Mac Python (`requirements/mac.txt`, hashes) doesn't exist yet: it has to
  be made on a Mac. Until then the build installs from `requirements/mac.in` with floating
  versions, and prints a warning.
- Chrome and other Chromium/Electron apps only expose their web content to Accessibility when an
  assistive app asks for it (`AXManualAccessibility`); Pyxi doesn't set that yet, so inside web
  pages it relies on the screenshot and OCR.

## Never run on a real Mac (all of it), and what's most likely to break

Everything in `src/pyxi/macos/`, the Mac branches in `desktop/src/main/index.ts`, the permissions
card inside Electron, `scripts/build_mac.py`, the electron-builder `mac` config and
`.github/workflows/mac-build.yml`. In rough order of risk:

1. **pyobjc calling conventions.** The fakes assume pyobjc's usual shapes: `(error, value)` from
   `AXUIElementCopyAttributeValue`, `(ok, struct)` from `AXValueGetValue`, `(status, data)` from
   `SecItemCopyMatching`. A different shape on the real thing means an empty tree or a crash
   caught and logged.
2. **Matching Quartz windows to AX windows** by frame (title as fallback).
3. **TCC attribution.** Pyxi assumes the bundled Python, started by Pyxi.app, is covered by the
   permissions granted to Pyxi.app. If macOS asks for Python separately, the card needs to say so.
4. **The overlay** as a transparent, click-through `panel` window over full-screen apps, at
   `screen-saver` level, on every Space, and `setContentProtection` hiding it from Pyxi's own
   screenshots.
5. **The keyboard hook** on a Mac (Accessibility vs. Input Monitoring), and Control+Option not
   clashing with VoiceOver for people who use it.
6. **Screen capture.** `mss` uses `CGDisplayCreateImage`; macOS 15 still allows it but shows a
   periodic "is requesting to bypass the private window picker" prompt. ScreenCaptureKit would
   avoid that.
7. **Signing the bundled Python.** electron-builder should sign every Mach-O file in the app,
   including `Contents/Resources/python`, with the entitlements in
   `desktop/resources/entitlements.mac.plist`. If notarization rejects a file, that's where.
8. **The Intel build** on an Apple silicon runner (Python under Rosetta, `arch -x86_64`).
9. **Media keys** (`NSEvent` system-defined events) and the Spotify AppleScript.
10. **Keychain access** prompts after an update: an ad-hoc signed build changes identity each
    time, so macOS may ask whether Pyxi may use its own Keychain item.

## What needs an Apple account

An Apple Developer Program membership (paid, yearly) for:

- a **Developer ID Application** certificate (signing), and
- **notarization** (an App Store Connect API key, or an Apple ID with an app-specific password).

Without them the app is ad-hoc signed: it runs on Apple silicon, but Gatekeeper says it's from an
unidentified developer (right-click > Open the first time), permissions may need granting again
after each update, and automatic updates can't work.

The hooks are in place:

- `scripts/build_mac.py` signs with `CSC_LINK` + `CSC_KEY_PASSWORD` (a .p12) or `CSC_NAME` (a
  certificate in the keychain), else ad-hoc (`-c.mac.identity=-`).
- It notarizes when also given `APPLE_TEAM_ID` and either `APPLE_API_KEY` (path to the .p8) +
  `APPLE_API_KEY_ID` + `APPLE_API_ISSUER`, or `APPLE_ID` + `APPLE_APP_SPECIFIC_PASSWORD`.
- The workflow reads the same from repository secrets (listed at the top of
  `.github/workflows/mac-build.yml`). With none set it still builds, ad-hoc signed.
- Once builds are signed: set `MAC_UPDATES = true` in `desktop/src/main/updates.ts` and have
  `scripts/release.py` upload the Mac zip, dmg and `latest-mac.yml`.

## Building

On a Mac with Python 3.11+, Node 22 and Xcode's command line tools, beside a checkout of Otto in
`../otto`:

```bash
cd desktop && npm ci && cd ..
python3 scripts/build_mac.py --arch arm64     # or x64, or both
```

That downloads python-build-standalone's CPython 3.12.16 for the architecture (checked against
its SHA-256), installs `requirements/mac.in`, Otto and Pyxi into `desktop/build/python`, checks
that the bundle imports the brain and the Apple frameworks, then builds
`desktop/dist/Pyxi-<version>-<arch>.dmg` (and the zip). `--python-only` stops after the Python,
`--skip-python` reuses it.

To make the lock: `pip-compile --generate-hashes requirements/mac.in -o requirements/mac.txt`
(on a Mac, once per architecture if they differ).

GitHub Actions: `.github/workflows/mac-build.yml` (run by hand, on a push to `claude/mac-port`,
or on a `v*` tag) builds Apple silicon on `macos-latest` and Intel natively on `macos-15-intel`,
runs the Mac tests and the desktop tests, smoke-tests the bundled brain, and keeps the dmgs as
artifacts for a week. Apple silicon runners are sometimes short: a job nobody picks up is
cancelled after a while, and running it again later is the fix.

Developing on a Mac without building: `pip install -e . -r requirements/mac.in`, then
`npm run dev` in `desktop/` (the brain starts from `.venv/bin/python`).

## First-run test checklist (when a Mac is available)

Do it on Apple silicon first, then Intel if there's one. Keep `~/Library/Application
Support/Pyxi/.pyxi/brain.log` open (`tail -f`); most failures land there.

**Build and install**

1. `python3 scripts/build_mac.py --arch arm64` finishes, and its bundle check prints `bundle ok`.
2. Open the dmg, drag Pyxi to Applications. Right-click > Open (it's ad-hoc signed). It opens with
   no Dock icon and a firefly in the menu bar (check it in light and dark menu bars).
3. `ps aux | grep python3` shows the bundled brain; `curl -s 127.0.0.1:8766/api/health` answers.

**Permissions card**

4. The card appears at first start, listing four permissions with "Not yet" or "Allowed".
5. Each "Open Settings" opens the right page of System Settings. Nothing is changed by Pyxi.
6. Microphone: "Allow" shows macOS's own prompt. After allowing, the row turns to "Allowed" when
   you come back to the card.
7. Allow Accessibility and Screen Recording for Pyxi. Check whether macOS lists Pyxi, or
   "python3", or both (risk 3 above). "Restart Pyxi" appears and restarts it.
8. "Later" closes the card and it doesn't come back at the next start; the menu bar's
   "Permissions..." opens it again.

**Overlay and keys**

9. The notch sits just below the menu bar on every screen (and below the camera notch on a
   MacBook), stays above a full-screen app, and follows you across Spaces.
10. Clicks go through the overlay to the apps underneath, except on Pyxi's own cards.
11. Hold Control+Option, talk, let go: Pyxi hears it. A quick Control+Option tap does nothing.
12. Hold Control+Command in TextEdit and dictate: the words are typed there.
13. Double-tap Control opens the notch's text box, and typing goes there.
14. With `.pyxi/hotkeys.json` set to `{"talk": "option+cmd"}`, Option+Command talks instead.
15. Control+Option+Space, Control+Option+W, Control+Option+Escape (Stop everything) work.

**Seeing the screen**

16. Ask "what's on my screen?" over Safari, Notes and Finder: the answer is right, and
    `brain.log` shows elements read (not "Accessibility isn't allowed").
17. Point at something ("where's the share button?"): the pen lands on it, on a Retina screen and
    on an external non-Retina one (coordinates in points).
18. Text in an image (a photo with words) gets underlined: Vision OCR works.
19. A password manager window is blacked out of the screenshot; a password field is never read.
20. Pyxi's own drawings never appear in its own screenshots.

**Doing things**

21. "Open Notes", "open my Downloads folder", "open Spotify", "open sound settings".
22. "Put Safari on the left and Notes on the right": both snap to halves of the work area.
    Maximize, minimize, restore. Three and four panes.
23. With the user's OK, a small operator task in TextEdit ("type a shopping list"): the cursor
    glides, typing works with accents, Cmd shortcuts work (ctrl+s is pressed as Cmd+S). Moving the
    mouse stops it. Holding Esc stops it. It never clicks a Pyxi card (try with one open).
24. "Insert this into Notes" pastes with Cmd+V into the right window.
25. "Volume to 30%", "louder", "mute": the system volume changes.
26. "Play <a song> on Spotify" (Spotify installed): macOS asks once whether Pyxi may control
    Spotify; then it plays. Without Spotify: YouTube opens in the background browser.
27. "Next song", "pause": media keys reach Music or Spotify.
28. "What's the address of this page?" in Safari and Chrome (an AppleScript prompt appears once).
29. "Find my resume" with file access on: Spotlight finds it; macOS asks for Documents access once.

**Brain and data**

30. Connect Google: the token file in `.pyxi` starts with `PYXI-KEYCHAIN-1`, Keychain Access
    shows an item "Pyxi" (`app.pyxi.desktop`), and Pyxi stays connected after a restart.
31. The time zone in "what time is it in my calendar?" is the Mac's own (System Settings > Date &
    Time).
32. Quit Pyxi from the menu bar: the python3 process is gone within about a second.
33. A Scout job on example.com runs (Playwright's Chromium, or installed Chrome).

Write down each failure with the `brain.log` lines around it. The likely fixes are small and
local to `src/pyxi/macos/` or the `IS_MAC` branches in `desktop/src/main/index.ts`.

## Files

- `src/pyxi/macos/`: `windows.py`, `uia.py`, `hands.py`, `apps.py`, `media.py`, `ocr.py`,
  `secretstore.py`, `files.py`, `permissions.py`, `_ax.py`
- Swaps at the end of: `windows.py`, `screen/uia.py`, `hands.py`, `media.py`, `screen/ocr.py`,
  `secretstore.py`, `files_access.py`, `companion/actions.py`
- Mac branches in: `cli.py`, `localzone.py`, `screen/context.py`, `companion/operator.py`,
  `companion/brain.py`, `companion/paperwork.py`, `companion/pricecheck.py`, `agents/ditto.py`,
  `agents/libreoffice.py`
- Tests: `tests/macfakes.py`, `tests/test_macos_windows.py`, `tests/test_macos_hands.py`,
  `tests/test_macos_misc.py`; `desktop/src/main/mac.test.ts`, more cases in `holdToTalk.test.ts`
- Desktop: `desktop/src/main/mac.ts`, Mac branches in `index.ts`, `holdToTalk.ts`, `updates.ts`,
  `trayMenu.ts`; `desktop/src/renderer/permissions.html`, `src/renderer/src/permissions/`;
  `desktop/resources/entitlements.mac.plist`; `build.mac` and `build.dmg` in `desktop/package.json`
- Build: `scripts/build_mac.py`, `requirements/mac.in`, `.github/workflows/mac-build.yml`
