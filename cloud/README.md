# Pyxi beta server

A tiny Vercel project that lets people run Pyxi without an API key. It holds the founder's OpenRouter key, and:

- swaps invite codes for tokens (the beta testers);
- signs people in with Google, gives each account a 3-day trial of Pro with no card, then a paid plan through Stripe;
- passes each user's model calls to OpenRouter;
- keeps every tester inside a daily allowance, every account inside its plan's monthly allowance, and the whole service inside a monthly cap.

It stores no prompts, replies, screen content or voice, no card details and no Google sign-in tokens, and writes nothing to the function logs. Requests and replies pass straight through to OpenRouter. Cards are typed on Stripe's own pages and never reach this server. All it keeps (in Upstash) is:

| Key | What | How long |
|---|---|---|
| `tok:<sha256 of the token>` | for a tester's device: the invite code used and when it joined. For an account's device: which account, and when it signed in | until signed out or revoked |
| `code:<CODE>` | how many devices used that code | as long as the code exists |
| `acct:<Google account id>` | the account: Google's id for it, the email address, when it was made, when its trial ends, plan, status, Stripe's customer and subscription ids, when the paid period ends, whether it is set to cancel, when a payment first failed, and whether the founder switched it off or erased it | until erased; after that only the id, the dates and the trial end stay, so signing in again can't start a second trial |
| `email:<sha256 of the email>` | which account has that email, so the founder can look an account up | until erased |
| `cus:<Stripe customer id>` | which account that Stripe customer is | until erased |
| `spend:<token hash>:<day>` | that device's cost that day (OpenRouter's own figure) | 3 days |
| `spend:trial:<Google account id>` | what the account spent over its trial | with the account |
| `spend:acct:<Google account id>:<month>` | what the account spent that month | about 40 days |
| `spend:all:<month>` | the whole service's cost that month | about 40 days |
| `evt:<Stripe event id>` | that this Stripe event was already handled (nothing from inside the event) | 7 days |
| `rl:join:<hash of the IP address>:<hour>`, `rl:auth:...`, `rl:bill:...` | join, sign-in and payment-page tries from one address in that hour | 1 hour |
| `revoked:<device id>`, `revoked-code:<CODE>`, `revoked-before` | what the founder revoked | until restored |

`test/beta.test.ts` checks this twice: once for a tester (a join and two chats) and once for an account (sign-in on two devices, chats, checkout, every Stripe event with card details inside it, the portal, an admin lookup, sign-out). Each time the store holds only these keys, the account record has only the fields listed, and there is no prompt, reply, token, Google token, name, card detail, secret or IP address anywhere in it. Vercel and Upstash keep their own standard request logs (time, path, status, IP), under their own policies. On OpenRouter, turn off prompt logging and "use my data for training" for the key's account.

| Route | What it does |
|---|---|
| `GET /api/config` | `{"sign_in", "google_client_id", "google_web_client_id", "invite_codes"}`: what the app and the website's account page should offer. `sign_in` is true once `GOOGLE_SIGNIN_CLIENT_ID` is set |
| `POST /api/join` `{"code": "..."}` | Invite code → `{"token": "pyxi_..."}`. Each code works on `USES_PER_CODE` devices |
| `POST /api/auth/google` `{"id_token": "..."}` | A Google sign-in → `{"token": "pyxi_...", "account": {...}}`. The first one makes the account and starts the trial. 401 `bad_sign_in` |
| `POST /api/auth/signout` | This device's token stops working. Other devices carry on |
| `GET /api/me` | Today's spend and what's left, for the caller only, their short device id (`user`), and `account` (null for a tester's device) |
| `POST /api/billing/checkout` `{"plan": "starter"/"pro"/"max", "yearly": bool}` | `{"url"}` of a Stripe Checkout page for the caller's account. 503 `billing_off` until Stripe is set up |
| `POST /api/billing/portal` | `{"url"}` of Stripe's own page: change plan or card, cancel, receipts. 409 `no_subscription` |
| `POST /api/billing/webhook` | Stripe's events, signature checked → `{"received": true}` |
| `POST /api/v1/chat/completions` | OpenAI-style chat, forwarded to OpenRouter within the limits. The cost is OpenRouter's own figure. For an account: 402 `trial_ended`, `subscription_ended`, `payment_failed`; 429 `allowance_used` |
| `POST /api/admin/revoke` | Founder only (`ADMIN_TOKEN`): stop a device or an invite code at once. See [Revoking access](#revoking-access) |
| `POST /api/admin/account` | Founder only: look an account up by email, extend its trial, switch it off, erase it. See [Looking after accounts](#looking-after-accounts) |

Errors from the account and billing routes are always `{"error": {"code": "...", "message": "plain words for the user"}}`.

Nobody ever sees someone else's usage or account: every count is keyed by a hash of the caller's own token, the account comes from that token's own record, and no route (other than the founder's) takes an id or an email. `/api/join` allows `JOIN_PER_IP_PER_HOUR` tries per IP address an hour (default 10), so invite codes can't be guessed; sign-in allows `AUTH_PER_IP_PER_HOUR` (default 20) and the two payment-page routes together `BILLING_PER_IP_PER_HOUR` (default 30). Only a hash of the address is kept, for that hour.

How accounts work:

- One account per Google account, on any number of devices, each device with its own token.
- The trial is 3 days of Pro from the first sign-in, with `TRIAL_LIMIT_USD` of model cost for the whole trial. It never starts again: not by signing out and in, not from a second device, and not after the founder erases the account.
- A paid plan has a monthly allowance (`STARTER_LIMIT_USD`, `PRO_LIMIT_USD`, `MAX_LIMIT_USD`) that refills on the 1st of each month (UTC).
- The plan an account is on only ever comes from Stripe. On every Stripe event the server asks Stripe what the subscription is now and writes that down, so events in the wrong order or sent twice do no harm, and nothing in a request can raise a plan.
- If a payment fails, Pyxi keeps working for `GRACE_DAYS` (default 3), then chat answers `payment_failed` until the card is fixed.
- If Stripe's news of a renewal never arrives, the plan stops `GRACE_DAYS` after the paid period ran out, rather than running free.

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
   - `ADMIN_TOKEN`: a long random secret that turns on `POST /api/admin/revoke` and `POST /api/admin/account`. Leave it unset and those routes don't exist.
   - `TRIAL_LIMIT_USD=1.50`: model cost for a whole trial.
   - `STARTER_LIMIT_USD=4`, `PRO_LIMIT_USD=9`, `MAX_LIMIT_USD=20`: model cost per account per calendar month, by plan.
   - `GRACE_DAYS=3`: how long Pyxi keeps working after a failed payment.
   - `AUTH_PER_IP_PER_HOUR=20`, `BILLING_PER_IP_PER_HOUR=30`: sign-in and payment-page tries per IP address per hour.
   - `SITE_URL=https://www.heypyxi.com`: where Stripe sends people back to. It must be an `https` address.

   Sign-in and plans need more settings: see [Going on sale](#going-on-sale).

5. **Deploy:**

   ```bash
   npx vercel --prod
   ```

6. **Point Pyxi at it:** if the URL isn't `https://api.heypyxi.com`, change `DEFAULT_URL` in `src/pyxi/cloud.py` (or set `PYXI_CLOUD_URL`) and rebuild the installer.

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
curl -X POST https://api.heypyxi.com/api/admin/revoke -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"user": "0123456789abcdef"}'
```

Send `{"code": "FIREFLY-ANNA"}` instead to stop an invite code and every device that joined with it (a code that was shared publicly, for example). Add `"restore": true` to undo either one. Without an admin token, put the code in `REVOKED_CODES` and redeploy. To stop everyone at once, set `MONTHLY_CAP_USD=0`, or delete the OpenRouter key.

Keep `ADMIN_TOKEN` as safe as the OpenRouter key: it can't spend money, but it can lock people out, and it can read an account's email and plan.

## Looking after accounts

With `ADMIN_TOKEN` set in your terminal (the same secret as in Vercel), from the `cloud` folder:

```bash
node admin.ts account anna@example.com
```

That shows the account: plan, status, days of trial left, what it has spent in its trial and this month, and its Stripe customer id (paste that into Stripe's search to see payments).

```bash
node admin.ts extend-trial anna@example.com 7
```

Seven more days of trial, counted from now if the trial was already over, with a fresh trial allowance. Someone with a running plan keeps the plan.

```bash
node admin.ts revoke-account anna@example.com
```

Switches the account off on every device at once; signing in again doesn't get round it. `restore-account` switches it back on. This does not stop Stripe charging: cancel the subscription in Stripe as well if you mean to.

```bash
node admin.ts erase-account anna@example.com
```

For someone who asks for their details to be removed. It takes away the email address and the Stripe ids and signs every device out. It refuses while a plan is running: cancel it in Stripe first. The account's id and trial dates stay, so the same Google account can't get a second trial. Delete the customer in Stripe too if they want that gone.

## Going on sale

Everything here is done by hand, once. Do the whole thing in Stripe's **test mode** first (the "Test mode" switch in the Stripe dashboard), try it, then repeat steps 1 to 4 in live mode. Until step 4 is done, sign-in and the trial work and choosing a plan says "Plans aren't open yet."

### 1. Google sign-in

1. Open the Google Cloud console, the project Pyxi already uses, then **APIs & Services**, then **Credentials**.
2. Find the OAuth client the desktop app uses for "Sign in with Google" (type "Desktop app"). Copy its **Client ID**. It ends in `.apps.googleusercontent.com`. Do not copy the client secret: the server doesn't want it.
3. In Vercel, open the project, then **Settings**, then **Environment Variables**, and add `GOOGLE_SIGNIN_CLIENT_ID` with that value.

The app and the server must use the same client ID: the server only accepts a sign-in that Google made out to that ID.

#### The website's account page (optional)

`https://www.heypyxi.com/account` lets someone see their plan, choose one or open Stripe's page without opening the app. It stays switched off (it says "choose your plan inside Pyxi") until the website has a Google client of its own:

1. In the same **Credentials** page, press **Create credentials**, then **OAuth client ID**, type **Web application**.
2. Under **Authorized JavaScript origins** add `https://www.heypyxi.com`. No redirect address is needed.
3. Copy its **Client ID** and add it in Vercel as `GOOGLE_WEB_CLIENT_ID`, then redeploy the server.

`POST /api/auth/web` takes only a sign-in made out to that ID. What it answers with lasts an hour, can read the account and open Stripe's pages, and never reaches a model. It never makes an account: the trial starts in the app.

### 2. Stripe products and the six prices

In Stripe, open **Product catalogue** and add three products. Each gets two prices, both "Recurring", in US dollars:

| Product | Monthly price | Yearly price |
|---|---|---|
| Pyxi Starter | $9 every month | $90 every year |
| Pyxi Pro | $19 every month | $180 every year |
| Pyxi Max | $39 every month | $372 every year |

Open each price and copy its id. A price id starts with `price_`. You will have six.

### 3. Stripe's customer page (the portal)

In Stripe, open **Settings**, then **Billing**, then **Customer portal**, and switch on:

- customers can update their payment method;
- customers can see their invoices;
- customers can cancel (choose "at the end of the billing period");
- customers can switch plans: add the three products with all six prices.

Save. This is the page Pyxi opens when someone wants to change or cancel their plan.

### 4. Tell the server

In Stripe, open **Developers**, then **Webhooks**, then **Add endpoint**:

- Endpoint URL: your server's address followed by `/api/billing/webhook`, for example `https://api.heypyxi.com/api/billing/webhook`.
- Events to send. Tick exactly these six:
  - `checkout.session.completed`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.paid`
  - `invoice.payment_failed`

After saving, open the endpoint and reveal its **Signing secret** (it starts with `whsec_`).

Then in Vercel, **Settings**, **Environment Variables**, add these. The names must be exact. Paste the values straight from Stripe into Vercel and nowhere else: not a chat, not an email, not a file in this repo.

| Name | What goes in it |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe, **Developers**, **API keys**, the secret key (`sk_test_...` in test mode, `sk_live_...` when live) |
| `STRIPE_WEBHOOK_SECRET` | the endpoint's signing secret from just above |
| `STRIPE_PRICE_STARTER` | the Starter monthly price id |
| `STRIPE_PRICE_STARTER_YEARLY` | the Starter yearly price id |
| `STRIPE_PRICE_PRO` | the Pro monthly price id |
| `STRIPE_PRICE_PRO_YEARLY` | the Pro yearly price id |
| `STRIPE_PRICE_MAX` | the Max monthly price id |
| `STRIPE_PRICE_MAX_YEARLY` | the Max yearly price id |
| `SITE_URL` | only if the site isn't `https://www.heypyxi.com` |

Then redeploy so the server picks them up:

```bash
npx vercel --prod
```

### 5. Try it in test mode

Use a Google account of your own that has never signed in to Pyxi.

1. Start Pyxi and sign in with Google. It should say you have a 3-day trial. Ask it something: it answers.
2. Choose Pro, monthly. Stripe's page opens. Pay with Stripe's test card: number `4242 4242 4242 4242`, any date in the future, any three digits, any name and postcode. No real money moves in test mode.
3. Back in Pyxi, within a few seconds your plan shows as Pro. In Stripe, **Developers**, **Webhooks**, your endpoint: every delivery should show `200`. A `400` there means `STRIPE_WEBHOOK_SECRET` is not this endpoint's secret. A `500` with `unknown_price` means one of the six price names holds the wrong id.
4. In Pyxi, open your plan to manage it. Stripe's page opens. Switch to Max: Pyxi shows Max. Cancel: Pyxi still works, and shows the day the plan ends.
5. In Stripe, open **Customers**, your test customer, the subscription, and cancel it immediately. Pyxi now says "Your Pyxi plan has ended. Choose a plan to carry on."
6. Try a card that is refused, `4000 0000 0000 0002`: Stripe's page says the card was declined, and the account stays as it was.
7. Check `node admin.ts account you@example.com` (with your own address) shows what you expect.

### 6. Go live

1. Switch Stripe out of test mode. Make the three products and six prices again (or use "Copy to live mode" on each product), set up the customer portal again, and add the webhook endpoint again: live mode has its own prices, its own secret key and its own signing secret.
2. In Vercel, replace the eight Stripe values with the live ones, then redeploy.
3. Make one real purchase yourself with a real card, check it arrives in Stripe, then refund it from Stripe.
4. When the invite-code beta is over, empty `INVITE_CODES` and redeploy. From then on invite-code devices stop working and are asked to sign in, and the app offers only Google sign-in. Tell the testers first.

If the Stripe secret key or the signing secret may have leaked, roll it in Stripe (**Developers**, **API keys** or the webhook endpoint, "Roll"), put the new value in Vercel, and redeploy. People who have paid notice nothing.

## Test

```bash
npm install && npm test
```

To run the server locally, using the key in `../.env` and the invite code `LOCALTEST`:

```bash
node dev-server.ts
```

Then start a key-less Pyxi with `PYXI_CLOUD_URL=http://127.0.0.1:8790`.
