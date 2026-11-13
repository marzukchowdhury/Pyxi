# Pyxi privacy policy

*Draft for the Pyxi beta, last updated 7 November 2026. It describes how the software works today. It isn't legal advice: have a lawyer review it before the public launch.*

Pyxi is a desktop assistant that can see your screen, read the files you point it at and work in the accounts you connect, so privacy is the core of the product, not a footnote. This page says plainly what Pyxi looks at, what leaves your computer, what is kept, and how to delete it.

**Who we are.** Pyxi is made by Marzuk Haider Chowdhury, British Columbia, Canada ("we"). Contact: **[contact email]**.

## The short version

- Pyxi looks at your screen **only when you talk or type to it**, unless you turn on a feature that watches for you (the Memory Palace, price checks while you shop, or Ditto while you show it a task).
- Passwords, password fields, password managers, banking apps and anything that looks like an API key or token are **blacked out before anything is sent**.
- Pyxi **refuses to remember secrets**: a password, card number, government ID or key is never saved as a memory, even if you say one.
- What Pyxi reads is **data, not instructions**. Text in an email, page or document can't make Pyxi act: every send, booking and file change still needs your Allow, and the card says when a recipient came from something Pyxi read.
- What you ask, plus the screenshot, on-screen text and the parts of files or emails it needs to answer, goes to an AI model to work out the reply. **We don't sell it, and we don't use it to train models.**
- Your history stays **on your computer**. Conversations are deleted after 30 days; Memory Palace history after 72 hours.
- What you say to Pyxi is sent to a cloud model to be turned into text, because it mishears far less than a model that fits on a PC. **Neither Pyxi nor the Pyxi beta server keeps the sound**; the model provider processes it under its own terms (see below). You can keep your voice on your computer instead: Voice panel, "Turn my speech into text on this PC only". Then nothing you say is sent. Offline, your computer listens by itself. Meeting recordings are always turned into text on your computer.
- Pyxi uses your mouse and keyboard **only after you press Allow**. Anything that sends, books, buys, deletes or posts asks again. Pyxi never types passwords, card numbers or government ID numbers, and **never pays for anything**.

## What Pyxi looks at

**When you talk or type to Pyxi**, it captures the screen you're on: a screenshot, the names and positions of buttons and other controls (Windows UI Automation), and the text on screen (Windows' own offline text recognition). It doesn't capture your screen in the background unless you turn on one of the features below.

**The Memory Palace (off by default).** If you turn it on, Pyxi keeps a running record of window titles, what you clicked or focused, and where controls were, so you can ask "how did I get here?", go back in time, or have Trace find "where did I see that?". It records no keystrokes. Small screenshots are kept only if you also turn on "keep small screenshots". This history stays on your computer and is deleted after 72 hours.

**Ditto (only while you show it a task).** When you ask Ditto to watch you do something once, it records the windows you switch to, the buttons and fields that take focus, what ended up in each field when you left it (never a password field), a small screenshot at each window change, and the list of files in the folder you name. The recording stays on your computer, in Pyxi's data folder, until you delete it.

**Price checks while you shop (off by default).** With "Check prices while I shop" on, Pyxi reads a store's product page when one comes up in your browser, to look for the same item elsewhere.

**Never captured:**

- password fields;
- password managers, banking apps and credential windows (a list you can change in settings);
- text that looks like a credential, such as API keys, access tokens, private keys and `KEY=value` lines. It's removed from the text and blacked out of the screenshot pixels.

You can pause all screen access at any time with <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd>.

## Your files

**Searching your files is off until you allow it.** The first time Pyxi wants to look, it asks with a card, and you can switch "Let Pyxi search my files" on or off in Settings. It only looks inside the folders you allow (Documents, Desktop, Downloads and Pictures to start with), using the Windows Search index or a quick look through those folders.

**Files you hand to a teammate** are read on your computer. What happens next depends on the job:

| Teammate | What it does with the file | Sent to the AI model? |
|---|---|---|
| Pixel | Converts, merges, compresses, fills and compares documents | No, it all happens on your computer |
| Ledger | Edits Excel, Word and PowerPoint files, the workbook, document or deck you have open in those apps, and Google Sheets and Docs | An outline of the file or the open document, to decide the edits |
| Sage | Makes flashcards and quizzes from slides and notes | The text of the file |
| Scribe | Turns a meeting recording into notes | The recording is transcribed on your computer; only the transcript is sent |
| Patch | Explains an error in a script and proposes a small fix | The error and the lines around it |
| Forms | Fills a job or government form from a CV or document you name | The parts the form needs. Passwords, card numbers and government ID numbers are cut out first |

Results are saved as new files beside the original, or in `Documents\Pyxi`. Originals are never overwritten, with two exceptions you approve: Patch changes a script only after you press Allow on a card that shows the change (and keeps a backup), and Google Docs and Sheets are edited in place, where Google keeps every earlier version.

**A document open in Excel, Word or PowerPoint.** When you ask for a change to the workbook, document or deck you have open in the app, Ledger makes it right there, through the app itself, instead of with your mouse. The same outline as for a file goes to the AI model, and nothing else: no copy is made, nothing new is stored, and Pyxi never saves, closes or deletes the document. You save it, or not. Before a change would replace or remove more than a few filled cells, a card asks you first. Word and PowerPoint can undo these changes with Ctrl+Z; Excel usually cannot, so the card says so.

**LibreOffice (optional).** For exact Office layouts and old .doc, .xls and .ppt files, Pyxi can download LibreOffice from The Document Foundation, after a card asks you first. It's installed in Pyxi's own folder and changes nothing else on your PC.

## Your email and accounts

Connecting an account is optional. Until you connect one, Pyxi can't see it. You can connect Gmail, Google Calendar, Google Docs, Google Sheets, Discord and LinkedIn. Google and LinkedIn sign you in on their own websites; Pyxi never sees your password.

**What Pyxi asks Google for:** reading, drafting and sending email (Gmail); your calendar events; your Docs and Sheets; the Drive files Pyxi creates or you open with it; and the names of your Drive files, so you can say "my sales sheet".

**What reads your mail, and why:**

- **Atlas** finds airline and hotel confirmations to build your trips. It reads them on your computer; only a confirmation it can't read is sent to the AI model.
- **Penny** and the money features read receipts, billing and order emails to total your spending, find subscriptions and catch return deadlines. This is all worked out on your computer, with no AI model.
- **Trace** searches your mail, along with your screen history and files, when you ask where you saw something.
- **Errands you ask for** ("fix the letter Sam emailed me") read the emails involved and send what's needed to the AI model.

Pyxi never sends an email, posts, messages, invites someone or cancels anything without showing you an approval card first.

**Google user data.** Pyxi's use of information received from Google APIs follows the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements. We use Google data only to do what you ask Pyxi to do. We don't sell it, use it for advertising or use it to train models, and it isn't stored on our servers.

**Sign-in tokens** for connected accounts, and your beta token, are stored on your computer, encrypted with Windows' own data protection (DPAPI), so they can't be used if the files are copied to another PC or Windows account.

## What leaves your computer

| What | Where it goes | Why |
|---|---|---|
| Your request, the masked screenshot and on-screen text, recent conversation, and the parts of files or emails a job needs | The AI model, through OpenRouter (during the beta, through the Pyxi beta server), or the provider of your own key if you added one | To understand your screen and answer |
| What you say to Pyxi (the sound of each request, and dictation) | Google's Gemini model, through OpenRouter (during the beta, through the Pyxi beta server) | To turn your speech into text. With "Turn my speech into text on this PC only" switched on in the Voice panel, nothing you say is sent: your computer listens (Bengali then needs the offline Bengali model, which Pyxi offers to download) |
| Web searches you ask for, and price checks | DuckDuckGo or Bing, then the store websites that come up | To look things up and compare prices |
| Pages a teammate opens | Those websites, in a browser on your computer | To do the research, booking or form you asked for |
| Emails, calendar events, documents and posts you ask Pyxi to send or change | Google, Discord or LinkedIn, only for accounts you connected | To do the errand, after an approval card |
| Bookings and forms | The website you're booking with or filling in, with the details you saved | To make the booking. You approve the final step; you pay yourself |
| Optional premium voices | ElevenLabs or Google, only if you add your own key | To speak with that voice |
| Update checks | GitHub | Pyxi checks for a new version when it starts and every four hours, and downloads updates from GitHub |
| Voice and speech models | GitHub and Hugging Face | Downloaded once, the first time they're needed |

**AI providers.** OpenRouter passes your request to the model provider: Anthropic's Claude for answers and teammates, and Google's Gemini for turning speech into text. They process it to produce the answer, under their own terms and privacy policies ([OpenRouter](https://openrouter.ai/privacy), [Anthropic](https://www.anthropic.com/legal/privacy), [Google](https://policies.google.com/privacy)). **[Before launch: confirm and state the data-retention and training settings on your OpenRouter account.]** In local mode (Ollama), nothing goes to any AI provider.

**The Pyxi beta server** forwards your requests to OpenRouter with our key, so you don't need one. It **doesn't store what you ask, what's on your screen, or your voice**. It keeps only:

- a scrambled (hashed) form of your beta token, with the invite code you used and the date you joined;
- how many devices have used each invite code;
- what you spent each day (kept 3 days) and what the whole beta spent each month (kept about 40 days), to enforce the free allowance.

It sends OpenRouter an anonymous per-user ID, not your name or email, so abuse can be spotted. The server runs on Vercel and keeps its data in Upstash. Like any web host, they may keep standard technical logs, such as IP addresses and times, under their own policies.

## What's stored on your computer

Everything lives in `%APPDATA%\Pyxi\.pyxi`:

- conversations, with the masked screenshot each answer used (deleted after 30 days);
- things you asked Pyxi to remember;
- Memory Palace history, if it's on (deleted after 72 hours);
- Ditto's recordings, until you delete them;
- short notes on how apps and websites work ("the PivotTable button is on the Insert tab"), which Pyxi writes after doing a task in one so the next task there is quicker. At most six per app or site, in `learned.json`. They are about the app, not about you: what you typed, your files, names and numbers are kept out. "Wipe everything" clears them, and you can open or delete the file yourself;
- teammates' task records, flashcard decks and budgets;
- the booking details you saved (name, email, phone, date of birth, city and preferences). Health card, passport and other ID numbers are never saved;
- your settings, including which folders Pyxi may search;
- encrypted sign-in tokens for accounts you connected, and your beta token.

Files Pyxi makes for you go to `Documents\Pyxi`, or beside the file they came from. LibreOffice, if you allow it, goes to `%LOCALAPPDATA%\Pyxi\LibreOffice`.

## Using your mouse and keyboard

Pyxi controls your mouse and keyboard only after you press **Allow** on its request card, once or "always". An "always" grant expires after 30 days, and Settings lists every grant so you can revoke it. <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Pause</kbd> (also in the tray and Settings) stops everything at once: every teammate, the mouse and keyboard, and anything waiting to go out. Moving the mouse or pressing <kbd>Esc</kbd> stops it instantly. Sending, paying, booking, buying, deleting, posting, signing in or out, and closing tabs or apps always ask again. Pyxi never types into password fields, never types card numbers or government ID numbers, and never completes a payment: when a booking reaches payment, Pyxi hands the browser window to you.

## Your choices

- **See it:** the Memory tab shows what Pyxi and the teammates remember. The activity log in the panel lists everything Pyxi did on your behalf, with the time and the teammate (emails sent, buttons pressed, files moved, scripts changed), and can undo what can be undone. Settings shows what's connected, which folders Pyxi may search and which "always allow" grants are active.
- **Delete it:**
  - "Wipe everything" in the Memory tab forgets every memory, the Memory Palace and the conversations with their screenshots. It asks first.
  - "Forget …" removes a memory.
  - Disconnect an account in Settings to remove its tokens.
  - Turn off "Let Pyxi search my files" in Settings.
  - Uninstalling Pyxi and deleting `%APPDATA%\Pyxi` removes everything.
- **Beta data:** email **[contact email]** to have your beta token and spend totals deleted.

## This website

The Pyxi website uses no cookies, no analytics and no trackers. It's hosted on Vercel, which keeps standard request logs, and the installer downloads from GitHub.

## Children

Pyxi isn't meant for children under 13, and we don't knowingly collect their information.

## Changes

If this policy changes, the new version will be in the app and on our website, with the date at the top. Big changes will be shown in the app before they take effect.
