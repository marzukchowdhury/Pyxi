import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { adminAccount, config, signIn, signOut, WORDS } from "../lib/accounts.js";
import { chat, configFromEnv, join, me, notSignedIn } from "../lib/beta.js";
import { googleKeys, verifyGoogleIdToken } from "../lib/google.js";
import { MemoryStore } from "../lib/store.js";
import { as, cfg, CLIENT_ID, ENV, errorOf, post, type Signer, signer, stored } from "./helpers.js";

const DAY = 86_400_000;
const MODEL = "anthropic/claude-haiku-5.5";
let google: Signer;
beforeAll(async () => {
  google = await signer();
});
afterEach(() => vi.useRealTimers());

/** Freeze "now" (only the clock: promises and crypto run as usual). */
function at(ms: number) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(ms);
}
const T0 = Date.UTC(2027, 0, 10, 12, 0, 0);

interface Account {
  email: string;
  plan: string;
  status: string;
  trial_ends_at: string | null;
  days_left: number | null;
  renews_at: string | null;
  cancels: boolean;
  allowance: { used_usd: number; limit_usd: number; resets_at: string | null };
}

async function signInAs(store: MemoryStore, claims: Record<string, unknown> = {}, c = cfg, ip = "198.51.100.7") {
  const res = await signIn(store, c, post("/api/auth/google", { id_token: await google.token(claims) }, { "x-forwarded-for": ip }), google.keys);
  expect(res.status).toBe(200);
  return (await res.json()) as { token: string; account: Account };
}

const costing = (cost: number) =>
  (async () => new Response(JSON.stringify({ choices: [], usage: { cost } }), { status: 200 })) as unknown as typeof fetch;
const never = (async () => {
  throw new Error("should not reach OpenRouter");
}) as unknown as typeof fetch;
const ask = (store: MemoryStore, token: string, upstream = costing(0.01), c = cfg) =>
  chat(store, c, post("/api/v1/chat/completions", { model: MODEL, messages: [{ role: "user", content: "hi" }] }, as(token)), upstream);
const meOf = async (store: MemoryStore, token: string, c = cfg) =>
  (await (await me(store, c, new Request("https://beta.pyxi.app/api/me", { headers: as(token) }))).json()) as Record<string, unknown> & { account: Account | null };

/** Put an account straight into a paid state, as the webhook would. */
function setPaid(store: MemoryStore, fields: Record<string, unknown>, sub = "108000000000000000001") {
  store.data.set(`acct:${sub}`, JSON.stringify({ ...stored(store, sub), customer: "cus_test1", subscription: "sub_test1", ...fields }));
}

describe("checking a Google ID token", () => {
  const check = async (claims: Record<string, unknown> = {}, header: Record<string, unknown> = {}) =>
    verifyGoogleIdToken(await google.token(claims, header), CLIENT_ID, google.keys);

  it("accepts a good one and keeps only sub and email", async () => {
    expect(await check()).toEqual({ sub: "108000000000000000001", email: "anna@example.com" });
    expect(await check({ iss: "accounts.google.com", email: "Anna@Example.com", email_verified: "true" })).toEqual({
      sub: "108000000000000000001",
      email: "anna@example.com",
    });
  });

  it("refuses the wrong audience, issuer, an expired one and an unverified email", async () => {
    const now = Math.floor(Date.now() / 1000);
    expect(await check({ aud: "someone-else.apps.googleusercontent.com" })).toBeNull();
    expect(await check({ aud: [CLIENT_ID, "other"] })).toBeNull();
    expect(await check({ iss: "https://accounts.example.com" })).toBeNull();
    expect(await check({ exp: now - 120 })).toBeNull();
    expect(await check({ exp: undefined })).toBeNull();
    expect(await check({ iat: now + 3600 })).toBeNull();
    expect(await check({ email_verified: false })).toBeNull();
    expect(await check({ email_verified: undefined })).toBeNull();
    expect(await check({ sub: "" })).toBeNull();
    expect(await check({ email: undefined })).toBeNull();
    expect(await verifyGoogleIdToken(await google.token(), "", google.keys)).toBeNull(); // no client id set
  });

  it("refuses a bad signature, another signer, an unknown key and unsigned tokens", async () => {
    const good = await google.token();
    const [h, p, s] = good.split(".");
    const forged = Buffer.from(JSON.stringify({ ...JSON.parse(Buffer.from(p, "base64url").toString()), email: "mallory@example.com" })).toString("base64url");
    expect(await verifyGoogleIdToken(`${h}.${forged}.${s}`, CLIENT_ID, google.keys)).toBeNull(); // payload changed
    const other = await signer("test-key-1"); // same key id, different key
    expect(await verifyGoogleIdToken(await other.token(), CLIENT_ID, google.keys)).toBeNull();
    expect(await check({}, { kid: "unknown-key" })).toBeNull();
    expect(await check({}, { alg: "none" })).toBeNull();
    expect(await check({}, { alg: "HS256" })).toBeNull();
    expect(await verifyGoogleIdToken(`${Buffer.from('{"alg":"none"}').toString("base64url")}.${p}.`, CLIENT_ID, google.keys)).toBeNull();
    for (const junk of ["", "abc", "a.b.c", null, 42, { id_token: good }, "x".repeat(9000)])
      expect(await verifyGoogleIdToken(junk, CLIENT_ID, google.keys)).toBeNull();
    const broken = (async () => {
      throw new Error("no network");
    }) as never;
    expect(await verifyGoogleIdToken(good, CLIENT_ID, broken)).toBeNull(); // Google's keys unreachable
  });

  it("fetches Google's keys once, keeps them, and re-fetches when they run out", async () => {
    let fetches = 0;
    let clock = T0;
    const fake = (async (url: string) => {
      fetches += 1;
      expect(url).toBe("https://www.googleapis.com/oauth2/v3/certs");
      return new Response(JSON.stringify({ keys: [google.jwk, { kid: "odd", kty: "EC" }] }), { headers: { "Cache-Control": "public, max-age=600" } });
    }) as unknown as typeof fetch;
    const keys = googleKeys(fake, () => clock);
    const token = await google.token();
    expect(await verifyGoogleIdToken(token, CLIENT_ID, keys)).not.toBeNull();
    expect(await verifyGoogleIdToken(token, CLIENT_ID, keys)).not.toBeNull();
    expect(fetches).toBe(1);
    // An unknown key id doesn't make it ask Google again within a minute...
    expect(await keys("who-knows")).toBeNull();
    expect(fetches).toBe(1);
    clock += 61_000; // ...but does after that (Google rotates its keys)
    expect(await keys("who-knows")).toBeNull();
    expect(fetches).toBe(2);
    clock += 601_000; // past max-age
    expect(await keys(google.kid)).not.toBeNull();
    expect(fetches).toBe(3);
  });
});

describe("GET /api/config", () => {
  it("says whether sign-in and invite codes are on, and nothing secret", async () => {
    expect(await config(cfg).json()).toEqual({ sign_in: true, google_client_id: CLIENT_ID, invite_codes: true });
    const off = configFromEnv({ OPENROUTER_API_KEY: "sk-or-test", STRIPE_SECRET_KEY: "sk_test_only" });
    const body = await config(off).json();
    expect(body).toEqual({ sign_in: false, google_client_id: null, invite_codes: false });
    expect(JSON.stringify(body)).not.toContain("sk_");
  });
});

describe("POST /api/auth/google", () => {
  it("makes the account, starts a 3-day trial of Pro and hands this device a token", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token, account } = await signInAs(store);
    expect(token).toMatch(/^pyxi_[0-9a-f]{48}$/);
    expect(account).toEqual({
      email: "anna@example.com",
      plan: "trial",
      status: "trial",
      trial_ends_at: new Date(T0 + 3 * DAY).toISOString(),
      days_left: 2,
      renews_at: null,
      cancels: false,
      allowance: { used_usd: 0, limit_usd: 1.5, resets_at: null },
    });
    const mine = await meOf(store, token);
    expect(mine.account).toEqual(account);
    expect(mine.user).toMatch(/^[0-9a-f]{16}$/);
    expect(Object.keys(mine).sort()).toEqual(["account", "daily_limit_usd", "left_today_usd", "spent_today_usd", "user"]);
    expect(mine.left_today_usd).toBe(1.5);
  });

  it("counts the trial down: 2, 1, 0 on the last day, then ended", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    const left = async () => (await meOf(store, token)).account!;
    at(T0 + DAY + 1000);
    expect((await left()).days_left).toBe(1);
    at(T0 + 2 * DAY + 1000);
    expect((await left()).days_left).toBe(0);
    expect((await left()).status).toBe("trial");
    at(T0 + 3 * DAY + 1000);
    expect(await left()).toMatchObject({ plan: "none", status: "ended", days_left: null, trial_ends_at: new Date(T0 + 3 * DAY).toISOString(), allowance: { used_usd: 0, limit_usd: 0, resets_at: null } });
  });

  it("answers 401 bad_sign_in in the agreed words for anything that isn't a good token", async () => {
    const store = new MemoryStore();
    const tryWith = (body: unknown) => signIn(store, cfg, post("/api/auth/google", body), google.keys);
    for (const body of [{}, { id_token: "nope" }, "not json", { id_token: await google.token({ aud: "other" }) }, { id_token: await google.token({ email_verified: false }) }]) {
      const res = await tryWith(body);
      expect(res.status).toBe(401);
      expect(await errorOf(res)).toEqual({ code: "bad_sign_in", message: "That Google sign-in didn't work. Try again." });
    }
    expect([...store.data.keys()].filter((k) => !k.startsWith("rl:"))).toEqual([]); // nothing was made
  });

  it("is off (503) until the server has a Google client id", async () => {
    const off = configFromEnv({ ...ENV, GOOGLE_SIGNIN_CLIENT_ID: "" });
    const res = await signIn(new MemoryStore(), off, post("/api/auth/google", { id_token: await google.token() }), google.keys);
    expect(res.status).toBe(503);
    expect((await errorOf(res)).code).toBe("sign_in_off");
  });

  it("is rate limited per IP address, keeping only a hash of it", async () => {
    const store = new MemoryStore();
    const few = configFromEnv({ ...ENV, AUTH_PER_IP_PER_HOUR: "3" });
    const from = async (ip: string, good = false) =>
      signIn(store, few, post("/api/auth/google", { id_token: good ? await google.token() : "junk" }, { "x-forwarded-for": `${ip}, 10.0.0.1` }), google.keys);
    for (let i = 0; i < 3; i++) expect((await from("203.0.113.9")).status).toBe(401);
    const blocked = await from("203.0.113.9", true); // even a good sign-in waits
    expect(blocked.status).toBe(429);
    expect((await errorOf(blocked)).code).toBe("too_many_tries");
    expect(Number(blocked.headers.get("Retry-After"))).toBeGreaterThan(0);
    expect((await from("198.51.100.4", true)).status).toBe(200); // someone else
    expect([...store.data.keys()].some((k) => k.includes("203.0.113.9"))).toBe(false);
  });
});

describe("one account, any number of devices, one trial", () => {
  it("a second device joins the same account with the same trial end", async () => {
    at(T0);
    const store = new MemoryStore();
    const pc = await signInAs(store);
    at(T0 + DAY);
    const laptop = await signInAs(store);
    expect(laptop.token).not.toBe(pc.token);
    expect(laptop.account.trial_ends_at).toBe(pc.account.trial_ends_at);
    expect(laptop.account.days_left).toBe(1);
    expect([...store.data.keys()].filter((k) => k.startsWith("acct:")).length).toBe(1);
    // What either device spends counts against the one allowance.
    expect((await ask(store, pc.token, costing(0.4))).status).toBe(200);
    expect((await ask(store, laptop.token, costing(0.3))).status).toBe(200);
    expect((await meOf(store, pc.token)).account!.allowance.used_usd).toBeCloseTo(0.7);
    expect((await meOf(store, laptop.token)).account!.allowance.used_usd).toBeCloseTo(0.7);
    expect((await meOf(store, laptop.token)).spent_today_usd).toBeCloseTo(0.3); // this device, today
  });

  it("tells a device it doesn't know to sign in, and mentions an invite code only when sign-in is off", async () => {
    at(T0);
    const store = new MemoryStore();
    const said = async (res: Response) => ((await res.json()) as { error: { code: string; message: string } }).error;
    // Sign-in is on: a signed-out account device, and a token that was never real.
    const pc = await signInAs(store);
    await signOut(store, post("/api/auth/signout", {}, as(pc.token)));
    for (const token of [pc.token, "pyxi_never_given_out"]) {
      const res = await ask(store, token, never);
      expect(res.status).toBe(401);
      expect(await said(res)).toEqual({ code: "not_signed_in", message: "Sign in to Pyxi to carry on." });
    }
    // Sign-in is off (a server that still runs on invite codes alone): the invite wording stays.
    const invitesOnly = configFromEnv({ ...ENV, GOOGLE_SIGNIN_CLIENT_ID: "" });
    const res = await ask(store, "pyxi_never_given_out", never, invitesOnly);
    expect(res.status).toBe(401);
    expect(await said(res)).toEqual({ code: "not_signed_in", message: "Sign in to the Pyxi beta first (Settings, invite code)." });
    expect(notSignedIn(cfg)).not.toMatch(/invite/i);
  });

  it("signing out stops only that device, and signing in again does not restart the trial", async () => {
    at(T0);
    const store = new MemoryStore();
    const pc = await signInAs(store);
    const laptop = await signInAs(store);
    at(T0 + 2 * DAY);
    const out = await signOut(store, post("/api/auth/signout", {}, as(pc.token)));
    expect(await out.json()).toEqual({ ok: true });
    expect((await ask(store, pc.token)).status).toBe(401);
    expect((await me(store, cfg, new Request("https://x/api/me", { headers: as(pc.token) }))).status).toBe(401);
    expect((await ask(store, laptop.token)).status).toBe(200);
    expect((await signOut(store, post("/api/auth/signout", {}, as(pc.token)))).status).toBe(401); // already out
    expect((await signOut(store, post("/api/auth/signout", {}))).status).toBe(401);
    const again = await signInAs(store);
    expect(again.account.trial_ends_at).toBe(pc.account.trial_ends_at);
    expect(again.account.days_left).toBe(0);
    at(T0 + 4 * DAY);
    const late = await signInAs(store); // after the trial: still the old clock
    expect(late.account).toMatchObject({ plan: "none", status: "ended", trial_ends_at: pc.account.trial_ends_at });
    expect((await ask(store, late.token, never)).status).toBe(402);
  });

  it("two first sign-ins at the same moment still make one account and one trial", async () => {
    at(T0);
    const store = new MemoryStore();
    const [a, b] = await Promise.all([signInAs(store), signInAs(store)]);
    expect(a.account.trial_ends_at).toBe(b.account.trial_ends_at);
    expect([...store.data.keys()].filter((k) => k.startsWith("acct:")).length).toBe(1);
  });

  it("different Google accounts are different accounts, and nobody sees another's", async () => {
    const store = new MemoryStore();
    const anna = await signInAs(store);
    const ben = await signInAs(store, { sub: "108000000000000000002", email: "ben@example.com" });
    await ask(store, anna.token, costing(0.5));
    const asBen = (await (await me(store, cfg, new Request("https://x/api/me?email=anna@example.com&sub=108000000000000000001", { headers: as(ben.token) }))).json()) as { account: Account };
    expect(asBen.account.email).toBe("ben@example.com");
    expect(asBen.account.allowance.used_usd).toBe(0);
    expect((await meOf(store, anna.token)).account!.allowance.used_usd).toBeCloseTo(0.5);
  });

  it("an invite-code device still works as before, with account null", async () => {
    const store = new MemoryStore();
    const res = await join(store, cfg, "firefly");
    const { token } = (await res.json()) as { token: string };
    const mine = await meOf(store, token);
    expect(mine.account).toBeNull();
    expect(mine).toMatchObject({ spent_today_usd: 0, daily_limit_usd: 0.25, left_today_usd: 0.25 });
    expect((await ask(store, token)).status).toBe(200);
    // Until INVITE_CODES is emptied: then invite-code devices are asked to sign in, accounts carry on.
    const account = await signInAs(store);
    const closed = configFromEnv({ ...ENV, INVITE_CODES: "" });
    expect((await ask(store, token, never, closed)).status).toBe(401);
    expect((await me(store, closed, new Request("https://x/api/me", { headers: as(token) }))).status).toBe(401);
    expect((await join(store, closed, "firefly")).status).toBe(403);
    expect((await ask(store, account.token, costing(0.01), closed)).status).toBe(200);
    expect((await ask(store, token)).status).toBe(200); // put the codes back and they work again
  });
});

describe("what chat answers for an account", () => {
  it("trial: works, then 402 trial_ended when the three days are over", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    expect((await ask(store, token)).status).toBe(200);
    at(T0 + 3 * DAY + 1);
    const res = await ask(store, token, never);
    expect(res.status).toBe(402);
    expect(await errorOf(res)).toEqual({ code: "trial_ended", message: "Your 3-day trial has ended. Choose a plan to keep going." });
  });

  it("trial: 429 allowance_used once the trial's total allowance is spent, even across months", async () => {
    at(Date.UTC(2027, 0, 30, 12));
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    expect((await ask(store, token, costing(1.0))).status).toBe(200);
    at(Date.UTC(2027, 1, 1, 6)); // a new month does not refill a trial
    expect((await ask(store, token, costing(0.6))).status).toBe(200);
    const res = await ask(store, token, never);
    expect(res.status).toBe(429);
    const err = await errorOf(res);
    expect(err.code).toBe("allowance_used");
    expect(err.message).toBe(WORDS.trial_allowance_used);
    expect((await meOf(store, token)).left_today_usd).toBe(0);
  });

  it("paid: the plan's monthly allowance, with the day it resets in the agreed sentence", async () => {
    at(Date.UTC(2027, 1, 20, 12));
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    setPaid(store, { plan: "starter", status: "active", period_end: Date.UTC(2027, 2, 15) });
    expect((await ask(store, token, costing(3.9))).status).toBe(200);
    expect((await ask(store, token, costing(0.2))).status).toBe(200);
    const res = await ask(store, token, never);
    expect(res.status).toBe(429);
    expect(await errorOf(res)).toEqual({
      code: "allowance_used",
      message: "You've used this month's allowance on your plan. It resets on 1 March, or you can move up a plan.",
    });
    const account = (await meOf(store, token)).account!;
    expect(account).toMatchObject({ plan: "starter", status: "active", trial_ends_at: null, days_left: null, renews_at: new Date(Date.UTC(2027, 2, 15)).toISOString() });
    expect(account.allowance.limit_usd).toBe(4);
    expect(account.allowance.used_usd).toBeCloseTo(4.1);
    expect(account.allowance.resets_at).toBe("2027-03-01T00:00:00.000Z");
    // Moving up a plan lifts it at once; a new month refills it.
    setPaid(store, { plan: "pro", status: "active", period_end: Date.UTC(2027, 2, 15) });
    expect((await ask(store, token)).status).toBe(200);
    setPaid(store, { plan: "starter", status: "active", period_end: Date.UTC(2027, 2, 15) });
    expect((await ask(store, token, never)).status).toBe(429);
    at(Date.UTC(2027, 2, 1, 0, 0, 1));
    expect((await ask(store, token)).status).toBe(200);
  });

  it("uses each plan's own limit, from the environment or the defaults", async () => {
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    const limit = async (plan: string, c = cfg) => {
      setPaid(store, { plan, status: "active", period_end: Date.now() + 10 * DAY });
      return (await meOf(store, token, c)).account!.allowance.limit_usd;
    };
    expect([await limit("starter"), await limit("pro"), await limit("max")]).toEqual([4, 9, 20]);
    const own = configFromEnv({ ...ENV, TRIAL_LIMIT_USD: "0.5", STARTER_LIMIT_USD: "5", PRO_LIMIT_USD: "11", MAX_LIMIT_USD: "25" });
    expect([await limit("starter", own), await limit("pro", own), await limit("max", own)]).toEqual([5, 11, 25]);
    expect(own.limits.trial).toBe(0.5);
  });

  it("a failed payment keeps working for the grace period, then 402 payment_failed", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    setPaid(store, { plan: "pro", status: "past_due", period_end: T0 + 20 * DAY, past_due_since: T0 });
    expect((await ask(store, token)).status).toBe(200);
    expect((await meOf(store, token)).account).toMatchObject({ plan: "pro", status: "past_due" });
    at(T0 + 3 * DAY - 1000);
    expect((await ask(store, token)).status).toBe(200);
    at(T0 + 3 * DAY + 1000);
    const res = await ask(store, token, never);
    expect(res.status).toBe(402);
    expect(await errorOf(res)).toEqual({ code: "payment_failed", message: "Your last payment didn't go through. Update your card to keep Pyxi working." });
    expect((await meOf(store, token)).account).toMatchObject({ plan: "pro", status: "past_due" });
    const longer = configFromEnv({ ...ENV, GRACE_DAYS: "7" });
    expect((await ask(store, token, costing(0.01), longer)).status).toBe(200);
  });

  it("an ended plan answers 402 subscription_ended", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    setPaid(store, { plan: "none", status: "ended", period_end: T0 - DAY });
    const res = await ask(store, token, never);
    expect(res.status).toBe(402);
    expect(await errorOf(res)).toEqual({ code: "subscription_ended", message: "Your Pyxi plan has ended. Choose a plan to carry on." });
    expect((await meOf(store, token)).account).toMatchObject({ plan: "none", status: "ended", trial_ends_at: null, renews_at: null, cancels: false });
    // A paid period that ran out long ago with no word from Stripe also ends, after the grace days.
    setPaid(store, { plan: "pro", status: "active", period_end: T0 - 2 * DAY });
    expect((await ask(store, token)).status).toBe(200);
    setPaid(store, { plan: "pro", status: "active", period_end: T0 - 4 * DAY });
    expect((await errorOf(await ask(store, token, never))).code).toBe("subscription_ended");
  });

  it("the whole service's monthly cap still applies to accounts", async () => {
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    await store.set(`spend:all:${new Date().toISOString().slice(0, 7)}`, "50");
    expect((await ask(store, token, never)).status).toBe(429);
  });

  it("counts a streamed reply's cost against the account", async () => {
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    const enc = new TextEncoder();
    const streamed = (async () =>
      new Response(new ReadableStream({
        start(c) {
          c.enqueue(enc.encode('data: {"choices":[{"delta":{"content":"On it."}}]}\n\n'));
          c.enqueue(enc.encode('data: {"choices":[],"usage":{"cost":0.25}}\n\n'));
          c.close();
        },
      }), { status: 200 })) as unknown as typeof fetch;
    const res = await chat(store, cfg, post("/api/v1/chat/completions", { model: MODEL, stream: true }, as(token)), streamed);
    expect(await res.text()).toContain("On it.");
    expect((await meOf(store, token)).account!.allowance.used_usd).toBeCloseTo(0.25);
  });
});

describe("the founder's account admin", () => {
  const admin = configFromEnv({ ...ENV, ADMIN_TOKEN: "founder-secret" });
  const run = (store: MemoryStore, body: unknown, secret = "founder-secret", c = admin) =>
    adminAccount(store, c, post("/api/admin/account", body, { Authorization: `Bearer ${secret}` }));

  it("looks an account up by email: plan, status, spend", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    await ask(store, token, costing(0.3));
    const res = await run(store, { email: " Anna@Example.com " });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      ok: true,
      account: { email: "anna@example.com", plan: "trial", status: "trial", days_left: 2 },
      created: new Date(T0).toISOString(),
      revoked: false,
      stripe_customer: null,
      stripe_subscription: null,
      spent_this_month_usd: 0,
    });
    expect((await run(store, { email: "nobody@example.com" })).status).toBe(404);
    expect((await run(store, {})).status).toBe(400);
  });

  it("is for the founder only, and doesn't exist without an admin secret", async () => {
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    expect((await run(store, { email: "anna@example.com" }, "guess")).status).toBe(403);
    expect((await run(store, { email: "anna@example.com" }, "")).status).toBe(403);
    expect((await run(store, { email: "anna@example.com" }, token)).status).toBe(403); // a user's own token is no key
    expect((await run(store, { email: "anna@example.com", extend_trial_days: 30 }, token)).status).toBe(403);
    expect((await run(store, { email: "anna@example.com" }, "founder-secret", cfg)).status).toBe(404);
    expect((await run(store, { email: "anna@example.com" }, "", cfg)).status).toBe(404);
  });

  it("extends a trial by N days, with a fresh trial allowance, even after it ended", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token } = await signInAs(store);
    await ask(store, token, costing(1.6));
    expect((await ask(store, token, never)).status).toBe(429);
    const res = await run(store, { email: "anna@example.com", extend_trial_days: 7 });
    expect(((await res.json()) as { account: Account }).account).toMatchObject({ status: "trial", trial_ends_at: new Date(T0 + 10 * DAY).toISOString(), days_left: 9, allowance: { used_usd: 0 } });
    expect((await ask(store, token)).status).toBe(200);
    at(T0 + 20 * DAY); // long over
    expect((await ask(store, token, never)).status).toBe(402);
    await run(store, { email: "anna@example.com", extend_trial_days: 2 });
    expect((await meOf(store, token)).account).toMatchObject({ status: "trial", trial_ends_at: new Date(T0 + 22 * DAY).toISOString(), days_left: 1 });
    for (const bad of [0, -3, 1.5, 9999, "soon"]) expect((await run(store, { email: "anna@example.com", extend_trial_days: bad })).status).toBe(400);
    // Someone with a running plan keeps the plan.
    setPaid(store, { plan: "max", status: "active", period_end: Date.now() + 10 * DAY });
    await run(store, { email: "anna@example.com", extend_trial_days: 5 });
    expect((await meOf(store, token)).account).toMatchObject({ plan: "max", status: "active" });
  });

  it("switches an account off on every device, and back on", async () => {
    const store = new MemoryStore();
    const pc = await signInAs(store);
    const laptop = await signInAs(store);
    const res = await run(store, { email: "anna@example.com", revoke: true });
    expect(await res.json()).toMatchObject({ ok: true, revoked: true });
    expect((await ask(store, pc.token, never)).status).toBe(401);
    expect((await ask(store, laptop.token, never)).status).toBe(401);
    const back = await signIn(store, cfg, post("/api/auth/google", { id_token: await google.token() }), google.keys);
    expect(back.status).toBe(403); // signing in again doesn't get round it
    expect((await errorOf(back)).code).toBe("account_closed");
    await run(store, { email: "anna@example.com", restore: true });
    expect((await ask(store, pc.token)).status).toBe(200);
  });

  it("erasing an account removes its details but not its trial clock", async () => {
    at(T0);
    const store = new MemoryStore();
    const { token, account } = await signInAs(store);
    await ask(store, token, costing(0.9));
    at(T0 + DAY);
    const res = await run(store, { email: "anna@example.com", erase: true });
    expect(res.status).toBe(200);
    expect((await ask(store, token, never)).status).toBe(401); // old devices are out
    expect(JSON.stringify([...store.data.entries()])).not.toContain("anna@example.com");
    expect([...store.data.keys()].filter((k) => k.startsWith("email:") || k.startsWith("cus:"))).toEqual([]);
    expect((await run(store, { email: "anna@example.com" })).status).toBe(404);
    // Signing in again finds the old clock and the old spend: no second trial.
    at(T0 + 2 * DAY);
    const again = await signInAs(store);
    expect(again.account.trial_ends_at).toBe(account.trial_ends_at);
    expect(again.account.days_left).toBe(0);
    expect(again.account.allowance.used_usd).toBeCloseTo(0.9);
    at(T0 + 5 * DAY);
    await run(store, { email: "anna@example.com", erase: true });
    const late = await signInAs(store);
    expect(late.account).toMatchObject({ plan: "none", status: "ended" });
    expect((await errorOf(await ask(store, late.token, never))).code).toBe("trial_ended");
  });

  it("won't erase an account whose plan is still running", async () => {
    const store = new MemoryStore();
    await signInAs(store);
    setPaid(store, { plan: "pro", status: "active", period_end: Date.now() + 10 * DAY });
    expect((await run(store, { email: "anna@example.com", erase: true })).status).toBe(409);
    expect(stored(store).email).toBe("anna@example.com");
  });

  it("the admin command sends the right request to the right route", async () => {
    const { accountBodyFor, run: cli } = await import("../admin");
    expect(accountBodyFor("account", "anna@example.com")).toEqual({ email: "anna@example.com" });
    expect(accountBodyFor("extend-trial", "anna@example.com", "7")).toEqual({ email: "anna@example.com", extend_trial_days: 7 });
    expect(accountBodyFor("extend-trial", "anna@example.com", "")).toBeNull();
    expect(accountBodyFor("extend-trial", "anna@example.com", "0")).toBeNull();
    expect(accountBodyFor("revoke-account", "anna@example.com")).toEqual({ email: "anna@example.com", revoke: true });
    expect(accountBodyFor("restore-account", "anna@example.com")).toEqual({ email: "anna@example.com", restore: true });
    expect(accountBodyFor("erase-account", "anna@example.com")).toEqual({ email: "anna@example.com", erase: true });
    expect(accountBodyFor("account", "not-an-email")).toBeNull();
    expect(accountBodyFor("delete-everything", "anna@example.com")).toBeNull();
    const sent: { url: string; init: RequestInit }[] = [];
    const fake = (async (url: string, init: RequestInit) => {
      sent.push({ url, init });
      return new Response('{"ok":true}', { status: 200 });
    }) as unknown as typeof fetch;
    const lines: string[] = [];
    const env = { ADMIN_TOKEN: "s3cret", PYXI_BETA_URL: "http://127.0.0.1:8790" };
    expect(await cli(["extend-trial", "anna@example.com", "7"], env, fake, (l) => lines.push(l))).toBe(0);
    expect(sent[0].url).toBe("http://127.0.0.1:8790/api/admin/account");
    expect(JSON.parse(String(sent[0].init.body))).toEqual({ email: "anna@example.com", extend_trial_days: 7 });
    expect(await cli(["revoke-code", "LAMP"], env, fake, (l) => lines.push(l))).toBe(0);
    expect(sent[1].url).toBe("http://127.0.0.1:8790/api/admin/revoke");
    expect(lines.join(" ")).not.toContain("s3cret");
    expect(await cli(["extend-trial", "anna@example.com"], env, fake, (l) => lines.push(l))).toBe(2);
    expect(sent.length).toBe(2);
  });
});
