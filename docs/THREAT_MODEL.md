# Pyxi threat model

*Short and practical, for the beta (7 November 2026). For deciding what to protect first and what a security tester should try. Update it when Pyxi gains a new way to act or a new place it reads from.*

## What we protect

| Asset | Where it lives | Why it matters |
|---|---|---|
| What's on the user's screen | Captured per turn; the Memory Palace (opt-in) keeps metadata | Anything the user can see, including private messages and documents |
| Their accounts (Gmail, Calendar, Docs, Sheets, LinkedIn, Discord) | OAuth tokens in `%APPDATA%\Pyxi\.pyxi\connectors`, DPAPI-encrypted | Read and send as them |
| Their files | Read on request inside allowed folders; outputs in `Documents\Pyxi` | Private documents; a write can destroy work |
| Their mouse and keyboard | `pyxi/hands.py`, after a card | Pyxi can do anything they could |
| The approval cards | The overlay and the panel | The one gate between a model's wish and the world |
| Personal details | Booking profile, memories, conversations, activity log | Identity, contacts, habits |
| The beta's money | The OpenRouter key on Vercel; per-tester tokens | Spending, and access for everyone |
| The update channel | GitHub releases, electron-updater | Code that runs on every tester's PC |

## Who might attack

- **A web page, email or document** the user (or a teammate) reads, carrying words aimed at the AI (prompt injection). This is the most likely attacker by far: anyone can send an email.
- **A website in the user's browser**, trying to reach the local brain on 127.0.0.1.
- **Another program on the PC**, or another Windows user, trying the same.
- **Malware running as the user.** It can read what the user can, so we mostly limit what it gains *through Pyxi* (it can't drive cards, and tokens are useless elsewhere).
- **A curious or abusive tester**, against the beta server: free model use, others' data, guessing invite codes.
- **Someone with a leaked secret**: the OpenRouter key, a tester token, the admin token, the Google client secret.
- **A compromised dependency or release**: a changed PyPI or npm package, or a fake update.

## Entry points and what guards them

| Entry point | Mitigations | Tested by |
|---|---|---|
| The brain's WebSocket and HTTP API on 127.0.0.1 | A per-launch key (the brain refuses everything without it), an Origin check against web pages, `/file` serves pictures only | `tests/test_brain_and_server.py`, `desktop/e2e/security.mjs` |
| The approval cards | A card shows exactly what will be sent, byte for byte. Pyxi's hands refuse Pyxi's windows; answers are ignored while Pyxi's own input moves; buttons take only trusted events | `tests/test_exact_cards.py`, `tests/test_own_cards.py`, e2e |
| Content the models read (mail, pages, documents, code, the screen) | The rule plus fencing in every prompt. Cards flag recipients, links and amounts from content. After reading, everything asks fresh (no "always"). Addresses from nowhere are refused | `tests/test_prompt_injection.py`, `tests/test_fresh_cards.py`, `scripts/injection_scenarios.py` |
| Outward actions (send, post, invite, book) | A card for each, and hourly limits that stop and ask. Stop everything. The activity log | `tests/test_limits.py`, `tests/test_stop_everything.py`, `tests/test_audit.py` |
| The mouse and keyboard | A card first; "always" expires after 30 days and is revocable; Esc or moving the mouse stops it; never in password fields or Pyxi's own windows | `tests/test_grants.py`, `tests/test_operator.py` |
| Screen capture | Private apps (password managers, banking, sign-in, UAC, lock screen, the user's own list) are skipped in front and blacked out elsewhere; secrets are blacked out of pixels and text | `tests/test_private_apps.py`, `tests/test_secrets.py` |
| Electron windows | Sandbox, context isolation, no Node, a strict CSP, no popups or navigation, links limited to http and https, no running files | `desktop/e2e/security.mjs` |
| Scout's browser | Its own profile in Pyxi's data folder (never the user's), no CDP attach, Otto's guardrails (confirm sensitive clicks, no password or card fields) | `tests/test_scout_profile.py`, Otto's tests |
| Stored secrets | DPAPI (CurrentUser) for OAuth and beta tokens; a corrupt file means sign in again | `tests/test_secretstore.py` |
| Memory and logs | No secrets in memory (Pyxi's or the teammates'); "Forget this" and "Wipe everything"; logs scrubbed of emails, phones, names in paths and tokens | `tests/test_memory_hygiene.py`, `tests/test_logscrub.py` |
| The beta server | Hashed tokens; per-tester usage only; revoke a device, a code or everyone; a join rate limit; daily and monthly caps; stores only counts and costs | `cloud/test/beta.test.ts` |
| Dependencies and releases | A hash-pinned installer lock; a dependency audit and a key scan in the release script; Dependabot | `tests/test_installer_lock.py`, `tests/test_security_check.py` |

## Risks that remain

In rough order of how much they worry us:

1. **Unsigned installer and updates.** Until code-signing (`docs/LAUNCH-SECURITY.md`), Windows can't vouch for the publisher. And electron-updater on an unsigned app trusts GitHub plus HTTPS alone. A stolen GitHub account would mean a malicious update for everyone.
2. **Scout following a page elsewhere.** Navigation isn't gated, so an injected page could send Scout to another site with task details in the address. It's told not to, and it asks before sensitive clicks, but it doesn't ask before simply opening a page.
3. **`quick_action` opens links** a talked-into model chooses, without a card. They're http and https only, and nothing is sent, but it could open a phishing page.
4. **Malware running as the user** can read Pyxi's data folder (DPAPI doesn't stop the same user), and could drive the real mouse onto a card. Real input from malware is indistinguishable from the user's.
5. **Model providers see what's sent.** Prompts, screenshots and the parts of mail and files a job needs go to OpenRouter and the model provider. That's limited by their policies, and by turning off logging on the account.
6. **Prompt-injection resistance is statistical.** The models usually refuse (7/7 in the scenarios), but the cards are the real guarantee. A user who clicks Allow without reading can still be fooled; the cards' warnings are the last line.
7. **The Google OAuth client secret ships in the installer** (normal for desktop apps; Google treats it as not secret). Its abuse would show up as quota on the project.

## When to update this

- A new teammate or tool that can send, write, click or buy.
- A new place Pyxi reads from.
- A new server or service.
- Any change to the cards, the brain's API or the update channel.
