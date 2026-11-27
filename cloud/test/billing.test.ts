import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { signIn } from "../lib/accounts.js";
import { chat, configFromEnv, join, me } from "../lib/beta.js";
import { checkout, planOfPrice, portal, subscriptionOfEvent, webhook } from "../lib/billing.js";
import { MemoryStore } from "../lib/store.js";
import { readSubscription, stripeFromKey, validStripeSignature } from "../lib/stripe.js";
import { as, cfg, errorOf, FakeStripe, paidCfg, post, type Signer, signer, STRIPE_ENV, stored, stripeEvent, stripeSignature, WEBHOOK_SECRET } from "./helpers.js";

const DAY = 86_400_000;
const MODEL = "anthropic/claude-haiku-5.5";
const SUB = "108000000000000000001";
let google: Signer;
beforeAll(async () => {
  google = await signer();
});
afterEach(() => vi.useRealTimers());

async function signedIn(store: MemoryStore, claims: Record<string, unknown> = {}): Promise<string> {
  const res = await signIn(store, paidCfg, post("/api/auth/google", { id_token: await google.token(claims) }), google.keys);
  expect(res.status).toBe(200);
  return ((await res.json()) as { token: string }).token;
}
const account = async (store: MemoryStore, token: string) =>
  ((await (await me(store, paidCfg, new Request("https://x/api/me", { headers: as(token) }))).json()) as { account: Record<string, unknown> }).account;
const ok = (async () => new Response(JSON.stringify({ usage: { cost: 0.01 } }), { status: 200 })) as unknown as typeof fetch;
const ask = (store: MemoryStore, token: string) =>
  chat(store, paidCfg, post("/api/v1/chat/completions", { model: MODEL }, as(token)), ok);

/** Sign in, go through checkout, and return what is needed to play Stripe's part. */
async function customer(plan = "pro", yearly = false) {
  const store = new MemoryStore();
  const stripe = new FakeStripe();
  const token = await signedIn(store);
  const res = await checkout(store, paidCfg, post("/api/billing/checkout", { plan, yearly }, as(token)), stripe);
  expect(res.status).toBe(200);
  const send = async (type: string, object: Record<string, unknown>, id?: string) => webhook(store, paidCfg, await stripeEvent(type, object, id), stripe);
  return { store, stripe, token, send, cus: "cus_test1" };
}

describe("POST /api/billing/checkout", () => {
  it("answers 503 billing_off in the agreed words while Stripe isn't set up, and the trial carries on", async () => {
    const store = new MemoryStore();
    const res0 = await signIn(store, cfg, post("/api/auth/google", { id_token: await google.token() }), google.keys);
    const { token } = (await res0.json()) as { token: string };
    const res = await checkout(store, cfg, post("/api/billing/checkout", { plan: "pro", yearly: false }, as(token)), null);
    expect(res.status).toBe(503);
    expect(await errorOf(res)).toEqual({ code: "billing_off", message: "Plans aren't open yet. Your trial keeps working for now." });
    expect((await chat(store, cfg, post("/api/v1/chat/completions", { model: MODEL }, as(token)), ok)).status).toBe(200);
    // Half set up is still off: a key but no webhook secret, or no price for that plan.
    const half = configFromEnv({ ...STRIPE_ENV, STRIPE_WEBHOOK_SECRET: "" });
    expect((await checkout(store, half, post("/api/billing/checkout", { plan: "pro" }, as(token)), new FakeStripe())).status).toBe(503);
    const noPrice = configFromEnv({ ...STRIPE_ENV, STRIPE_PRICE_MAX_YEARLY: "" });
    const stripe = new FakeStripe();
    expect((await checkout(store, noPrice, post("/api/billing/checkout", { plan: "max", yearly: true }, as(token)), stripe)).status).toBe(503);
    expect(stripe.checkouts).toEqual([]);
    expect((await portal(store, cfg, post("/api/billing/portal", {}, as(token)), null)).status).toBe(503);
  });

  it("opens a Stripe page for the right one of the six prices, for the caller's own account", async () => {
    const want: [string, boolean, string][] = [
      ["starter", false, "price_starter_m"],
      ["starter", true, "price_starter_y"],
      ["pro", false, "price_pro_m"],
      ["pro", true, "price_pro_y"],
      ["max", false, "price_max_m"],
      ["max", true, "price_max_y"],
    ];
    for (const [plan, yearly, price] of want) {
      const store = new MemoryStore();
      const stripe = new FakeStripe();
      const token = await signedIn(store);
      const res = await checkout(store, paidCfg, post("/api/billing/checkout", { plan, yearly }, as(token)), stripe);
      expect(res.status).toBe(200);
      expect(((await res.json()) as { url: string }).url).toMatch(/^https:\/\/checkout\.stripe\.com\//);
      expect(stripe.customers).toEqual(["anna@example.com"]);
      expect(stripe.checkouts[0]).toMatchObject({ customer: "cus_test1", price });
      expect(stored(store).customer).toBe("cus_test1");
      expect(store.data.get("cus:cus_test1")).toBe(SUB);
      expect(stored(store)).toMatchObject({ plan: "trial", status: "trial" }); // asking is not paying
    }
  });

  it("makes one Stripe customer per account, however often checkout is opened", async () => {
    const { store, stripe, token } = await customer();
    await checkout(store, paidCfg, post("/api/billing/checkout", { plan: "max", yearly: true }, as(token)), stripe);
    expect(stripe.customers.length).toBe(1);
    expect(stripe.checkouts.map((c) => c.customer)).toEqual(["cus_test1", "cus_test1"]);
  });

  it("only ever sends people back to the site, whatever the request says", async () => {
    const store = new MemoryStore();
    const stripe = new FakeStripe();
    const token = await signedIn(store);
    const sneaky = {
      plan: "pro",
      yearly: false,
      success_url: "https://evil.example.com/thanks",
      cancel_url: "https://evil.example.com/",
      successUrl: "https://evil.example.com/",
      return_url: "//evil.example.com",
      redirect: "https://evil.example.com",
    };
    await checkout(store, paidCfg, post("/api/billing/checkout?success_url=https://evil.example.com", sneaky, as(token, { Origin: "https://evil.example.com", Referer: "https://evil.example.com/x" })), stripe);
    expect(stripe.checkouts[0].successUrl).toBe("https://www.heypyxi.com/welcome");
    expect(stripe.checkouts[0].cancelUrl).toBe("https://www.heypyxi.com/billing-cancelled");
    // SITE_URL itself must be a plain https address; anything else falls back to the real site.
    expect(configFromEnv({ SITE_URL: "https://staging.example.com/" }).siteUrl).toBe("https://staging.example.com");
    for (const bad of ["javascript:alert(1)", "http://www.heypyxi.com", "https://user:pw@evil.example.com", "not a url", ""])
      expect(configFromEnv({ SITE_URL: bad }).siteUrl).toBe("https://www.heypyxi.com");
  });

  it("can't be used to raise a plan: only Stripe's word changes the account", async () => {
    const store = new MemoryStore();
    const stripe = new FakeStripe();
    const token = await signedIn(store);
    const tampered = { plan: "max", yearly: true, status: "active", price: "price_free", price_id: "price_free", amount: 0, account: { plan: "max", status: "active" }, sub: "108000000000000000002", email: "ben@example.com", customer: "cus_someone_else", trial_ends: 9e15 };
    expect((await checkout(store, paidCfg, post("/api/billing/checkout", tampered, as(token)), stripe)).status).toBe(200);
    expect(stripe.checkouts[0]).toMatchObject({ customer: "cus_test1", price: "price_max_y" });
    expect(stripe.customers).toEqual(["anna@example.com"]);
    expect(stored(store)).toMatchObject({ plan: "trial", status: "trial", sub: SUB, email: "anna@example.com" });
    for (const plan of ["trial", "none", "enterprise", "", null, 3, ["max"], { plan: "max" }]) {
      const res = await checkout(store, paidCfg, post("/api/billing/checkout", { plan }, as(token)), stripe);
      expect(res.status).toBe(400);
      expect((await errorOf(res)).code).toBe("bad_plan");
    }
    expect(stripe.checkouts.length).toBe(1);
  });

  it("needs a signed-in account: no token, an invite-code device and a signed-out device get nothing", async () => {
    const store = new MemoryStore();
    const stripe = new FakeStripe();
    expect((await checkout(store, paidCfg, post("/api/billing/checkout", { plan: "pro" }), stripe)).status).toBe(401);
    expect((await checkout(store, paidCfg, post("/api/billing/checkout", { plan: "pro" }, as("pyxi_" + "0".repeat(48))), stripe)).status).toBe(401);
    const invite = ((await (await join(store, paidCfg, "firefly")).json()) as { token: string }).token;
    const res = await checkout(store, paidCfg, post("/api/billing/checkout", { plan: "pro" }, as(invite)), stripe);
    expect(res.status).toBe(403);
    expect((await errorOf(res)).code).toBe("sign_in_needed");
    expect((await portal(store, paidCfg, post("/api/billing/portal", {}, as(invite)), stripe)).status).toBe(403);
    expect((await portal(store, paidCfg, post("/api/billing/portal", {}), stripe)).status).toBe(401);
    expect(stripe.customers).toEqual([]);
  });

  it("says so when the account already has a plan, or when Stripe can't be reached", async () => {
    const { store, stripe, token, send, cus } = await customer();
    stripe.put("sub_1", cus, "active", "price_pro_m");
    await send("customer.subscription.created", { id: "sub_1", customer: cus });
    const res = await checkout(store, paidCfg, post("/api/billing/checkout", { plan: "max" }, as(token)), stripe);
    expect(res.status).toBe(409);
    expect((await errorOf(res)).code).toBe("already_subscribed");
    expect(stripe.checkouts.length).toBe(1);

    const store2 = new MemoryStore();
    const down = new FakeStripe();
    down.down = true;
    const token2 = await signedIn(store2);
    const failed = await checkout(store2, paidCfg, post("/api/billing/checkout", { plan: "pro" }, as(token2)), down);
    expect(failed.status).toBe(502);
    expect((await errorOf(failed)).code).toBe("billing_trouble");
  });

  it("is rate limited per IP address, as is the portal", async () => {
    const store = new MemoryStore();
    const stripe = new FakeStripe();
    const few = configFromEnv({ ...STRIPE_ENV, BILLING_PER_IP_PER_HOUR: "3" });
    const token = await signedIn(store);
    const from = (ip: string, path = "checkout") =>
      (path === "checkout" ? checkout : portal)(store, few, post(`/api/billing/${path}`, { plan: "pro" }, as(token, { "x-forwarded-for": ip })), stripe);
    for (let i = 0; i < 3; i++) expect((await from("203.0.113.9")).status).toBe(200);
    const blocked = await from("203.0.113.9");
    expect(blocked.status).toBe(429);
    expect(Number(blocked.headers.get("Retry-After"))).toBeGreaterThan(0);
    expect((await from("203.0.113.9", "portal")).status).toBe(429); // the same count
    expect((await from("198.51.100.4")).status).toBe(200);
    expect([...store.data.keys()].some((k) => k.includes("203.0.113.9"))).toBe(false);
  });
});

describe("POST /api/billing/portal", () => {
  it("answers 409 no_subscription until there has been a plan, then Stripe's own page", async () => {
    const { store, stripe, token, send, cus } = await customer();
    const none = await portal(store, paidCfg, post("/api/billing/portal", {}, as(token)), stripe);
    expect(none.status).toBe(409);
    expect((await errorOf(none)).code).toBe("no_subscription");
    stripe.put("sub_1", cus, "active", "price_pro_m");
    await send("checkout.session.completed", { id: "cs_1", mode: "subscription", subscription: "sub_1", customer: cus });
    const res = await portal(store, paidCfg, post("/api/billing/portal", { customer: "cus_someone_else", return_url: "https://evil.example.com" }, as(token)), stripe);
    expect(res.status).toBe(200);
    expect(((await res.json()) as { url: string }).url).toMatch(/^https:\/\/billing\.stripe\.com\//);
    expect(stripe.portals).toEqual([{ customer: "cus_test1", returnUrl: "https://www.heypyxi.com/welcome" }]);
    stripe.down = true;
    expect((await portal(store, paidCfg, post("/api/billing/portal", {}, as(token)), stripe)).status).toBe(502);
  });
});

describe("the Stripe signature", () => {
  const body = JSON.stringify({ id: "evt_1", type: "invoice.paid", data: { object: {} } });
  const bytes = (s: string) => new TextEncoder().encode(s);

  it("accepts the right signature over the exact bytes, and nothing else", async () => {
    const now = Date.now();
    const sig = await stripeSignature(body, WEBHOOK_SECRET, now);
    expect(await validStripeSignature(bytes(body), sig, WEBHOOK_SECRET, now)).toBe(true);
    // The same JSON written differently is different bytes: the signature must fail.
    expect(await validStripeSignature(bytes(JSON.stringify(JSON.parse(body), null, 1)), sig, WEBHOOK_SECRET, now)).toBe(false);
    expect(await validStripeSignature(bytes(body + " "), sig, WEBHOOK_SECRET, now)).toBe(false);
    expect(await validStripeSignature(bytes(body), await stripeSignature(body, "whsec_wrong", now), WEBHOOK_SECRET, now)).toBe(false);
    expect(await validStripeSignature(bytes(body), sig, "", now)).toBe(false);
    for (const bad of [null, "", "t=1", "v1=abc", "garbage", sig.replace(/v1=.{4}/, "v1=0000"), sig.replace("v1=", "v0=")])
      expect(await validStripeSignature(bytes(body), bad, WEBHOOK_SECRET, now)).toBe(false);
    // Two signatures (Stripe sends both while a secret is being rolled): one good one is enough.
    expect(await validStripeSignature(bytes(body), `${sig},v1=${"0".repeat(64)}`, WEBHOOK_SECRET, now)).toBe(true);
    // Bytes that aren't tidy text are signed as they are.
    const odd = "{\"id\":\"evt_2\",\r\n  \"note\":\"café \\u00e9 \u{1F600}\"}";
    expect(await validStripeSignature(bytes(odd), await stripeSignature(odd, WEBHOOK_SECRET, now), WEBHOOK_SECRET, now)).toBe(true);
  });

  it("refuses a stale signature, and one dated in the future", async () => {
    const now = Date.now();
    const old = await stripeSignature(body, WEBHOOK_SECRET, now - 6 * 60_000);
    expect(await validStripeSignature(bytes(body), old, WEBHOOK_SECRET, now)).toBe(false);
    const recent = await stripeSignature(body, WEBHOOK_SECRET, now - 4 * 60_000);
    expect(await validStripeSignature(bytes(body), recent, WEBHOOK_SECRET, now)).toBe(true);
    const ahead = await stripeSignature(body, WEBHOOK_SECRET, now + 6 * 60_000);
    expect(await validStripeSignature(bytes(body), ahead, WEBHOOK_SECRET, now)).toBe(false);
    // Re-dating an old signature breaks it: the time is part of what was signed.
    const redated = old.replace(/t=\d+/, `t=${Math.floor(now / 1000)}`);
    expect(await validStripeSignature(bytes(body), redated, WEBHOOK_SECRET, now)).toBe(false);
  });
});

describe("POST /api/billing/webhook", () => {
  it("rejects a bad, missing or stale signature and changes nothing", async () => {
    const { store, stripe, cus } = await customer();
    stripe.put("sub_1", cus, "active", "price_max_m");
    const body = JSON.stringify({ id: "evt_forged", type: "customer.subscription.created", data: { object: { id: "sub_1", customer: cus } } });
    const tries = [
      post("/api/billing/webhook", body),
      post("/api/billing/webhook", body, { "Stripe-Signature": await stripeSignature(body, "whsec_guess") }),
      post("/api/billing/webhook", body, { "Stripe-Signature": await stripeSignature(body, WEBHOOK_SECRET, Date.now() - 10 * 60_000) }),
      post("/api/billing/webhook", body.replace("sub_1", "sub_2"), { "Stripe-Signature": await stripeSignature(body) }),
    ];
    for (const req of tries) {
      const res = await webhook(store, paidCfg, req, stripe);
      expect(res.status).toBe(400);
      expect((await errorOf(res)).code).toBe("bad_signature");
    }
    expect(stripe.lookups).toBe(0);
    expect(stored(store)).toMatchObject({ plan: "trial", status: "trial", subscription: "" });
    expect([...store.data.keys()].filter((k) => k.startsWith("evt:"))).toEqual([]);
    // With Stripe not set up there is no webhook at all.
    expect((await webhook(store, cfg, await stripeEvent("invoice.paid", {}), null)).status).toBe(503);
  });

  it("checkout completed: the account is on the plan Stripe says, whatever the event claims", async () => {
    const { store, stripe, token, send, cus } = await customer("starter");
    const end = Date.now() + 30 * DAY;
    stripe.put("sub_1", cus, "active", "price_starter_m", end);
    // The event's own fields are not what is believed: Stripe is asked.
    const res = await send("checkout.session.completed", { id: "cs_1", mode: "subscription", subscription: "sub_1", customer: cus, plan: "max", metadata: { plan: "max" }, amount_total: 0 });
    expect(await res.json()).toEqual({ received: true });
    expect(await account(store, token)).toMatchObject({ plan: "starter", status: "active", renews_at: new Date(end).toISOString(), cancels: false, trial_ends_at: null, days_left: null, allowance: { limit_usd: 4 } });
    expect((await ask(store, token)).status).toBe(200);
    // A one-off payment session (not a subscription) is not ours to act on.
    const before = stripe.lookups;
    await send("checkout.session.completed", { id: "cs_2", mode: "payment", customer: cus });
    expect(stripe.lookups).toBe(before);
  });

  it("subscription created, with a yearly price, maps to the same plan", async () => {
    const { store, stripe, token, send, cus } = await customer("max", true);
    stripe.put("sub_1", cus, "active", "price_max_y", Date.now() + 365 * DAY);
    await send("customer.subscription.created", { id: "sub_1", customer: cus, status: "active" });
    expect(await account(store, token)).toMatchObject({ plan: "max", status: "active", allowance: { limit_usd: 20 } });
    for (const [price, plan] of [["price_starter_m", "starter"], ["price_starter_y", "starter"], ["price_pro_m", "pro"], ["price_pro_y", "pro"], ["price_max_m", "max"], ["price_max_y", "max"]])
      expect(planOfPrice(paidCfg, price)).toBe(plan);
    expect(planOfPrice(paidCfg, "price_other")).toBeNull();
    expect(planOfPrice(paidCfg, "")).toBeNull();
    expect(planOfPrice(cfg, "")).toBeNull(); // no prices set: nothing matches, not even ""
  });

  it("subscription updated: a change of plan, and a cancellation set for the period's end", async () => {
    const { store, stripe, token, send, cus } = await customer("starter");
    const end = Date.now() + 12 * DAY;
    stripe.put("sub_1", cus, "active", "price_starter_m", end);
    await send("customer.subscription.created", { id: "sub_1", customer: cus });
    stripe.put("sub_1", cus, "active", "price_pro_y", end);
    await send("customer.subscription.updated", { id: "sub_1", customer: cus });
    expect(await account(store, token)).toMatchObject({ plan: "pro", status: "active", cancels: false });
    stripe.put("sub_1", cus, "active", "price_pro_y", end, true);
    await send("customer.subscription.updated", { id: "sub_1", customer: cus });
    expect(await account(store, token)).toMatchObject({ plan: "pro", status: "active", cancels: true, renews_at: new Date(end).toISOString() });
    expect((await ask(store, token)).status).toBe(200); // still paid for until then
  });

  it("subscription deleted: the plan has ended and chat says so", async () => {
    const { store, stripe, token, send, cus } = await customer();
    stripe.put("sub_1", cus, "active", "price_pro_m");
    await send("customer.subscription.created", { id: "sub_1", customer: cus });
    stripe.put("sub_1", cus, "canceled", "price_pro_m");
    await send("customer.subscription.deleted", { id: "sub_1", customer: cus });
    expect(await account(store, token)).toMatchObject({ plan: "none", status: "ended", renews_at: null, cancels: false, trial_ends_at: null });
    const res = await ask(store, token);
    expect(res.status).toBe(402);
    expect((await errorOf(res)).code).toBe("subscription_ended");
    // They can choose a plan again, and the portal still opens for their receipts.
    expect((await checkout(store, paidCfg, post("/api/billing/checkout", { plan: "starter" }, as(token)), stripe)).status).toBe(200);
    expect((await portal(store, paidCfg, post("/api/billing/portal", {}, as(token)), stripe)).status).toBe(200);
    // A subscription Stripe no longer knows at all also counts as ended.
    const b = await customer();
    b.stripe.put("sub_9", b.cus, "active", "price_pro_m");
    await b.send("customer.subscription.created", { id: "sub_9", customer: b.cus });
    b.stripe.subs.delete("sub_9");
    await b.send("customer.subscription.deleted", { id: "sub_9", customer: b.cus });
    expect(await account(b.store, b.token)).toMatchObject({ plan: "none", status: "ended" });
  });

  it("invoice payment failed: past due with a grace period, then invoice paid puts it right", async () => {
    const T0 = Date.UTC(2027, 3, 10, 12);
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(T0);
    const { store, stripe, token, send, cus } = await customer();
    stripe.put("sub_1", cus, "active", "price_pro_m", T0 + 30 * DAY);
    await send("invoice.paid", { id: "in_1", subscription: "sub_1", customer: cus });
    expect(await account(store, token)).toMatchObject({ plan: "pro", status: "active" });

    vi.setSystemTime(T0 + 30 * DAY);
    stripe.put("sub_1", cus, "past_due", "price_pro_m", T0 + 60 * DAY);
    await send("invoice.payment_failed", { id: "in_2", subscription: "sub_1", customer: cus });
    expect(await account(store, token)).toMatchObject({ plan: "pro", status: "past_due" });
    expect((await ask(store, token)).status).toBe(200); // grace
    vi.setSystemTime(T0 + 31 * DAY);
    await send("invoice.payment_failed", { id: "in_2", subscription: "sub_1", customer: cus }); // Stripe's second try fails too
    expect(stored(store).past_due_since).toBe(T0 + 30 * DAY); // the grace clock doesn't start again
    vi.setSystemTime(T0 + 33 * DAY + 1000);
    const res = await ask(store, token);
    expect(res.status).toBe(402);
    expect((await errorOf(res)).code).toBe("payment_failed");

    stripe.put("sub_1", cus, "active", "price_pro_m", T0 + 60 * DAY);
    // The newer event shape, where the invoice names its subscription under "parent".
    await send("invoice.paid", { id: "in_2", customer: cus, parent: { subscription_details: { subscription: "sub_1" } } });
    expect(await account(store, token)).toMatchObject({ plan: "pro", status: "active", renews_at: new Date(T0 + 60 * DAY).toISOString() });
    expect(stored(store).past_due_since).toBe(0);
    expect((await ask(store, token)).status).toBe(200);
  });

  it("a first payment that hasn't gone through grants nothing", async () => {
    const { store, stripe, token, send, cus } = await customer();
    stripe.put("sub_1", cus, "incomplete", "price_max_m");
    await send("customer.subscription.created", { id: "sub_1", customer: cus });
    await send("invoice.payment_failed", { id: "in_1", subscription: "sub_1", customer: cus });
    expect(await account(store, token)).toMatchObject({ plan: "trial", status: "trial" });
    stripe.put("sub_1", cus, "incomplete_expired", "price_max_m");
    await send("customer.subscription.deleted", { id: "sub_1", customer: cus });
    expect(await account(store, token)).toMatchObject({ plan: "trial", status: "trial" }); // the trial is untouched
  });

  it("a replayed event is answered at once and changes nothing", async () => {
    const { store, stripe, token, send, cus } = await customer();
    stripe.put("sub_1", cus, "active", "price_pro_m");
    expect((await send("customer.subscription.created", { id: "sub_1", customer: cus }, "evt_same")).status).toBe(200);
    expect(stripe.lookups).toBe(1);
    const snapshot = JSON.stringify([...store.data.entries()]);
    // Stripe has moved on since; an attacker (or a retry) replays the old event.
    stripe.put("sub_1", cus, "canceled", "price_pro_m");
    for (let i = 0; i < 3; i++) {
      const res = await send("customer.subscription.created", { id: "sub_1", customer: cus }, "evt_same");
      expect(await res.json()).toEqual({ received: true });
    }
    expect(stripe.lookups).toBe(1);
    expect(JSON.stringify([...store.data.entries()])).toBe(snapshot);
    expect(await account(store, token)).toMatchObject({ plan: "pro", status: "active" });
  });

  it("events out of order end in the right state", async () => {
    // "updated" (to Max) arrives before "created", then a stale "created": Stripe's present state wins.
    const a = await customer("starter");
    a.stripe.put("sub_1", a.cus, "active", "price_max_m");
    await a.send("customer.subscription.updated", { id: "sub_1", customer: a.cus, items: { data: [{ price: { id: "price_max_m" } }] } });
    await a.send("invoice.paid", { id: "in_1", subscription: "sub_1", customer: a.cus });
    await a.send("customer.subscription.created", { id: "sub_1", customer: a.cus, status: "incomplete", items: { data: [{ price: { id: "price_starter_m" } }] } });
    await a.send("checkout.session.completed", { id: "cs_1", mode: "subscription", subscription: "sub_1", customer: a.cus });
    expect(await account(a.store, a.token)).toMatchObject({ plan: "max", status: "active" });

    // "deleted" arrives before a late "updated" that still says active in its own payload.
    const b = await customer();
    b.stripe.put("sub_1", b.cus, "active", "price_pro_m");
    await b.send("customer.subscription.created", { id: "sub_1", customer: b.cus });
    b.stripe.put("sub_1", b.cus, "canceled", "price_pro_m");
    await b.send("customer.subscription.deleted", { id: "sub_1", customer: b.cus });
    await b.send("customer.subscription.updated", { id: "sub_1", customer: b.cus, status: "active" });
    await b.send("invoice.paid", { id: "in_1", subscription: "sub_1", customer: b.cus });
    expect(await account(b.store, b.token)).toMatchObject({ plan: "none", status: "ended" });

    // They subscribe again; the old subscription's late "deleted" must not end the new one.
    b.stripe.put("sub_2", b.cus, "active", "price_starter_y");
    await b.send("customer.subscription.created", { id: "sub_2", customer: b.cus });
    await b.send("customer.subscription.deleted", { id: "sub_1", customer: b.cus });
    await b.send("invoice.payment_failed", { id: "in_0", subscription: "sub_1", customer: b.cus });
    expect(await account(b.store, b.token)).toMatchObject({ plan: "starter", status: "active" });
    expect(stored(b.store).subscription).toBe("sub_2");

    // "payment failed" arrives after the "paid" that fixed it.
    const c = await customer();
    c.stripe.put("sub_1", c.cus, "active", "price_pro_m");
    await c.send("invoice.paid", { id: "in_2", subscription: "sub_1", customer: c.cus });
    await c.send("invoice.payment_failed", { id: "in_2", subscription: "sub_1", customer: c.cus });
    expect(await account(c.store, c.token)).toMatchObject({ plan: "pro", status: "active" });
  });

  it("when Stripe can't be asked, it answers 500 so Stripe sends the event again, and then it works", async () => {
    const { store, stripe, token, send, cus } = await customer();
    stripe.put("sub_1", cus, "active", "price_pro_m");
    stripe.down = true;
    const res = await send("customer.subscription.created", { id: "sub_1", customer: cus }, "evt_retry");
    expect(res.status).toBe(500);
    expect(await account(store, token)).toMatchObject({ plan: "trial" });
    stripe.down = false;
    expect((await send("customer.subscription.created", { id: "sub_1", customer: cus }, "evt_retry")).status).toBe(200);
    expect(await account(store, token)).toMatchObject({ plan: "pro", status: "active" });
  });

  it("ignores events that aren't about a Pyxi account, and flags a price it doesn't know", async () => {
    const { store, stripe, token, send, cus } = await customer();
    const snapshot = () => JSON.stringify(stored(store));
    const before = snapshot();
    stripe.put("sub_x", "cus_stranger", "active", "price_max_m");
    expect((await send("customer.subscription.created", { id: "sub_x", customer: "cus_stranger" })).status).toBe(200);
    expect((await send("customer.created", { id: "cus_new" })).status).toBe(200);
    expect((await send("charge.refunded", { id: "ch_1", customer: cus })).status).toBe(200);
    expect((await send("invoice.paid", { id: "in_oneoff", customer: cus })).status).toBe(200); // no subscription on it
    expect(snapshot()).toBe(before);
    // An event that names our customer but a subscription that is really someone else's:
    // Stripe says whose it is, and that is what counts.
    expect((await send("customer.subscription.updated", { id: "sub_x", customer: cus })).status).toBe(200);
    expect(snapshot()).toBe(before);
    stripe.put("sub_1", cus, "active", "price_something_else");
    const res = await send("customer.subscription.created", { id: "sub_1", customer: cus }, "evt_price");
    expect(res.status).toBe(500);
    expect((await errorOf(res)).code).toBe("unknown_price");
    expect(await account(store, token)).toMatchObject({ plan: "trial", status: "trial" });
    expect(store.data.has("evt:evt_price")).toBe(false); // so Stripe's retry is looked at again
    // Unreadable but correctly signed bodies are refused, not crashed on.
    for (const body of ["not json", "{}", '{"id":""}', "null"]) {
      const bad = await webhook(store, paidCfg, post("/api/billing/webhook", body, { "Stripe-Signature": await stripeSignature(body) }), stripe);
      expect(bad.status).toBe(400);
    }
  });

  it("knows which subscription each kind of event is about", () => {
    const of = (type: string, object: Record<string, unknown>) => subscriptionOfEvent({ type, data: { object } });
    expect(of("checkout.session.completed", { mode: "subscription", subscription: "sub_1" })).toBe("sub_1");
    expect(of("checkout.session.completed", { mode: "subscription", subscription: { id: "sub_1" } })).toBe("sub_1");
    expect(of("checkout.session.completed", { mode: "setup", subscription: "sub_1" })).toBe("");
    for (const t of ["created", "updated", "deleted"]) expect(of(`customer.subscription.${t}`, { id: "sub_2" })).toBe("sub_2");
    for (const t of ["invoice.paid", "invoice.payment_failed", "invoice.payment_succeeded"]) {
      expect(of(t, { id: "in_1", subscription: "sub_3" })).toBe("sub_3");
      expect(of(t, { id: "in_1", parent: { subscription_details: { subscription: "sub_4" } } })).toBe("sub_4");
      expect(of(t, { id: "in_1" })).toBe("");
    }
    expect(of("customer.updated", { id: "cus_1" })).toBe("");
    expect(subscriptionOfEvent({})).toBe("");
  });
});

describe("talking to Stripe (plain fetch)", () => {
  it("sends the right requests, with the key only in the Authorization header", async () => {
    const sent: { url: string; method: string; auth: string; body: string }[] = [];
    const answers: Record<string, unknown> = {
      customers: { id: "cus_live1" },
      "checkout/sessions": { url: "https://checkout.stripe.com/c/pay/cs_1" },
      "billing_portal/sessions": { url: "https://billing.stripe.com/p/session/1" },
      "subscriptions/sub_1": { id: "sub_1", customer: "cus_live1", status: "active", cancel_at_period_end: false, items: { data: [{ price: { id: "price_pro_m" }, current_period_end: 1800000000 }] } },
    };
    const fake = (async (url: string, init: RequestInit) => {
      const path = url.replace("https://api.stripe.com/v1/", "");
      sent.push({ url, method: String(init.method), auth: new Headers(init.headers).get("authorization") ?? "", body: String(init.body ?? "") });
      return path in answers ? new Response(JSON.stringify(answers[path])) : new Response('{"error":{"message":"No such thing"}}', { status: 404 });
    }) as unknown as typeof fetch;
    const stripe = stripeFromKey("sk_test_only", fake);
    expect(await stripe.createCustomer("anna@example.com")).toBe("cus_live1");
    expect(await stripe.createCheckout({ customer: "cus_live1", price: "price_pro_m", successUrl: "https://www.heypyxi.com/pricing.html?checkout=done", cancelUrl: "https://www.heypyxi.com/pricing.html" })).toBe("https://checkout.stripe.com/c/pay/cs_1");
    expect(await stripe.createPortal("cus_live1", "https://www.heypyxi.com/pricing.html")).toBe("https://billing.stripe.com/p/session/1");
    expect(await stripe.getSubscription("sub_1")).toEqual({ id: "sub_1", customer: "cus_live1", status: "active", price: "price_pro_m", periodEnd: 1800000000000, cancels: false });
    expect(await stripe.getSubscription("sub_gone")).toBeNull();
    expect(await stripe.getSubscription("../customers")).toBeNull(); // never becomes part of a URL
    expect(sent.length).toBe(5);
    for (const s of sent) {
      expect(s.url.startsWith("https://api.stripe.com/v1/")).toBe(true);
      expect(s.auth).toBe("Bearer sk_test_only");
      expect(s.url + s.body).not.toContain("sk_test_only");
    }
    const form = new URLSearchParams(sent[1].body);
    expect(Object.fromEntries(form)).toEqual({
      mode: "subscription",
      customer: "cus_live1",
      "line_items[0][price]": "price_pro_m",
      "line_items[0][quantity]": "1",
      success_url: "https://www.heypyxi.com/pricing.html?checkout=done",
      cancel_url: "https://www.heypyxi.com/pricing.html",
    });
    expect(Object.fromEntries(new URLSearchParams(sent[0].body))).toEqual({ email: "anna@example.com" }); // nothing else about the person
    // A Stripe error surfaces as a status only: no key, no Stripe message.
    const failing = stripeFromKey("sk_test_only", (async () => new Response('{"error":{"message":"Invalid API Key provided: sk_test_only"}}', { status: 401 })) as unknown as typeof fetch);
    await expect(failing.createCustomer("anna@example.com")).rejects.toThrow(/^stripe customers: 401$/);
    await expect(failing.getSubscription("sub_1")).rejects.toThrow(/^stripe subscriptions: 401$/);
  });

  it("reads a subscription in the old and new shapes", () => {
    expect(readSubscription({ id: "sub_1", customer: { id: "cus_1" }, status: "past_due", current_period_end: 1800000000, cancel_at_period_end: true, items: { data: [{ price: "price_a" }] } })).toEqual({
      id: "sub_1", customer: "cus_1", status: "past_due", price: "price_a", periodEnd: 1800000000000, cancels: true,
    });
    expect(readSubscription({ id: "sub_2", customer: "cus_2", status: "active", cancel_at: 1800000000, items: { data: [] } })).toMatchObject({ price: "", periodEnd: 0, cancels: true });
    expect(readSubscription({})).toEqual({ id: "", customer: "", status: "", price: "", periodEnd: 0, cancels: false });
  });
});
