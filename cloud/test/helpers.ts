// Test stand-ins: a "Google" whose signing key is made here, and a "Stripe" that lives in memory.
// Nothing in the tests touches the network.
import { expect } from "vitest";
import { configFromEnv } from "../lib/beta.js";
import type { KeySource } from "../lib/google.js";
import type { MemoryStore } from "../lib/store.js";
import type { Stripe, Subscription } from "../lib/stripe.js";

export const CLIENT_ID = "1234-test.apps.googleusercontent.com";
export const WEBHOOK_SECRET = "whsec_test_only";

export const ENV = {
  OPENROUTER_API_KEY: "sk-or-test",
  INVITE_CODES: "FIREFLY",
  MONTHLY_CAP_USD: "50",
  GOOGLE_SIGNIN_CLIENT_ID: CLIENT_ID,
};
export const STRIPE_ENV = {
  ...ENV,
  STRIPE_SECRET_KEY: "sk_test_only",
  STRIPE_WEBHOOK_SECRET: WEBHOOK_SECRET,
  STRIPE_PRICE_STARTER: "price_starter_m",
  STRIPE_PRICE_STARTER_YEARLY: "price_starter_y",
  STRIPE_PRICE_PRO: "price_pro_m",
  STRIPE_PRICE_PRO_YEARLY: "price_pro_y",
  STRIPE_PRICE_MAX: "price_max_m",
  STRIPE_PRICE_MAX_YEARLY: "price_max_y",
};
export const cfg = configFromEnv(ENV);
export const paidCfg = configFromEnv(STRIPE_ENV);

const b64url = (bytes: Uint8Array) => Buffer.from(bytes).toString("base64url");
const enc = new TextEncoder();
const RS256 = { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" } as const;

export interface Signer {
  kid: string;
  keys: KeySource;
  jwk: JsonWebKey;
  /** A signed ID token; `claims` and `header` override the good defaults. */
  token(claims?: Record<string, unknown>, header?: Record<string, unknown>): Promise<string>;
}

/** A pretend Google: its own key pair, and a key source that only knows that key. */
export async function signer(kid = "test-key-1"): Promise<Signer> {
  const pair = await crypto.subtle.generateKey({ ...RS256, modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) }, true, ["sign", "verify"]);
  const jwk = { ...(await crypto.subtle.exportKey("jwk", pair.publicKey)), kid, use: "sig", alg: "RS256" };
  return {
    kid,
    jwk,
    keys: async (k) => (k === kid ? pair.publicKey : null),
    async token(claims = {}, header = {}) {
      const now = Math.floor(Date.now() / 1000);
      const h = b64url(enc.encode(JSON.stringify({ alg: "RS256", kid, typ: "JWT", ...header })));
      const p = b64url(enc.encode(JSON.stringify({
        iss: "https://accounts.google.com",
        aud: CLIENT_ID,
        sub: "108000000000000000001",
        email: "anna@example.com",
        email_verified: true,
        iat: now - 5,
        exp: now + 3600,
        ...claims,
      })));
      const sig = new Uint8Array(await crypto.subtle.sign(RS256, pair.privateKey, enc.encode(`${h}.${p}`)));
      return `${h}.${p}.${b64url(sig)}`;
    },
  };
}

export function post(path: string, body: unknown, headers: Record<string, string> = {}): Request {
  return new Request(`https://beta.pyxi.app${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

export const as = (token: string, extra: Record<string, string> = {}) => ({ Authorization: `Bearer ${token}`, ...extra });

export async function errorOf(res: Response): Promise<{ code: string; message: string }> {
  return ((await res.json()) as { error: { code: string; message: string } }).error;
}

/** Stripe in memory: `subs` is what "Stripe" would answer right now. */
export class FakeStripe implements Stripe {
  subs = new Map<string, Subscription>();
  customers: string[] = [];
  checkouts: { customer: string; price: string; successUrl: string; cancelUrl: string }[] = [];
  portals: { customer: string; returnUrl: string }[] = [];
  lookups = 0;
  down = false;
  async createCustomer(email: string) {
    if (this.down) throw new Error("stripe down");
    this.customers.push(email);
    return `cus_test${this.customers.length}`;
  }
  async createCheckout(o: { customer: string; price: string; successUrl: string; cancelUrl: string }) {
    if (this.down) throw new Error("stripe down");
    this.checkouts.push(o);
    return `https://checkout.stripe.com/c/pay/cs_test_${this.checkouts.length}`;
  }
  async createPortal(customer: string, returnUrl: string) {
    if (this.down) throw new Error("stripe down");
    this.portals.push({ customer, returnUrl });
    return "https://billing.stripe.com/p/session/test_1";
  }
  async getSubscription(id: string) {
    this.lookups += 1;
    if (this.down) throw new Error("stripe down");
    return this.subs.get(id) ?? null;
  }
  /** Say what a subscription is now. */
  put(id: string, customer: string, status: string, price: string, periodEnd = Date.now() + 30 * 86_400_000, cancels = false) {
    this.subs.set(id, { id, customer, status, price, periodEnd, cancels });
  }
}

export async function stripeSignature(body: string, secret = WEBHOOK_SECRET, at = Date.now()): Promise<string> {
  const t = Math.floor(at / 1000);
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(`${t}.${body}`)));
  return `t=${t},v1=${Buffer.from(mac).toString("hex")}`;
}

let eventNo = 0;
/** A signed webhook request carrying one Stripe event. */
export async function stripeEvent(type: string, object: Record<string, unknown>, id = `evt_test_${++eventNo}`): Promise<Request> {
  const body = JSON.stringify({ id, type, data: { object } });
  return post("/api/billing/webhook", body, { "Stripe-Signature": await stripeSignature(body) });
}

/** The stored account record, straight from the store. */
export function stored(store: MemoryStore, sub = "108000000000000000001"): Record<string, unknown> {
  const raw = store.data.get(`acct:${sub}`);
  expect(raw).toBeTruthy();
  return JSON.parse(raw!) as Record<string, unknown>;
}
