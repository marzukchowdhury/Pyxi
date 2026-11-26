// The four things the server asks of Stripe, behind a small interface so tests use a fake, and
// the check that a webhook really came from Stripe. Plain fetch, no library: four calls and one
// HMAC don't need one. Card details never come here: people type them on Stripe's own pages.

import { sameBytes } from "./util.js";

/** A subscription as Stripe has it right now. */
export interface Subscription {
  id: string;
  customer: string;
  /** Stripe's own word: active, trialing, past_due, unpaid, canceled, incomplete, ... */
  status: string;
  /** The price id of its (only) item. */
  price: string;
  /** When the paid period ends or renews (ms). */
  periodEnd: number;
  /** Set to end at periodEnd. */
  cancels: boolean;
}

export interface Stripe {
  /** Make a customer; returns its id. */
  createCustomer(email: string): Promise<string>;
  /** Start a subscription checkout; returns the page's URL. */
  createCheckout(o: { customer: string; price: string; successUrl: string; cancelUrl: string }): Promise<string>;
  /** Open Stripe's own page for changing plan, card, cancelling and receipts; returns its URL. */
  createPortal(customer: string, returnUrl: string): Promise<string>;
  /** The subscription as it is now, or null if Stripe has none with that id. */
  getSubscription(id: string): Promise<Subscription | null>;
}

const idOf = (v: unknown): string =>
  typeof v === "string" ? v : v && typeof v === "object" && typeof (v as { id?: unknown }).id === "string" ? (v as { id: string }).id : "";

/** Read a subscription object from Stripe's JSON (old and new API shapes). */
export function readSubscription(raw: Record<string, unknown>): Subscription {
  const items = ((raw.items as { data?: Record<string, unknown>[] } | undefined)?.data ?? []) as Record<string, unknown>[];
  const item = items[0] ?? {};
  const end = Number(raw.current_period_end ?? item.current_period_end ?? 0);
  return {
    id: idOf(raw.id),
    customer: idOf(raw.customer),
    status: String(raw.status ?? ""),
    price: idOf(item.price),
    periodEnd: end > 0 ? end * 1000 : 0,
    cancels: raw.cancel_at_period_end === true || typeof raw.cancel_at === "number",
  };
}

/** The real thing. Errors say only the HTTP status, never a key or Stripe's message. */
export function stripeFromKey(key: string, fetchImpl: typeof fetch = fetch): Stripe {
  async function call(method: "GET" | "POST", path: string, form?: Record<string, string>): Promise<Response> {
    return fetchImpl(`https://api.stripe.com/v1/${path}`, {
      method,
      headers: { Authorization: `Bearer ${key}`, ...(form ? { "Content-Type": "application/x-www-form-urlencoded" } : {}) },
      body: form ? new URLSearchParams(form).toString() : undefined,
    });
  }
  async function post(path: string, form: Record<string, string>, want: "id" | "url"): Promise<string> {
    const res = await call("POST", path, form);
    if (!res.ok) throw new Error(`stripe ${path}: ${res.status}`);
    const value = ((await res.json()) as Record<string, unknown>)[want];
    if (typeof value !== "string" || !value) throw new Error(`stripe ${path}: no ${want}`);
    return value;
  }
  return {
    createCustomer: (email) => post("customers", { email }, "id"),
    createCheckout: (o) =>
      post("checkout/sessions", {
        mode: "subscription",
        customer: o.customer,
        "line_items[0][price]": o.price,
        "line_items[0][quantity]": "1",
        success_url: o.successUrl,
        cancel_url: o.cancelUrl,
      }, "url"),
    createPortal: (customer, returnUrl) => post("billing_portal/sessions", { customer, return_url: returnUrl }, "url"),
    async getSubscription(id) {
      if (!/^sub_[A-Za-z0-9]+$/.test(id)) return null;
      const res = await call("GET", `subscriptions/${id}`);
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`stripe subscriptions: ${res.status}`);
      return readSubscription((await res.json()) as Record<string, unknown>);
    },
  };
}

const TOLERANCE_S = 300;

/** True only if `header` (Stripe-Signature) carries a valid signature of exactly these bytes,
 * made with the webhook secret within the last five minutes. The bytes must be the request body
 * as it arrived: parsing and re-writing the JSON would change them and break the signature. */
export async function validStripeSignature(raw: Uint8Array, header: string | null, secret: string, now = Date.now()): Promise<boolean> {
  if (!secret || !header) return false;
  let t = "";
  const given: string[] = [];
  for (const part of header.split(",")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    const name = part.slice(0, eq).trim();
    const value = part.slice(eq + 1).trim();
    if (name === "t") t = value;
    else if (name === "v1") given.push(value);
  }
  if (!/^\d{1,12}$/.test(t) || !given.length) return false;
  if (Math.abs(now / 1000 - Number(t)) > TOLERANCE_S) return false; // stale, or dated in the future
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const prefix = enc.encode(`${t}.`);
  const signed = new Uint8Array(prefix.length + raw.length);
  signed.set(prefix);
  signed.set(raw, prefix.length);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, signed));
  const want = enc.encode([...mac].map((b) => b.toString(16).padStart(2, "0")).join(""));
  let ok = false;
  for (const g of given.slice(0, 5)) if (sameBytes(enc.encode(g.toLowerCase()), want)) ok = true;
  return ok;
}
