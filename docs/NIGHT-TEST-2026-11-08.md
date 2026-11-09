# Pyxi night test, 8 November 2026

A full pass over Pyxi before launch: the voice and personality first, then every feature and teammate, the way the morning try-out did it (docs/TRYOUT-BASICS.md), with the real model. Real accounts, mail and files were never touched: Gmail, Google and Scout's sites were fakes or local practice sites, and the hands only typed into Notepad.

## Voice and personality

**What was wrong:**
- **Stock lines.** The prompt told Pyxi to say "On it." in eight places, and the code then swapped that for a random stock phrase ("Aye aye", "Roger that", "Leave it with me").
- **Repeats.** Pyxi said a line before a tool and again, reworded, after it ("New doc, I'm guessing... Got you, opening a new blank doc").
- **Cue cards echoed the opening line.** The first point repeated it ("a $107.80 phone bill, due Nov 28... You owe $107.80, due November 28th").
- **"Done." too early.** It came after only a guide arrow or a look at the screen.
- **Symbols read badly.** "12°C", "5x5", "km" and "A1:C13" all came out wrong.

**What changed:**
- **Starting work names the job.** Pyxi says one short line about the actual job, with personality: "Penny's going through your receipts for anything quietly renewing.", "Notepad, coming up." The stock phrases are gone.
- **A "Who you are" section.** Pyxi now has tastes and opinions, light firefly self-awareness, running jokes about the teammates, callbacks to earlier in the conversation, and owns mistakes in one line.
- **Repeat filter.** Later rounds are told what was already said out loud. The filter compares normalised words (Nov/November, 28th/28, Altman's/Altman) and drops a sentence when nearly everything in it was already said. A cue-card point that repeats keeps its reveal mark and loses only the words.
- **Spoken forms.** "12 degrees", "5 by 5", "kilometres", "A1 to C13".
- **"Done."** is said only when something was actually done.

Examples from tonight's runs:
- "Anytime. Your tab bar is doing heroic work, so go easy on it tonight."
- "Pretty good, thanks for asking. I've spent the evening watching you scroll past a post about Sam Altman and RAM, which is a strange thing to be the only witness to."
- "The sheet's empty, so there's nothing to pivot yet. Want me to drop in a small sample sales table so you can practise on it?"

## Bugs found and fixed

| # | Bug | Where | Fix |
|---|---|---|---|
| 1 | Stock acks ("Aye aye", "Roger that") instead of real lines | prompts.py, brain.py | Starting-work rule, plain acks, instant routes name what opens |
| 2 | Reworded repeats across rounds and in cue cards | brain.py `drop_repeats` | Normalised meaning-word comparison, ALREADY_SAID note, keep marks |
| 3 | Saying "Stop." didn't stop anything (only the hotkey did) | brain.py, instant.py | A plain spoken stop runs Stop everything at once, no model |
| 4 | Scout's second job in a row failed with "No open tabs" | otto session.py | Wait for Chrome to release the profile; fall back if the window has no tab |
| 5 | Screen reading failed in the Start menu and popups (`c_long` TypeError) | screen/uia.py | Ask Windows for the top-level window; a Notepad hands task went 28 s to 13 s |
| 6 | Cloudflare bot checks kept Scout waiting 78-111 s | otto session.py | A BOT_CHECK notice at once: use another source or say the site blocks it (20 s) |
| 7 | "Done." after only a guide step or a look | brain.py | Only after a tool that does something |
| 8 | "12°C", "5x5", "km", "A1:C13" read badly | voice/tts.py | Spoken forms |
| 9 | "Open a new tab with Google Maps" took the slow path | instant.py | Instant route |
| 10 | Routines time box cut "09:00 AM" to "09:00 AI" | panel.css | Wider |
| 11 | Pivot lesson on an empty sheet had nothing to coach | prompts.py | Offers sample data first |
| 12 | A silent turn cut short by the next request looked like a bug | brain.py | Recorded as "interrupted" |
| 13 | Opening a finished file could crash on a missing job | filework.py | Guarded |
| 14 | "Penny's... no, the subscriptions finder is..." (corrected itself mid-line) | prompts.py | Subscriptions are Penny's; decide the line first |
| 15 | "Penny's setting your budget. Budget's locked in." | prompts.py | Instant things just say they're done |
| 16 | Atlas missed confirmations written as prose (answered Toronto for Calgary) | agents/atlas.py | Read by the model too |
| 17 | Atlas "next month" / "this week" picked the wrong trip | agents/atlas.py | Matched by date |
| 18 | Sage marked unheard quiz questions wrong, "last question" on question 1 | specialists.py | One reply answers one question |
| 19 | Penny added US$ receipts into CA$ totals | agents/penny.py | Kept apart |
| 20 | Ledger dates showed as plain numbers in Excel | agents/ledger.py | Date format from the column |
| 21 | Notifications dropped "1 edit didn't work", showed Markdown, cut mid-word | jobs.py, brain.py | One sentence per result, plain text, `clip` |
| 22 | Scribe "(by This week)", Ditto repeated its plan as "done", Trace showed full paths | scribe.py, specialists.py, trace.py | Wording |
| 23 | A copied example line ("every total gets checked twice") in 4 of 6 handoffs | prompts.py | Removed; no example said word for word |
| 24 | Email card's Gist line repeated the body's first line | connectors/google.py | Only Pyxi's own summary |

## Results (final runs, all fixes in)

| Suite | Result | Cost |
|---|---|---|
| Python unit tests | 650 passed | free |
| Desktop tests and typecheck | 44 passed, clean | free |
| Scenarios (real model, recorded screens, 199 cases incl. the morning try-out) | 195/199; the 3 real misses fixed and re-checked 8/8; the 4th is a joke 5 characters over the limit | $0.30 |
| Teammate jobs end to end (33 cases, all 10 teammates, fake data) | 33/33 | $0.03 |
| Email errands on a fake mailbox | 5/5 (each stops at the approval card) | $0.01 |
| Prompt injection (mail, pages, docs) | 7/7 | $0.01 |
| Bookings on practice sites (dentist then cinema, back to back) | 2/2, about 30 s each | $0.02 |
| Job application from a CV, flight check-in | 2/2; the SIN never reached Scout | ~$0.02 |
| Hands (Notepad, Calculator) | 3/3, no screen-reading errors | ~$0.01 |
| 3D: new model / edit / explain | first part 6.5 s, full 17 s / 2.7 s / 6.5 s | $0.05 |
| Security audit of what ships | nothing high or critical | free |

**Timing:** instant routes (open an app, site or blank file, media keys, play a song) answer in 0.3 s; weather 1.5 s; Sheets and Docs edits 3-5 s; a spoken answer starts in 3.4-4 s, a screen explanation in 4.4-5.9 s.

## Still open

- **Bill tags sometimes float.** On the insurance statement, 1 run in 3 left two tags unpinned (no quoted line). The answer itself was right every time. That's the model, not the code.
- **First words take 3.4-4 s.** That's the model reading 25-37k tokens of instructions and screen context per turn. Trimming the prompt is the next big speed-up.
- **Not tested tonight:**
  - real Google Docs and Drive (only fakes);
  - real Whisper transcription of a meeting;
  - Spotify (not installed);
  - merging your real Firefox windows (it's your tabs);
  - the tray menu by right-click (covered by unit tests only);
  - music playback (it would have played sound in the night; verified this morning).
- **Left on screen:** Notepad has three unsaved test tabs from the hands tests ("Pyxi night test, line one", "second line", "third line"). Close them without saving.

---

# Live session and the overnight A–Z run (8–9 November)

After the first report, the founder tested Pyxi live, then left the PC on overnight on all three screens. The overnight run used a driver (`.pyxi/night/driver.py`) that talks to the real Pyxi Dev app the way the notch's text box does: real screens, windows, mouse, teammates and the connected Google account. **138 cases, A to Z,** ran in three rounds: each failure was fixed, then everything was run again.

**Safety during the run:**
- The PC was muted; its sound was put back in the morning.
- The driver only approved the "use your mouse and keyboard" card, for harmless tasks; every other card was declined.
- Cases that would act on real accounts (sending email, creating a meeting, buying, deleting, tidying a folder) were left out. They stay covered by the fake-mailbox and practice-site checks.

## Bugs found live and overnight (all fixed, each with a test)

| # | Bug | Fix |
|---|---|---|
| 25 | A 7am alarm took 2 minutes of the mouse in the Clock app, was right only by luck, then looped | Alarms and timers are Pyxi's own: understood without the model, set in about 1 s, rung with a chime, a card and a line |
| 26 | "Make it one-time" deleted the alarm and set nothing; model alarms repeated every day | Changing an alarm sets the new one; an alarm or reminder without days rings once |
| 27 | "Remind me in 2 minutes to drink water" became a reminder every day at 12:02 | "Remind me in N minutes to X" and "remind me at 5pm to X" ring once, instantly |
| 28 | "Wake me up at 6:30 tomorrow" said at 12:30 AM was set a day late | After midnight, "tomorrow" means the coming morning, and Pyxi says so |
| 29 | Saying "Pyxi" was heard as "Your pixxit", "Epic City", "Pixie", "Pixig"; a request was read as a complaint | Misheard wake words become "Pyxi," before anything reads them; "alarm" and "timer" are speech hotwords |
| 30 | "Can you see my three screens?" got "I can only see one screen" | Every turn knows each screen and what's on it; it can look at one, and put or lay out windows on one |
| 31 | A split screen put both windows behind the full-screen window | The panes end up in front |
| 32 | Ledger edited the morning's "Untitled spreadsheet" instead of the blank one on the middle screen | The open file is found on any screen, and an Untitled file is never looked up by name |
| 33 | The hands kept going after the task was done (alt+tab after Play, taskbar clicks after the alarm) | A finishing step tells them to stop; switching windows after it ends the task; repeated actions count as circles |
| 34 | "Play the song on my middle screen" played a Fortnite track called "Meet Me in the Middle" | Screen and window requests aren't song searches; play or pause on a screen is one key press |
| 35 | Typing "in Notepad" went into the Calculator | The named app is brought to the front before typing |
| 36 | **Penny froze the whole brain** on one email full of spaces | Two mail patterns rewritten to run in time proportional to the email; email text tidied first |
| 37 | **Penny counted job-alert emails as spending** (salaries read as charges) | Job alerts, recruiters and unpaid big numbers are never spending or subscriptions |
| 38 | Every Google call reloaded certificates (25 Gmail reads: 6.6 s) | One kept-open client: 0.37 s. Web search, page reads and weather share one too |
| 39 | Weather for "this weekend", "on Saturday" or "is it going to rain?" was slow, or asked which city | Answered from the forecast in about 1 s, with the city from this PC's time zone when Google's isn't allowed |
| 40 | "Open my Downloads folder" used the mouse (20 s) | Folders and more apps open instantly; "search YouTube for X", "google X" and "…, open Gmail" too |
| 41 | "Who won the last F1 race?" took four model rounds (21.6 s) | One search, at most one page: 7.7 s |
| 42 | "Close the 3D model" hid parts one by one (12 s) | Instant |
| 43 | "Quiz me on the heart" asked for slides | A short deck from general knowledge |
| 44 | Spotify "opened" though it isn't installed | The web player opens instead |
| 45 | The Stop everything hotkey was never registered (Electron threw on "Pause") | The next shortcut is tried |
| 46 | "Pause the music" crashed (a local import named like a module) | Fixed |
| 47 | Ledger's report said "bold" when it made the text bold and blue | Says every change |
| 48 | Scout's second job in a row failed; bot checks kept it waiting 2 minutes | Fixed in Otto (two commits) |
