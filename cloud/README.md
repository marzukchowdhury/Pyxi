# Pyxi beta server

A tiny Vercel project that lets beta users run Pyxi without an API key. It holds the founder's OpenRouter key, and:

- swaps invite codes for tokens;
- passes each user's model calls to OpenRouter;
- keeps everyone inside a daily allowance and the whole beta inside a monthly cap.

It stores no prompts or screen content. It keeps only token hashes and spend totals.

| Route | What it does |
|---|---|
| `POST /api/join` `{"code": "..."}` | Invite code → `{"token": "pyxi_..."}`. Each code works on `USES_PER_CODE` devices |
| `POST /api/v1/chat/completions` | OpenAI-style chat, forwarded to OpenRouter with the beta's limits. The cost is OpenRouter's own figure |
| `GET /api/me` | Today's spend and what's left, for the caller only, plus their short device id (`user`) |
| `POST /api/admin/revoke` | Founder only (`ADMIN_TOKEN`): stop a device or an invite code at once. See [Revoking access](#revoking-access) |

A tester only ever sees their own usage: every count is keyed by a hash of the caller's own token, and no route takes someone else's id. `/api/join` allows `JOIN_PER_IP_PER_HOUR` tries per IP address an hour (default 10), so invite codes can't be guessed; only a hash of the address is kept, for that hour.

The same chat route also accepts the listening model (`EARS_MODELS`, default `google/gemini-3.1-flash-lite`) for Bengali speech to text, but only as a transcription: one message with one audio clip (about 80 seconds at most), no tools, images or streaming, and a short answer. It costs about $0.001 per minute of speech and counts against the same daily allowance.

## Deploy (about 10 minutes)

1. **Create the project:**

   ```bash
   cd cloud && npx vercel link
   ```

2. **Add storage:** in the Vercel dashboard, open the project, then Storage, then Marketplace, then **Upstash for Redis** (free tier). Connecting it sets `KV_REST_API_URL` and `KV_REST_API_TOKEN`.

3. **Set the secrets.** Paste each value when asked:

   ```bash
   npx vercel env add OPENROUTER_API_KEY production
   ```

   ```bash
   npx vercel env add INVITE_CODES production
   ```

   For `INVITE_CODES`, use comma-separated codes, for example `FIREFLY-ANNA,FIREFLY-BEN`.

4. **Optional limits** (the defaults are shown):
   - `DAILY_LIMIT_USD=0.25`: per user, per UTC day.
   - `MONTHLY_CAP_USD=20`: the whole beta.
   - `USES_PER_CODE=3`: devices per code.
   - `MAX_TOKENS=16000`: the longest reply allowed (a detailed 3D model asks for 12000).
   - `ALLOWED_MODELS`: comma-separated; defaults to Claude Haiku 5.5 and Sonnet 5.5.
   - `EARS_MODELS`: models allowed only to transcribe speech; defaults to Gemini 3.1 Flash-Lite. Set it empty to turn Bengali listening off.
   - `MAX_AUDIO_CHARS=3500000`: the largest audio clip, in base64 characters (about 80 seconds).
   - `JOIN_PER_IP_PER_HOUR=10`: join tries per IP address per hour.
   - `REVOKED_CODES`: comma-separated invite codes that no longer work, nor do their devices' tokens.
   - `ADMIN_TOKEN`: a long random secret that turns on `POST /api/admin/revoke`. Leave it unset and the route doesn't exist.

5. **Deploy:**

   ```bash
   npx vercel --prod
   ```

6. **Point Pyxi at it:** if the URL isn't `https://pyxi-beta.vercel.app`, change `DEFAULT_URL` in `src/pyxi/cloud.py` (or set `PYXI_CLOUD_URL`) and rebuild the installer.

## The OpenRouter key

Use a **dedicated key for the beta**, not the one you use for anything else, and give it a **credit limit** in OpenRouter's dashboard (Keys, then the key, then Credit limit), a little above `MONTHLY_CAP_USD`. The limit is the hard ceiling if anything here goes wrong: a bug, a leaked token, or a stolen Vercel environment. Keep the account's own balance low too, and turn off auto top-up during the beta.

### Rotating it

Do this every few months, straight away if the key may have leaked (it showed up in a log, a screenshot or a commit), and when anyone who had Vercel access leaves. Testers notice nothing: their Pyxi tokens are separate from the key.

1. In OpenRouter, create a new key with the same credit limit. Name it after the date, for example `pyxi-beta-2026-11`.
2. Replace the key in Vercel. Paste the new key when asked:

   ```bash
   npx vercel env rm OPENROUTER_API_KEY production
   ```

   ```bash
   npx vercel env add OPENROUTER_API_KEY production
   ```

3. Redeploy, so the running functions pick it up:

   ```bash
   npx vercel --prod
   ```

4. Check a model call works (ask Pyxi something while in the beta), then **delete the old key** in OpenRouter. Until you delete it, the old key still works.
5. Look at the old key's activity in OpenRouter for anything you don't recognise.

## Revoking access

Every device has a short id: Pyxi's `GET /api/me` shows it as `user`, and OpenRouter's activity page shows the same value as `X-User`. To stop a device at once:

```bash
curl -X POST https://pyxi-beta.vercel.app/api/admin/revoke -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"user": "0123456789abcdef"}'
```

Send `{"code": "FIREFLY-ANNA"}` instead to stop an invite code and every device that joined with it (a code that was shared publicly, for example). Add `"restore": true` to undo either one. Without an admin token, put the code in `REVOKED_CODES` and redeploy. To stop everyone at once, set `MONTHLY_CAP_USD=0`, or delete the OpenRouter key.

Keep `ADMIN_TOKEN` as safe as the OpenRouter key: it can't spend money, but it can lock testers out.

## Test

```bash
npm install && npm test
```

To run the server locally, using the key in `../.env` and the invite code `LOCALTEST`:

```bash
node dev-server.ts
```

Then start a key-less Pyxi with `PYXI_CLOUD_URL=http://127.0.0.1:8790`.
