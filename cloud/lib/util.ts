// Small shared pieces: hashing, tokens, JSON answers, the caller's address and per-address limits.

import type { Store } from "./store.js";

const hex = (bytes: Uint8Array) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function sha256(text: string): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))));
}

export function randomToken(): string {
  return "pyxi_" + hex(crypto.getRandomValues(new Uint8Array(24)));
}

/** Compare two byte strings without stopping at the first difference. */
export function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  let diff = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  return diff === 0;
}

/** Compare two secrets in constant time: both are hashed first, so neither the length nor the
 * place of the first wrong character shows in how long it takes. */
export async function sameSecret(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  return sameBytes(new Uint8Array(ha), new Uint8Array(hb));
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** The contract's error shape: {"error": {"code", "message"}}, the message in plain words. */
export function fail(status: number, code: string, message: string): Response {
  return json({ error: { code, message } }, status);
}

/** The caller's address as Vercel's edge saw it (the edge sets these headers itself). */
export function clientIp(req: Request): string {
  return (
    req.headers.get("x-real-ip") ??
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown"
  );
}

/** The Bearer value of a request ("" if none). */
export function bearer(req: Request): string {
  return (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
}

/** Count one try from the caller's address this hour. Returns the seconds to wait when they are
 * over `max`, else 0. Only a hash of the address is kept, and only for that hour. */
export async function overLimit(store: Store, kind: string, req: Request, max: number): Promise<number> {
  const ip = (await sha256(clientIp(req))).slice(0, 24);
  const hour = Math.floor(Date.now() / 3_600_000);
  const tries = await store.incr(`rl:${kind}:${ip}:${hour}`, 1, 3600);
  return tries > max ? 3600 - Math.floor((Date.now() % 3_600_000) / 1000) : 0;
}

/** Let a page on the site itself (and only there) call a route from the browser. Sign-in is a
 * Bearer token the page holds, never a cookie, so another site can't ride on it. */
export function cors(res: Response, siteUrl: string): Response {
  res.headers.set("Access-Control-Allow-Origin", siteUrl);
  res.headers.set("Vary", "Origin");
  return res;
}

/** The answer to a browser's OPTIONS question before such a call. */
export function preflight(siteUrl: string): Response {
  const res = new Response(null, { status: 204 });
  res.headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.headers.set("Access-Control-Allow-Headers", "Authorization, Content-Type");
  res.headers.set("Access-Control-Max-Age", "600");
  return cors(res, siteUrl);
}

export function tooMany(wait: number): Response {
  const res = fail(429, "too_many_tries", "Too many tries. Wait a while, then try again.");
  res.headers.set("Retry-After", String(wait));
  return res;
}
