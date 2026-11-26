// Paying for a plan: a Stripe Checkout page to start, Stripe's own portal to change or cancel,
// and the webhook through which Stripe tells the server what a subscription's state is.
//
// The plan an account is on only ever comes from Stripe: on every webhook the server asks Stripe
// for the subscription as it is now and writes that down. So the order events arrive in doesn't
// matter, a replayed event changes nothing, and nothing in a request can raise a plan.

import { type AccountRecord, WORDS, accountOf, saveAccount, standing } from "./accounts.js";
import { type Config, PAID_PLANS, type PaidPlan, identify } from "./beta.js";
import type { Store } from "./store.js";
import { type Stripe, type Subscription, stripeFromKey, validStripeSignature } from "./stripe.js";
import { fail, json, overLimit, tooMany } from "./util.js";

const EVENT_TTL = 7 * 86400; // Stripe retries an event for up to three days

/** Plans are open only when the server can both charge and hear back from Stripe. */
export const billingOn = (cfg: Config) => Boolean(cfg.stripeKey && cfg.stripeWebhookSecret);

export function stripeFromConfig(cfg: Config): Stripe | null {
  return billingOn(cfg) ? stripeFromKey(cfg.stripeKey) : null;
}

/** Which plan a Stripe price id is, monthly or yearly; null for a price that isn't Pyxi's. */
export function planOfPrice(cfg: Config, price: string): PaidPlan | null {
  for (const plan of PAID_PLANS) if (price && (cfg.prices[plan] === price || cfg.prices[`${plan}_yearly`] === price)) return plan;
  return null;
}

/** The signed-in account for a billing request, or the answer to send instead. */
async function payer(store: Store, cfg: Config, req: Request): Promise<AccountRecord | Response> {
  const wait = await overLimit(store, "bill", req, cfg.billingPerIpPerHour);
  if (wait) return tooMany(wait);
  const who = await identify(store, req, cfg);
  if (!who) return fail(401, "not_signed_in", WORDS.not_signed_in);
  if (!who.account) return fail(403, "sign_in_needed", "Sign in with Google to choose a plan.");
  return who.account;
}

/** POST /api/billing/checkout (Bearer) {"plan", "yearly"} -> {"url"}: a Stripe Checkout page for
 * the caller's own account. The request picks only which of the six prices; where Stripe sends
 * the person afterwards is always the site (SITE_URL), never an address from the request. */
export async function checkout(store: Store, cfg: Config, req: Request, stripe: Stripe | null): Promise<Response> {
  const acct = await payer(store, cfg, req);
  if (acct instanceof Response) return acct;
  const body = (await req.json().catch(() => null)) as { plan?: unknown; yearly?: unknown } | null;
  const plan = PAID_PLANS.find((p) => p === body?.plan);
  if (!plan) return fail(400, "bad_plan", "Choose Starter, Pro or Max.");
  const price = cfg.prices[body?.yearly === true ? `${plan}_yearly` : plan];
  if (!stripe || !billingOn(cfg) || !price) return fail(503, "billing_off", WORDS.billing_off);
  if (["active", "past_due"].includes(standing(acct, cfg).status))
    return fail(409, "already_subscribed", "You already have a Pyxi plan. Open your plan page to change it.");
  try {
    let customer = acct.customer;
    if (!customer) {
      customer = await stripe.createCustomer(acct.email);
      // Written before the checkout page exists, so Stripe's first event already finds the account.
      await store.set(`cus:${customer}`, acct.sub);
      const latest = (await accountOf(store, acct.sub)) ?? acct;
      await saveAccount(store, { ...latest, customer });
    }
    const url = await stripe.createCheckout({
      customer,
      price,
      successUrl: `${cfg.siteUrl}/pricing.html?checkout=done`,
      cancelUrl: `${cfg.siteUrl}/pricing.html?checkout=cancelled`,
    });
    return json({ url });
  } catch {
    return fail(502, "billing_trouble", "The payment page didn't open. Try again in a moment.");
  }
}

/** POST /api/billing/portal (Bearer) -> {"url"}: Stripe's own page for the caller's account. */
export async function portal(store: Store, cfg: Config, req: Request, stripe: Stripe | null): Promise<Response> {
  const acct = await payer(store, cfg, req);
  if (acct instanceof Response) return acct;
  if (!stripe || !billingOn(cfg)) return fail(503, "billing_off", WORDS.billing_off);
  if (!acct.customer || !acct.subscription) return fail(409, "no_subscription", "You don't have a plan yet. Choose one first.");
  try {
    return json({ url: await stripe.createPortal(acct.customer, `${cfg.siteUrl}/pricing.html`) });
  } catch {
    return fail(502, "billing_trouble", "Your plan page didn't open. Try again in a moment.");
  }
}

const idOf = (v: unknown): string =>
  typeof v === "string" ? v : v && typeof v === "object" && typeof (v as { id?: unknown }).id === "string" ? (v as { id: string }).id : "";

/** The subscription a Stripe event is about ("" if it isn't about one we follow). */
export function subscriptionOfEvent(event: { type?: unknown; data?: { object?: Record<string, unknown> } }): string {
  const obj = event.data?.object ?? {};
  switch (event.type) {
    case "checkout.session.completed":
      return obj.mode === "subscription" ? idOf(obj.subscription) : "";
    case "customer.subscription.created":
    case "customer.subscription.updated":
    case "customer.subscription.deleted":
      return idOf(obj.id);
    case "invoice.paid":
    case "invoice.payment_succeeded":
    case "invoice.payment_failed": {
      const parent = obj.parent as { subscription_details?: { subscription?: unknown } } | undefined;
      return idOf(obj.subscription) || idOf(parent?.subscription_details?.subscription);
    }
    default:
      return "";
  }
}

const LIVE = new Set(["active", "trialing", "past_due", "unpaid"]);
const DEAD = new Set(["canceled", "incomplete_expired", "paused"]);

/** Write Stripe's present state of a subscription onto the account. Returns the account to
 * save, null if nothing changes, or "unknown_price" if the subscription isn't for a Pyxi plan. */
export function applySubscription(cfg: Config, acct: AccountRecord, sub: Subscription, now = Date.now()): AccountRecord | null | "unknown_price" {
  if (LIVE.has(sub.status)) {
    const plan = planOfPrice(cfg, sub.price);
    if (!plan) return "unknown_price";
    const paid = sub.status === "active" || sub.status === "trialing";
    return {
      ...acct,
      subscription: sub.id,
      plan,
      status: paid ? "active" : "past_due",
      period_end: sub.periodEnd,
      cancels: sub.cancels,
      past_due_since: paid ? 0 : (acct.subscription === sub.id && acct.past_due_since) || now,
    };
  }
  // Ended: only if it is the subscription the account is on now. A late event about an older
  // subscription must not end a newer one, or a trial the founder has since extended.
  if (DEAD.has(sub.status) && acct.subscription === sub.id && (acct.status === "active" || acct.status === "past_due"))
    return { ...acct, plan: "none", status: "ended", cancels: false, past_due_since: 0 };
  return null; // "incomplete" (first payment not finished) grants nothing
}

/** POST /api/billing/webhook: Stripe's events. The signature is checked over the raw bytes of
 * the body before anything is read from it. An event seen before answers at once. */
export async function webhook(store: Store, cfg: Config, req: Request, stripe: Stripe | null): Promise<Response> {
  if (!stripe || !billingOn(cfg)) return fail(503, "billing_off", WORDS.billing_off);
  const raw = new Uint8Array(await req.arrayBuffer());
  if (!(await validStripeSignature(raw, req.headers.get("stripe-signature"), cfg.stripeWebhookSecret)))
    return fail(400, "bad_signature", "The signature didn't match.");
  let event: { id?: unknown; type?: unknown; data?: { object?: Record<string, unknown> } };
  try {
    event = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return fail(400, "bad_event", "The event couldn't be read.");
  }
  const id = typeof event?.id === "string" && /^[\w-]{1,255}$/.test(event.id) ? event.id : "";
  if (!id) return fail(400, "bad_event", "The event couldn't be read.");
  if ((await store.get(`evt:${id}`)) !== null) return json({ received: true }); // a replay

  const subId = subscriptionOfEvent(event);
  if (subId) {
    let sub: Subscription | null;
    try {
      sub = await stripe.getSubscription(subId);
    } catch {
      return fail(500, "try_again", "Stripe couldn't be reached."); // Stripe sends the event again
    }
    // Gone from Stripe altogether counts as ended. The customer then comes from the (signed) event.
    const customer = sub?.customer || idOf(event.data?.object?.customer);
    sub ??= { id: subId, customer, status: "canceled", price: "", periodEnd: 0, cancels: false };
    const owner = customer ? await store.get(`cus:${customer}`) : null;
    const acct = owner ? await accountOf(store, owner) : null;
    if (acct && !acct.erased) {
      const next = applySubscription(cfg, acct, sub);
      if (next === "unknown_price") return fail(500, "unknown_price", "This subscription's price isn't one of the six set for Pyxi.");
      if (next) await saveAccount(store, next);
    }
  }
  await store.set(`evt:${id}`, "1", EVENT_TTL);
  return json({ received: true });
}
