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
| `GET /api/me` | Today's spend and what's left |

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
   - `ALLOWED_MODELS`: comma-separated; defaults to Claude Haiku 5.5 and Sonnet 5.5.

5. **Deploy:**

   ```bash
   npx vercel --prod
   ```

6. **Point Pyxi at it:** if the URL isn't `https://pyxi-beta.vercel.app`, change `DEFAULT_URL` in `src/pyxi/cloud.py` (or set `PYXI_CLOUD_URL`) and rebuild the installer.

Also set a **credit limit on the OpenRouter key** in OpenRouter's dashboard. It's the hard ceiling if anything here goes wrong.

## Test

```bash
npm install && npm test
```

To run the server locally, using the key in `../.env` and the invite code `LOCALTEST`:

```bash
node dev-server.ts
```

Then start a key-less Pyxi with `PYXI_CLOUD_URL=http://127.0.0.1:8790`.
