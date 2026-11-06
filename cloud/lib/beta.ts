// The Pyxi beta server: invite codes in, a token out, and an OpenRouter proxy that keeps every
// user inside a daily allowance and the whole beta inside a monthly cap. The founder's
// OpenRouter key lives only here (an environment variable), never on anyone's PC.

import type { Store } from "./store.js";

export interface Config {
  openrouterKey: string;
  inviteCodes: Set<string>;
  usesPerCode: number;
  dailyLimitUsd: number;
  monthlyCapUsd: number;
  models: Set<string>;
  maxTokens: number;
}

const DEFAULT_MODELS = [
  "anthropic/claude-haiku-5.5",
  "anthropic/claude-sonnet-5.5",
];

export function configFromEnv(env: Record<string, string | undefined> = process.env): Config {
  const list = (v: string | undefined) => (v ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  return {
    openrouterKey: env.OPENROUTER_API_KEY ?? "",
    inviteCodes: new Set(list(env.INVITE_CODES).map((c) => c.toUpperCase())),
    usesPerCode: Number(env.USES_PER_CODE ?? 3),
    dailyLimitUsd: Number(env.DAILY_LIMIT_USD ?? 0.25),
    monthlyCapUsd: Number(env.MONTHLY_CAP_USD ?? 20),
    models: new Set(list(env.ALLOWED_MODELS).length ? list(env.ALLOWED_MODELS) : DEFAULT_MODELS),
    maxTokens: Number(env.MAX_TOKENS ?? 16000), // detailed 3D models ask for 12000
  };
}

const day = (d = new Date()) => d.toISOString().slice(0, 10);
const month = (d = new Date()) => d.toISOString().slice(0, 7);
const DAY_TTL = 3 * 86400;
const MONTH_TTL = 40 * 86400;

async function sha256(text: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function randomToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return "pyxi_" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Swap an invite code for a token (each code works on a few devices). */
export async function join(store: Store, cfg: Config, code: unknown): Promise<Response> {
  const c = String(code ?? "").trim().toUpperCase();
  if (!c || !cfg.inviteCodes.has(c)) return json({ error: "That invite code isn't valid." }, 403);
  const uses = await store.incr(`code:${c}`, 1);
  if (uses > cfg.usesPerCode) return json({ error: "That invite code has been used on too many devices." }, 403);
  const token = randomToken();
  await store.set(`tok:${await sha256(token)}`, JSON.stringify({ code: c, created: Date.now() }));
  return json({ token, daily_limit_usd: cfg.dailyLimitUsd });
}

export interface Who {
  id: string;
  code: string;
}

/** Who is calling, from "Authorization: Bearer pyxi_...". */
export async function identify(store: Store, req: Request): Promise<Who | null> {
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.replace(/^Bearer\s+/i, "").trim();
  if (!token.startsWith("pyxi_")) return null;
  const id = await sha256(token);
  const raw = await store.get(`tok:${id}`);
  if (!raw) return null;
  return { id, code: (JSON.parse(raw) as { code: string }).code };
}

export async function usage(store: Store, cfg: Config, who: Who) {
  const today = Number((await store.get(`spend:${who.id}:${day()}`)) ?? 0);
  return { spent_today_usd: today, daily_limit_usd: cfg.dailyLimitUsd, left_today_usd: Math.max(0, cfg.dailyLimitUsd - today) };
}

async function record(store: Store, who: Who, cost: number): Promise<void> {
  if (!(cost > 0)) return;
  await store.incr(`spend:${who.id}:${day()}`, cost, DAY_TTL);
  await store.incr(`spend:all:${month()}`, cost, MONTH_TTL);
}

/** OpenAI-style chat completions, passed through to OpenRouter with the beta's limits. */
export async function chat(
  store: Store,
  cfg: Config,
  req: Request,
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const who = await identify(store, req);
  if (!who) return json({ error: { message: "Sign in to the Pyxi beta first (Settings, invite code)." } }, 401);
  if (!cfg.openrouterKey) return json({ error: { message: "The beta server has no model key set." } }, 503);
  const mine = await usage(store, cfg, who);
  if (mine.left_today_usd <= 0)
    return json({ error: { message: "You've used today's Pyxi beta allowance. It resets at midnight UTC." } }, 429);
  const all = Number((await store.get(`spend:all:${month()}`)) ?? 0);
  if (all >= cfg.monthlyCapUsd)
    return json({ error: { message: "The Pyxi beta is at its limit for this month. Back soon." } }, 429);

  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return json({ error: { message: "Bad request body." } }, 400);
  }
  if (!cfg.models.has(String(body.model))) return json({ error: { message: `Model ${String(body.model)} isn't part of the beta.` } }, 400);
  body.max_tokens = Math.min(Number(body.max_tokens ?? cfg.maxTokens), cfg.maxTokens);
  body.usage = { include: true }; // OpenRouter reports the exact cost, which is what gets counted

  const upstream = await fetchImpl("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${cfg.openrouterKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "https://pyxi.app",
      "X-Title": "Pyxi beta",
      "X-User": who.id.slice(0, 16), // per-user abuse tracking on OpenRouter's side, no identity
    },
    body: JSON.stringify(body),
  });
  if (!upstream.body || !body.stream) {
    const text = await upstream.text();
    try {
      await record(store, who, Number((JSON.parse(text) as { usage?: { cost?: number } }).usage?.cost ?? 0));
    } catch {
      /* not JSON: an upstream error, nothing to count */
    }
    return new Response(text, { status: upstream.status, headers: { "Content-Type": "application/json" } });
  }

  // Streaming: pass every byte straight through, and read the cost from the final usage chunk.
  let tail = "";
  let cost = 0;
  const decoder = new TextDecoder();
  const counted = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, ctrl) {
      ctrl.enqueue(chunk);
      tail += decoder.decode(chunk, { stream: true });
      const lines = tail.split("\n");
      tail = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data:") || !line.includes('"usage"')) continue;
        try {
          const c = (JSON.parse(line.slice(5)) as { usage?: { cost?: number } }).usage?.cost;
          if (typeof c === "number") cost = c;
        } catch {
          /* a partial or keep-alive line */
        }
      }
    },
    async flush() {
      await record(store, who, cost);
    },
  });
  return new Response(upstream.body.pipeThrough(counted), {
    status: upstream.status,
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
  });
}
