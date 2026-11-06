# Pyxi privacy policy

*Draft for the Pyxi beta, last updated 6 November 2026. Have a lawyer review this before launch; it describes how the software works today, not legal advice.*

Pyxi is a desktop assistant that can see your screen, so privacy is the core of the product, not a footnote. This page says plainly what Pyxi looks at, what leaves your computer, what is kept, and how to delete it.

**Who we are.** Pyxi is made by Marzuk Haider Chowdhury, British Columbia, Canada. Contact: **[privacy contact email]**.

## The short version

- Pyxi looks at your screen **only when you talk or type to it**, unless you turn on the Memory Palace yourself.
- Passwords, password fields, password managers, banking apps and anything that looks like an API key or token are **blacked out before anything is sent**.
- What you ask, plus the screenshot and on-screen text it needs to answer, goes to an AI model to work out the reply. **We don't sell it, and we don't use it to train models.**
- Your history stays **on your computer**. Conversations are deleted after 30 days; Memory Palace history after 72 hours.
- Pyxi uses your mouse and keyboard **only after you press Allow**, and anything that sends, buys, deletes or posts asks again.

## What Pyxi looks at

**When you talk or type to Pyxi**, it captures the screen you're on: a screenshot, the names and positions of buttons and other controls (Windows UI Automation), and the text on screen (Windows' own offline text recognition). It doesn't capture your screen in the background unless you turn on the Memory Palace.

**The Memory Palace (off by default).** If you turn it on, Pyxi keeps a running record of window titles, what you clicked or focused, and where controls were, so you can ask "how did I get here?" or go back in time. It records no keystrokes. Small screenshots are kept only if you also turn on "keep small screenshots". This history stays on your computer and is deleted after 72 hours.

**Never captured:**

- password fields;
- password managers, banking apps and credential windows (a list you can change);
- text that looks like a credential, such as API keys, access tokens, private keys and `KEY=value` lines. It's removed from the text and blacked out of the screenshot pixels.

You can pause all screen access at any time with <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd>.

## What leaves your computer

| What | Where it goes | Why |
|---|---|---|
| Your request, the masked screenshot and on-screen text, and recent conversation | The AI model, through OpenRouter (during the beta, via the Pyxi beta server) | To understand your screen and answer |
| Web searches you ask for | DuckDuckGo or Bing | To look things up |
| Pages a teammate opens | Those websites, in a browser on your computer | To do the research or task you asked for |
| The booking details you saved (only the ones a form asks for) | The booking site you approved, typed in by Scout in a browser you can watch | To book the appointment, tickets or table you asked for. Each booking shows you which details it will use before it starts, and you approve the final booking click |
| Emails, calendar events and documents you ask Pyxi to send or create | Google, Discord or LinkedIn, only for accounts you connected | To do the errand. Every email, post or message shows you an approval card first |
| Optional premium voices | ElevenLabs or Google, only if you add your own key | To speak with that voice |

**AI providers.** OpenRouter passes your request to the model provider (Anthropic's Claude by default). They process it to produce the answer, under their own terms and privacy policies ([OpenRouter](https://openrouter.ai/privacy), [Anthropic](https://www.anthropic.com/legal/privacy)). **[Before launch: confirm and state the data-retention and training settings on your OpenRouter account.]** In local mode (Ollama), nothing goes to any AI provider.

**The Pyxi beta server** forwards your requests to OpenRouter with our key, so you don't need one. It **doesn't store what you ask or what's on your screen**. It keeps only:

- a scrambled (hashed) form of your beta token;
- which invite code you used;
- running totals of what you spent today and what the whole beta spent this month, to enforce the free allowance.

It sends OpenRouter an anonymous per-user ID, not your name or email, so abuse can be spotted.

**On your computer:** speech-to-text (Whisper) and the default voice (Kokoro) run on your computer. Your voice recordings aren't sent anywhere.

## What's stored on your computer

Everything lives in `%APPDATA%\Pyxi\.pyxi`:

- your booking details (name, email, phone, date of birth, city and preferences, as you entered them in Settings). Health card, insurance, SIN and passport numbers are never saved; if a form needs one, Pyxi asks you to type it yourself;

- conversations, with the masked screenshot each answer used (deleted after 30 days);
- things you asked Pyxi to remember;
- Memory Palace history, if it's on (deleted after 72 hours);
- teammates' task records;
- your settings;
- sign-in tokens for accounts you connected;
- your beta token.

Files Pyxi makes for you go to `Documents\Pyxi`.

## Bookings and payments

Pyxi never pays for anything and never types card numbers. When a booking reaches a payment page, Scout stops and hands the browser window to you; you pay, then press Done. Final booking buttons ("Book appointment", "Reserve", "Confirm") always need your approval first.

## Using your mouse and keyboard

Pyxi controls your mouse and keyboard only after you press **Allow** on its request card, once or "always". Moving the mouse or pressing <kbd>Esc</kbd> stops it instantly. Sending, paying, buying, deleting, posting, signing in or out, and closing tabs or apps always ask again. It never types into password fields.

## Your choices

- **See it:** the Memory tab shows what Pyxi remembers. Settings shows what's connected.
- **Delete it:**
  - "Forget everything" in the Memory tab wipes the Memory Palace.
  - "Forget …" removes a memory.
  - Disconnect an account in Settings to remove its tokens.
  - Uninstalling Pyxi and deleting `%APPDATA%\Pyxi` removes everything.
- **Beta data:** email **[privacy contact email]** to have your beta token and spend totals deleted.

## Children

Pyxi isn't meant for children under 13, and we don't knowingly collect their information.

## Changes

If this policy changes, the new version will be in the app and on our website, with the date at the top. Big changes will be shown in the app before they take effect.
