# Security work left before the public launch

*For the founder. Written 7 November 2026, after the beta hardening pass (local brain key, DPAPI tokens, Electron hardening, dependency audit, prompt-injection red team, beta server revocation and rate limit). Prices and rules below were checked on that date; confirm them before you pay for anything.*

The code side is in good shape for the beta. What's left needs your accounts, your money or your signature. Roughly in order:

## Before inviting more testers (this week)

1. **Deploy the beta server changes** (`cloud/`, commit "Beta server: revocation, a join rate limit, key rotation docs"). They aren't deployed: deploying needs your OK. Before you deploy:
   - Set `ADMIN_TOKEN` in Vercel to a long random secret, so you can revoke a device or a code at once. Keep it as safe as the OpenRouter key.
   - Then run `npx vercel --prod` from `cloud/`.
2. **Use a dedicated OpenRouter key for the beta**, with a credit limit a little above `MONTHLY_CAP_USD`, and auto top-up off. Then rotate it, as `cloud/README.md` describes. If the current key has ever been used anywhere else, rotate it now.
3. **Turn on GitHub's private vulnerability reporting** for the Pyxi repository: Settings, then Security, then Private vulnerability reporting. `SECURITY.md` points reporters there. Fill in the `[security contact email]` placeholder too, for example `security@` on your domain.
4. **Release the hardened app** (the next version after 0.1.6) with `scripts/release.py`. It now also runs `scripts/security_check.py`, which needs internet access for pip-audit and npm audit.

## 1. Code-signing the installer

Today Windows SmartScreen shows "Windows protected your PC: unknown publisher" for `Pyxi-Setup.exe`, and the release notes tell testers to choose "Run anyway". That's fine for a beta and not for a launch. It also trains people to click through a warning that exists to protect them.

**Recommended: Azure Artifact Signing** (it was called Trusted Signing until recently).

- It's open to **individual developers in Canada** (and the US), and to organisations in more countries. The old "3 years of business history" rule no longer applies.
- **Basic: US$9.99 a month** for up to 5,000 signatures, about US$120 a year. That's far below a classic EV certificate (US$300 to 900 a year), and there's no USB token to keep.
- What it needs:
  - a paid Azure subscription (not a free or trial one);
  - for an individual, an Azure billing account of type Individual;
  - identity verification, which takes a few business days. Billing starts when you create the account, even if verification fails.
- Its certificates are short-lived (a few days), and timestamping keeps signed files valid afterwards. SmartScreen reputation still builds up over the first downloads, so expect the warning to fade over a few weeks rather than vanish on day one.
- To wire it in:
  - electron-builder supports Azure signing through `win.azureSignOptions` in `desktop/package.json`.
  - Today `signAndEditExecutable` is `false`; set it to `true`.
  - Sign `Pyxi-Setup.exe`, `Pyxi.exe` and the bundled `python.exe`.
  - Give the signing step an Azure service principal, kept out of the repo. `desktop/build-secrets` is already ignored by git.
- Updates: electron-updater checks the publisher of a new version against the installed one, so the first signed release should be one that every beta tester installs. After that, keep the same publisher name.

**Alternative: an OV certificate** from a certificate authority such as Sectigo, DigiCert or SSL.com, if Azure verification doesn't work out. Since 2023 its private key must live on a hardware token or a cloud HSM, so signing happens on one machine or through the CA's cloud service. An OV certificate no longer gets SmartScreen reputation instantly either (EV doesn't either, since 2024), so its main advantage over Azure is not needing an Azure account.

## 2. Google's OAuth verification and the CASA assessment

Pyxi asks Google for `gmail.modify`, which Google lists as a **restricted** scope (so is `drive.metadata.readonly`). Calendar, Docs, Sheets and `drive.file` are **sensitive** scopes. Today the Google Cloud project is unverified, which means:

- users see Google's "Google hasn't verified this app" screen;
- an unverified app with sensitive or restricted scopes stops at **100 users in total**, for the life of the project. After that, Google sign-in stops working for new users;
- while the app is in **Testing** mode, only the test users you list can sign in, and their sign-in **expires after 7 days**, so beta testers will have to press Connect again each week.

To launch past 100 users:

1. **Brand verification and sensitive-scope verification:**
   - a verified domain, with the privacy policy (`docs/PRIVACY.md`, already written with Google's Limited Use wording) and the terms on it;
   - an app homepage;
   - a demo video showing each scope in use, and why it's needed.

   This takes days to a few weeks.
2. **Restricted-scope verification, plus a CASA security assessment.** Google requires a yearly Cloud Application Security Assessment for apps that handle restricted data off the user's device. Pyxi qualifies, because parts of emails go to the AI model through OpenRouter and the beta server.
   - The assessment is done by a lab authorised by the App Defense Alliance; TAC Security is one.
   - Google tells you the tier during review. For apps like Pyxi it's usually **Tier 2**: you scan the app and the lab validates the results.
   - Third-party estimates put Tier 2 at roughly **US$500 to 1,000 a year** in lab fees, and the whole process at **one to three months**. Get quotes.
   - Have ready:
     - how tokens are stored (DPAPI-encrypted, on the user's PC only);
     - that the beta server keeps no Google data;
     - how a user deletes their data (Disconnect deletes the token; uninstalling and deleting `%APPDATA%\Pyxi` removes everything);
     - this repository's security tests.
3. **Ways to shrink the work**, worth weighing before you start:
   - Could Pyxi do without `gmail.modify`? Search and read need `gmail.readonly`, which is also restricted. `gmail.send` on its own is only sensitive.
   - Replacing `drive.metadata.readonly` with `drive.file` plus Google's file picker would leave Gmail as the only restricted scope.
   - Mail stays restricted either way, so plan for the assessment if the Gmail features are part of the launch.

Until verification is done, keep the beta to under 100 Google users, and tell testers about the weekly reconnect.

## 3. An external penetration test before the public launch

This pass was done from inside: the code was hardened and then tested by the people who wrote it. Before the public launch, pay an independent tester to try to break it. In scope:

- **The local brain:** reaching it from another program, another Windows user or a web page; replaying or stealing the launch key; answering an Allow card from outside.
- **The Electron app:**
  - escaping the renderer sandbox;
  - IPC misuse through the preload;
  - opening files or links;
  - the update channel: could a fake update be installed? This matters even more once releases are signed.
- **Prompt injection, with the real models:**
  - mail, web pages, documents and the screen;
  - getting Scout to leak data by navigating to another site;
  - getting the chat to start errands or routines nobody asked for.

  `scripts/injection_scenarios.py` is a starting point.
- **The beta server:**
  - token guessing and revocation;
  - the rate limit, including spoofed headers;
  - spending beyond the allowance;
  - reading another tester's data;
  - abusing the listening model as a free model.
- **Data at rest:** what's left in `%APPDATA%\Pyxi` and in logs.

A focused test of this size is typically one to two weeks of a tester's time. Ask for a retest of the fixes in the quote. Share `SECURITY.md` and this file with the tester.

## Known gaps worth fixing (smaller)

- **Scout can open any page a website points it to.** A page could steer it to another site, with what it knows in the address. Scout is told to ignore page instructions, and Otto's own tests cover the classic version. A stricter fix: ask before Scout leaves the site it was sent to, unless the task names the new one.
- **`quick_action` opens links without a card.** Opening a link isn't sending, but a page or document that talks the chat model into opening a phishing link would work. A fix: open only links the user said, or that came from Pyxi's own lookups, and put anything else on a card.
- **"Always allow" for the mouse and keyboard** skips the first card for every later task, including one an injection talks the model into. Sends, payments, bookings, deletes and posts still ask. A fix: make "always" expire, for example after a day.
- **The build's own pip is old** (25.0.1 in `desktop/build/python-full`, with 5 moderate advisories and 1 low). It never ships, but it installs every package at build time. Upgrade it in `scripts/build_installer.py` (`python -m pip install --upgrade pip` after `ensurepip`), and run the next build with `--full`.
- **electron-builder** has 6 moderate advisories (minimatch). It's a build tool and doesn't ship; update it when a fixed major version is out.
