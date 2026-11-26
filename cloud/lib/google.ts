// Checking a Google ID token: the signature against Google's published keys, who issued it, who
// it was issued for, that it hasn't run out, and that Google has verified the email address.
// Plain Web Crypto, no library. Nothing from the token is kept except `sub` and `email`.

/** Finds the public key with this id, or null. Tests pass their own. */
export type KeySource = (kid: string) => Promise<CryptoKey | null>;

export interface GoogleUser {
  sub: string;
  email: string;
}

const CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs";
const ISSUERS = new Set(["accounts.google.com", "https://accounts.google.com"]);
const RS256 = { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" } as const;
const SKEW_S = 60; // clocks differ a little
const MAX_TOKEN_CHARS = 8192; // a real one is under 2000

interface Jwk {
  kid?: string;
  kty?: string;
  alg?: string;
  use?: string;
  n?: string;
  e?: string;
}

/** Google's published signing keys, fetched when needed and kept for as long as Google's
 * Cache-Control says (an hour if it doesn't). An unknown key id triggers one fresh fetch (Google
 * rotates keys), but never more than once a minute, so bad tokens can't make us hammer Google. */
export function googleKeys(fetchImpl: typeof fetch = fetch, now: () => number = Date.now): KeySource {
  let keys = new Map<string, CryptoKey>();
  let fetchedAt = 0;
  let goodUntil = 0;
  let loading: Promise<void> | null = null;

  async function load(): Promise<void> {
    const res = await fetchImpl(CERTS_URL);
    if (!res.ok) throw new Error(`Google keys: ${res.status}`);
    const body = (await res.json()) as { keys?: Jwk[] };
    const next = new Map<string, CryptoKey>();
    for (const jwk of body.keys ?? []) {
      if (!jwk.kid || jwk.kty !== "RSA" || !jwk.n || !jwk.e) continue;
      if (jwk.use && jwk.use !== "sig") continue;
      if (jwk.alg && jwk.alg !== "RS256") continue;
      try {
        next.set(jwk.kid, await crypto.subtle.importKey("jwk", { kty: "RSA", n: jwk.n, e: jwk.e, alg: "RS256", ext: true }, RS256, false, ["verify"]));
      } catch {
        /* a key we can't read: skip it */
      }
    }
    if (!next.size) throw new Error("Google keys: none usable");
    const maxAge = Number(/max-age=(\d+)/.exec(res.headers.get("cache-control") ?? "")?.[1] ?? 3600);
    keys = next;
    fetchedAt = now();
    goodUntil = fetchedAt + Math.min(Math.max(maxAge, 60), 86400) * 1000;
  }

  const refresh = () => (loading ??= load().finally(() => (loading = null)));

  return async (kid) => {
    if (now() >= goodUntil) await refresh();
    let key = keys.get(kid);
    if (!key && now() - fetchedAt > 60_000) {
      await refresh();
      key = keys.get(kid);
    }
    return key ?? null;
  };
}

function b64url(text: string): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9_-]*$/.test(text)) throw new Error("not base64url");
  const bin = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** The person a Google ID token names, or null if anything about the token is wrong. Never
 * throws for a bad token; a failure to reach Google's keys also answers null. */
export async function verifyGoogleIdToken(
  idToken: unknown,
  clientId: string,
  keySource: KeySource,
  now: () => number = Date.now,
): Promise<GoogleUser | null> {
  try {
    if (!clientId || typeof idToken !== "string" || idToken.length > MAX_TOKEN_CHARS) return null;
    const parts = idToken.split(".");
    if (parts.length !== 3) return null;
    const dec = new TextDecoder();
    const header = JSON.parse(dec.decode(b64url(parts[0]))) as { alg?: unknown; kid?: unknown };
    // Only RS256: never "none", never a shared-secret algorithm.
    if (header.alg !== "RS256" || typeof header.kid !== "string" || !header.kid) return null;
    const key = await keySource(header.kid);
    if (!key) return null;
    const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
    if (!(await crypto.subtle.verify(RS256, key, b64url(parts[2]), signed))) return null;

    const claims = JSON.parse(dec.decode(b64url(parts[1]))) as Record<string, unknown>;
    const seconds = now() / 1000;
    if (typeof claims.iss !== "string" || !ISSUERS.has(claims.iss)) return null;
    if (claims.aud !== clientId) return null;
    if (typeof claims.exp !== "number" || claims.exp + SKEW_S <= seconds) return null;
    if (typeof claims.iat === "number" && claims.iat - SKEW_S > seconds) return null;
    if (typeof claims.nbf === "number" && claims.nbf - SKEW_S > seconds) return null;
    if (claims.email_verified !== true && claims.email_verified !== "true") return null;
    if (typeof claims.sub !== "string" || !/^[\w.-]{1,255}$/.test(claims.sub)) return null;
    if (typeof claims.email !== "string" || !/^[^\s@]{1,64}@[^\s@]{1,255}$/.test(claims.email)) return null;
    return { sub: claims.sub, email: claims.email.toLowerCase() };
  } catch {
    return null;
  }
}
