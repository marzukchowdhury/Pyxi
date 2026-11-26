// The route files themselves (what Vercel runs): that each exists with the right method, reads
// its settings from the environment, and that the webhook checks the signature over the body
// exactly as it arrived. No store and no network: every request here is answered before either.
import { afterEach, describe, expect, it, vi } from "vitest";
import { CLIENT_ID, post, STRIPE_ENV, stripeSignature } from "./helpers.js";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function env(values: Record<string, string>) {
  for (const name of ["KV_REST_API_URL", "KV_REST_API_TOKEN", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "GOOGLE_SIGNIN_CLIENT_ID", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SITE_URL", "INVITE_CODES", "ADMIN_TOKEN"]) vi.stubEnv(name, "");
  for (const [k, v] of Object.entries(values)) vi.stubEnv(k, v);
  // A store address that is never called: any fetch at all fails the test.
  vi.stubEnv("KV_REST_API_URL", "https://store.example.com");
  vi.stubEnv("KV_REST_API_TOKEN", "store-test-token");
  vi.stubGlobal("fetch", async () => {
    throw new Error("a route test must not reach the network");
  });
}

describe("comparing secrets", () => {
  it("is right for equal, different, shorter, longer and empty values", async () => {
    const { sameBytes, sameSecret } = await import("../lib/util.js");
    expect(await sameSecret("founder-secret", "founder-secret")).toBe(true);
    for (const wrong of ["founder-secreT", "founder-secre", "founder-secret ", "", "f"]) expect(await sameSecret(wrong, "founder-secret")).toBe(false);
    expect(await sameSecret("", "")).toBe(true); // callers refuse an empty secret before comparing
    const b = (s: string) => new TextEncoder().encode(s);
    expect(sameBytes(b("abc"), b("abc"))).toBe(true);
    expect(sameBytes(b("abc"), b("abd"))).toBe(false);
    expect(sameBytes(b("abc"), b("abcd"))).toBe(false);
    expect(sameBytes(b("abc\0"), b("abc"))).toBe(false); // a shorter value is not padded into a match
    expect(sameBytes(b(""), b(""))).toBe(true);
  });
});

describe("the route files", () => {
  it("every route in the contract exists with its method", async () => {
    const routes: [string, string][] = [
      ["../api/config", "GET"],
      ["../api/auth/google", "POST"],
      ["../api/auth/signout", "POST"],
      ["../api/me", "GET"],
      ["../api/billing/checkout", "POST"],
      ["../api/billing/portal", "POST"],
      ["../api/billing/webhook", "POST"],
      ["../api/v1/chat/completions", "POST"],
      ["../api/join", "POST"],
      ["../api/admin/revoke", "POST"],
      ["../api/admin/account", "POST"],
    ];
    for (const [file, method] of routes) expect(typeof ((await import(file)) as Record<string, unknown>)[method], file).toBe("function");
  });

  it("GET /api/config reads the environment and needs no store", async () => {
    env({ GOOGLE_SIGNIN_CLIENT_ID: CLIENT_ID, INVITE_CODES: "FIREFLY" });
    const { GET, OPTIONS } = await import("../api/config");
    const res = GET();
    expect(await res.json()).toEqual({ sign_in: true, google_client_id: CLIENT_ID, invite_codes: true });
    // A page on the site may call it from the browser; no other site is named.
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("https://www.heypyxi.com");
    const pre = OPTIONS();
    expect(pre.status).toBe(204);
    expect(pre.headers.get("Access-Control-Allow-Origin")).toBe("https://www.heypyxi.com");
    expect(pre.headers.get("Access-Control-Allow-Headers")).toContain("Authorization");
    env({});
    expect(await GET().json()).toEqual({ sign_in: false, google_client_id: null, invite_codes: false });
  });

  it("sign-in and billing routes answer in the contract's shape before any store is touched", async () => {
    env({});
    const google = await import("../api/auth/google");
    const off = await google.POST(post("/api/auth/google", { id_token: "x" }));
    expect(off.status).toBe(503);
    expect(((await off.json()) as { error: { code: string } }).error.code).toBe("sign_in_off");
    expect(off.headers.get("Access-Control-Allow-Origin")).toBe("https://www.heypyxi.com");
    const hook = await import("../api/billing/webhook");
    const closed = await hook.POST(post("/api/billing/webhook", "{}"));
    expect(closed.status).toBe(503);
    expect(((await closed.json()) as { error: { code: string } }).error.code).toBe("billing_off");
    expect(closed.headers.get("Access-Control-Allow-Origin")).toBeNull(); // Stripe calls this, never a browser
  });

  it("the webhook route checks the signature over the raw body, byte for byte", async () => {
    env(STRIPE_ENV);
    const { POST } = await import("../api/billing/webhook");
    // Bytes a JSON parser would not give back unchanged: odd spacing, escapes, a non-ASCII name.
    const raw = '{ "id":"",\r\n\t"type" : "customer.created","data":{"object":{"name":"Zo\\u00eb Café"}} }';
    const bytes = new TextEncoder().encode(raw);
    const signed = (body: BodyInit, sig: string) =>
      POST(new Request("https://beta.pyxi.app/api/billing/webhook", { method: "POST", headers: { "Stripe-Signature": sig, "Content-Type": "application/json" }, body }));
    const sig = await stripeSignature(raw);
    // Right signature: it gets past the check (and is then refused for its empty id, still
    // without the store). Had the body been re-written on the way in, this would be bad_signature.
    const passed = await signed(bytes, sig);
    expect(((await passed.json()) as { error: { code: string } }).error.code).toBe("bad_event");
    const reparsed = await signed(JSON.stringify(JSON.parse(raw)), sig);
    expect(reparsed.status).toBe(400);
    expect(((await reparsed.json()) as { error: { code: string } }).error.code).toBe("bad_signature");
    const none = await POST(post("/api/billing/webhook", raw));
    expect(((await none.json()) as { error: { code: string } }).error.code).toBe("bad_signature");
  });

  it("admin routes don't exist without an admin secret", async () => {
    env({});
    const { POST } = await import("../api/admin/account");
    expect((await POST(post("/api/admin/account", { email: "anna@example.com" }, { Authorization: "Bearer guess" }))).status).toBe(404);
  });
});
