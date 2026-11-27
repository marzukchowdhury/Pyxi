// Accounts: one per Google account, any number of devices, a 3-day trial that starts at the
// first sign-in and never starts again, then a paid plan with a monthly allowance.
// What is kept per account is listed in cloud/README.md; test/accounts.test.ts proves the list.

import type { Config, PaidPlan } from "./beta.js";
import { type KeySource, verifyGoogleIdToken } from "./google.js";
import type { Store } from "./store.js";
import { bearer, fail, json, overLimit, randomToken, sameSecret, sha256, tooMany } from "./util.js";

export const TRIAL_DAYS = 3;
const DAY_MS = 86_400_000;
const MONTH_TTL = 40 * 86400;

/** The sentences a person reads, exactly as the desktop app and the website expect them. */
export const WORDS = {
  trial_ended: "Your 3-day trial has ended. Choose a plan to keep going.",
  subscription_ended: "Your Pyxi plan has ended. Choose a plan to carry on.",
  payment_failed: "Your last payment didn't go through. Update your card to keep Pyxi working.",
  allowance_used: (date: string) =>
    `You've used this month's allowance on your plan. It resets on ${date}, or you can move up a plan.`,
  trial_allowance_used: "You've used your trial's allowance. Choose a plan to keep going.",
  billing_off: "Plans aren't open yet. Your trial keeps working for now.",
  bad_sign_in: "That Google sign-in didn't work. Try again.",
  sign_in_off: "Signing in with Google isn't open yet.",
  account_closed: "This Pyxi account has been switched off.",
  no_account: "There's no Pyxi account for this Google address yet. Download Pyxi and sign in there to start your free trial.",
  not_signed_in: "Sign in to Pyxi first.",
} as const;

/** Everything the server keeps about an account (the stored JSON has exactly these fields). */
export interface AccountRecord {
  /** Google's id for the account. */
  sub: string;
  /** "" once the founder has erased the account. */
  email: string;
  created: number;
  /** When the trial ends (ms). Set once, at the first sign-in; only the founder can move it. */
  trial_ends: number;
  plan: "trial" | PaidPlan | "none";
  status: "trial" | "active" | "past_due" | "ended";
  /** Stripe's ids, "" until there is one. Never card details: those stay with Stripe. */
  customer: string;
  subscription: string;
  /** When the paid period ends or renews (ms), 0 if never paid. */
  period_end: number;
  /** Paid, but set to end at period_end. */
  cancels: boolean;
  /** When a payment first failed (ms), 0 if none: the grace period counts from here. */
  past_due_since: number;
  /** When the founder switched the account off (ms), 0 if not. */
  revoked: number;
  /** When the founder erased the account's details (ms), 0 if not. The record stays so that
   * signing in again can't start a second trial. */
  erased: number;
}

export interface AccountView {
  email: string;
  plan: "trial" | PaidPlan | "none";
  status: "trial" | "active" | "past_due" | "ended";
  trial_ends_at: string | null;
  days_left: number | null;
  renews_at: string | null;
  cancels: boolean;
  allowance: { used_usd: number; limit_usd: number; resets_at: string | null };
}

export async function accountOf(store: Store, sub: string): Promise<AccountRecord | null> {
  const raw = await store.get(`acct:${sub}`);
  return raw ? (JSON.parse(raw) as AccountRecord) : null;
}

export async function saveAccount(store: Store, acct: AccountRecord): Promise<void> {
  await store.set(`acct:${acct.sub}`, JSON.stringify(acct));
}

const emailKey = async (email: string) => `email:${await sha256(email.trim().toLowerCase())}`;

type Stop = "" | "trial_ended" | "subscription_ended" | "payment_failed";

/** Where an account stands right now: what it may use, and why not if it may not. */
export function standing(acct: AccountRecord, cfg: Config, now = Date.now()): { plan: AccountView["plan"]; status: AccountView["status"]; stop: Stop } {
  const grace = cfg.graceDays * DAY_MS;
  if (acct.status === "trial") {
    return now < acct.trial_ends ? { plan: "trial", status: "trial", stop: "" } : { plan: "none", status: "ended", stop: "trial_ended" };
  }
  if (acct.status === "active" && acct.plan !== "none" && acct.plan !== "trial") {
    // If Stripe's news of a renewal never arrived, the plan stops a grace period after the paid
    // time ran out, rather than running free for ever.
    if (acct.period_end && now > acct.period_end + grace) return { plan: "none", status: "ended", stop: "subscription_ended" };
    return { plan: acct.plan, status: "active", stop: "" };
  }
  if (acct.status === "past_due" && acct.plan !== "none" && acct.plan !== "trial") {
    const since = acct.past_due_since || acct.period_end || now;
    return { plan: acct.plan, status: "past_due", stop: now > since + grace ? "payment_failed" : "" };
  }
  return { plan: "none", status: "ended", stop: "subscription_ended" };
}

const monthOf = (now: number) => new Date(now).toISOString().slice(0, 7);

/** Where an account's model cost is counted: one total for the whole trial, then one a month. */
export function spendKey(acct: AccountRecord, now = Date.now()): { key: string; ttl?: number } {
  if (acct.status === "trial") return { key: `spend:trial:${acct.sub}` };
  return { key: `spend:acct:${acct.sub}:${monthOf(now)}`, ttl: MONTH_TTL };
}

function nextMonth(now: number): number {
  const d = new Date(now);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
}

async function allowance(store: Store, cfg: Config, acct: AccountRecord, now: number) {
  const { plan } = standing(acct, cfg, now);
  if (plan === "none") return { used_usd: 0, limit_usd: 0, resets_at: null as string | null, reset: 0 };
  const used = Number((await store.get(spendKey(acct, now).key)) ?? 0);
  const reset = plan === "trial" ? 0 : nextMonth(now);
  return { used_usd: used, limit_usd: cfg.limits[plan], resets_at: reset ? new Date(reset).toISOString() : null, reset };
}

/** USD the account may still spend right now (0 when it is stopped for any reason). */
export async function accountUsage(store: Store, cfg: Config, acct: AccountRecord, now = Date.now()): Promise<number> {
  if (standing(acct, cfg, now).stop) return 0;
  const a = await allowance(store, cfg, acct, now);
  return Math.max(0, a.limit_usd - a.used_usd);
}

/** The Account the contract describes, for the account's own devices. */
export async function accountView(store: Store, cfg: Config, acct: AccountRecord, now = Date.now()): Promise<AccountView> {
  const { plan, status } = standing(acct, cfg, now);
  const paid = status === "active" || status === "past_due";
  const neverPaid = !acct.subscription;
  const { used_usd, limit_usd, resets_at } = await allowance(store, cfg, acct, now);
  return {
    email: acct.email,
    plan,
    status,
    trial_ends_at: status === "trial" || (neverPaid && !paid) ? new Date(acct.trial_ends).toISOString() : null,
    days_left: status === "trial" ? Math.max(0, Math.ceil((acct.trial_ends - now) / DAY_MS) - 1) : null,
    renews_at: paid && acct.period_end ? new Date(acct.period_end).toISOString() : null,
    cancels: paid && acct.cancels,
    allowance: { used_usd, limit_usd, resets_at },
  };
}

/** Why chat is refused for this account right now, or null if it may go ahead. */
export async function accountGate(store: Store, cfg: Config, acct: AccountRecord, now = Date.now()): Promise<Response | null> {
  const { plan, stop } = standing(acct, cfg, now);
  if (stop) return fail(402, stop, WORDS[stop]);
  const a = await allowance(store, cfg, acct, now);
  if (a.used_usd < a.limit_usd) return null;
  if (plan === "trial") return fail(429, "allowance_used", WORDS.trial_allowance_used);
  const date = new Date(a.reset).toLocaleDateString("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });
  return fail(429, "allowance_used", WORDS.allowance_used(date));
}

/** GET /api/config: what the app should offer. No secrets: a Google client id is public. */
export function config(cfg: Config): Response {
  return json({
    sign_in: Boolean(cfg.googleClientId),
    google_client_id: cfg.googleClientId || null,
    google_web_client_id: (cfg.googleClientId && cfg.googleWebClientId) || null,
    invite_codes: cfg.inviteCodes.size > 0,
  });
}

/** POST /api/auth/google {"id_token"}: a checked Google sign-in becomes a token for this device.
 * The first sign-in makes the account and starts the trial; later ones, from any device, find
 * the same account with the same trial end. The Google token itself is never kept. */
export async function signIn(store: Store, cfg: Config, req: Request, keys: KeySource): Promise<Response> {
  if (!cfg.googleClientId) return fail(503, "sign_in_off", WORDS.sign_in_off);
  const wait = await overLimit(store, "auth", req, cfg.authPerIpPerHour);
  if (wait) return tooMany(wait);
  const body = (await req.json().catch(() => null)) as { id_token?: unknown } | null;
  const user = await verifyGoogleIdToken(body?.id_token, cfg.googleClientId, keys);
  if (!user) return fail(401, "bad_sign_in", WORDS.bad_sign_in);

  const now = Date.now();
  const fresh: AccountRecord = {
    sub: user.sub,
    email: user.email,
    created: now,
    trial_ends: now + TRIAL_DAYS * DAY_MS,
    plan: "trial",
    status: "trial",
    customer: "",
    subscription: "",
    period_end: 0,
    cancels: false,
    past_due_since: 0,
    revoked: 0,
    erased: 0,
  };
  // "add" only writes when there is no account yet, so two first sign-ins at once (two devices)
  // can't each start a trial: one wins, the other reads the winner's record.
  let acct = fresh;
  if (!(await store.add(`acct:${user.sub}`, JSON.stringify(fresh)))) {
    const existing = await accountOf(store, user.sub);
    if (!existing) return fail(401, "bad_sign_in", WORDS.bad_sign_in);
    acct = existing;
    if (acct.revoked) return fail(403, "account_closed", WORDS.account_closed);
    if (acct.erased || acct.email !== user.email) {
      // Back after being erased (the old trial end stands), or the Google address changed.
      if (acct.email) await store.del(await emailKey(acct.email));
      acct = { ...acct, email: user.email, erased: 0 };
      await saveAccount(store, acct);
    }
  }
  await store.set(await emailKey(user.email), user.sub);

  const token = randomToken();
  await store.set(`tok:${await sha256(token)}`, JSON.stringify({ acct: user.sub, created: now }));
  return json({ token, account: await accountView(store, cfg, acct, now) });
}

const WEB_SIGN_IN_MS = 60 * 60 * 1000;

/** POST /api/auth/web {"id_token"}: the website's account page. A checked Google sign-in (from
 * the website's own Google client) becomes a token that lasts an hour and is good for two
 * things: reading the account (/api/me) and opening Stripe's pages (/api/billing/...). It never
 * reaches a model, so a token lifted from a browser can't spend anyone's allowance.
 * It never makes an account either: the trial starts in the app, the first time they sign in
 * there, so nobody burns their 3 days by visiting a web page. */
export async function signInWeb(store: Store, cfg: Config, req: Request, keys: KeySource): Promise<Response> {
  if (!cfg.googleClientId || !cfg.googleWebClientId) return fail(503, "sign_in_off", WORDS.sign_in_off);
  const wait = await overLimit(store, "auth", req, cfg.authPerIpPerHour);
  if (wait) return tooMany(wait);
  const body = (await req.json().catch(() => null)) as { id_token?: unknown } | null;
  const user = await verifyGoogleIdToken(body?.id_token, cfg.googleWebClientId, keys);
  if (!user) return fail(401, "bad_sign_in", WORDS.bad_sign_in);
  const acct = await accountOf(store, user.sub);
  if (!acct || acct.erased) return fail(404, "no_account", WORDS.no_account);
  if (acct.revoked) return fail(403, "account_closed", WORDS.account_closed);
  const now = Date.now();
  const token = randomToken();
  await store.set(`tok:${await sha256(token)}`, JSON.stringify({ acct: user.sub, created: now, web: true, expires: now + WEB_SIGN_IN_MS }));
  return json({ token, account: await accountView(store, cfg, acct, now) });
}

/** POST /api/auth/signout (Bearer): this device's token stops working. Other devices carry on. */
export async function signOut(store: Store, req: Request): Promise<Response> {
  const token = bearer(req);
  const key = token.startsWith("pyxi_") ? `tok:${await sha256(token)}` : "";
  if (!key || (await store.get(key)) === null) return fail(401, "not_signed_in", WORDS.not_signed_in);
  await store.del(key);
  return json({ ok: true });
}

/** POST /api/admin/account (Bearer ADMIN_TOKEN), for the founder (cloud/admin.ts calls it):
 * {"email": "..."} shows the account: plan, status, spend, Stripe ids;
 * add "extend_trial_days": N to give N more days of trial (and a fresh trial allowance);
 * add "revoke": true to switch the account off on every device, "restore": true to undo;
 * add "erase": true to remove the email and Stripe ids (the trial clock stays, so signing in
 * again can't start a new trial). Without ADMIN_TOKEN set, the route doesn't exist. */
export async function adminAccount(store: Store, cfg: Config, req: Request): Promise<Response> {
  if (!cfg.adminToken) return json({ error: "not found" }, 404);
  if (!(await sameSecret(bearer(req), cfg.adminToken))) return json({ error: "not allowed" }, 403);
  const body = ((await req.json().catch(() => ({}))) ?? {}) as Record<string, unknown>;
  const email = String(body.email ?? "").trim().toLowerCase();
  if (!email.includes("@")) return json({ error: 'Send {"email": "..."}.' }, 400);
  const sub = await store.get(await emailKey(email));
  let acct = sub ? await accountOf(store, sub) : null;
  if (!acct) return json({ error: "No account with that email." }, 404);
  const now = Date.now();
  let did = "looked up";

  if (body.extend_trial_days !== undefined) {
    const days = Number(body.extend_trial_days);
    if (!Number.isInteger(days) || days < 1 || days > 365) return json({ error: "extend_trial_days must be a whole number from 1 to 365." }, 400);
    const paid = ["active", "past_due"].includes(standing(acct, cfg, now).status);
    acct = { ...acct, trial_ends: Math.max(acct.trial_ends, now) + days * DAY_MS };
    // Someone with a running plan keeps it; anyone else is back on the trial until the new end.
    if (!paid) acct = { ...acct, plan: "trial", status: "trial", cancels: false, past_due_since: 0 };
    await saveAccount(store, acct);
    if (!paid) await store.del(`spend:trial:${acct.sub}`);
    did = paid ? `trial end moved ${days} days on (they have a running plan, so nothing changes for them now)` : `trial extended by ${days} days`;
  } else if (body.revoke === true || body.restore === true) {
    acct = { ...acct, revoked: body.revoke === true ? now : 0 };
    await saveAccount(store, acct);
    did = body.revoke === true ? "switched off" : "switched back on";
  } else if (body.erase === true) {
    if (["active", "past_due"].includes(standing(acct, cfg, now).status))
      return json({ error: "This account has a running plan. Cancel it in Stripe first, then erase." }, 409);
    await store.del(await emailKey(acct.email));
    if (acct.customer) await store.del(`cus:${acct.customer}`);
    acct = { ...acct, email: "", customer: "", subscription: "", period_end: 0, cancels: false, past_due_since: 0, plan: "trial", status: "trial", erased: now };
    await saveAccount(store, acct);
    return json({ ok: true, did: "erased: the email and Stripe ids are gone; the trial clock stays" });
  }

  return json({
    ok: true,
    did,
    account: await accountView(store, cfg, acct, now),
    created: new Date(acct.created).toISOString(),
    revoked: Boolean(acct.revoked),
    stripe_customer: acct.customer || null,
    stripe_subscription: acct.subscription || null,
    spent_trial_usd: Number((await store.get(`spend:trial:${acct.sub}`)) ?? 0),
    spent_this_month_usd: Number((await store.get(`spend:acct:${acct.sub}:${monthOf(now)}`)) ?? 0),
  });
}
