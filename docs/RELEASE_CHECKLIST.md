# Pre-release security checklist

`scripts/release.py` runs the **automated** part and stops if anything fails. It prints the **by hand** part before it publishes. `python scripts/release.py --checklist` prints this list without releasing anything.

## Automated (release.py runs these)

- The Python tests, `pytest`. These include the prompt-injection red team, the card, limit and stop tests, and the launch-key tests.
- The desktop typecheck and unit tests (`npm run typecheck`, `npm test` in `desktop/`).
- The beta server's tests (`npm test` in `cloud/`).
- The key scan of everything that ships: no keys, tokens, invite codes or personal files in `app.asar`, the bundled Python or the packs.
- The dependency audit, `scripts/security_check.py`: nothing high or critical in what ships.
- The installer's Python is installed from the hash-pinned lock, `requirements/installer.txt` (`build_installer.py` stops if a dependency isn't locked).

## By hand

- [ ] Run the real app's security check: `cd desktop && npm run build && node e2e/security.mjs` (all checks pass).
- [ ] If prompts, tools or models changed: `python scripts/injection_scenarios.py` (a few cents), and read any FAIL.
- [ ] Install the new build on a clean Windows account. Check:
  - the brain starts and the notch connects;
  - an Allow card works;
  - Stop everything (Ctrl+Alt+Pause) stops a running job.
- [ ] New ways to send, write, click or buy since the last release each have a card, an hourly limit, an activity-log entry and a test, and appear in `docs/THREAT_MODEL.md`.
- [ ] New places Pyxi reads from are fenced as untrusted (`pyxi/safety.py`) and counted for fresh cards.
- [ ] Any new data Pyxi stores or sends is listed in `docs/PRIVACY.md` and `SECURITY.md`.
- [ ] Beta server changes are deployed, and `ADMIN_TOKEN` is set. The OpenRouter key has its credit limit, and logging and training are off.
- [ ] Release notes say plainly if anything changes for testers (for example "connect Google again").
- [ ] After publishing: install from the website's download button, and check the update reached an older install.
