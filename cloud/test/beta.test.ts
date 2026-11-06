import { describe, expect, it } from "vitest";
import { chat, configFromEnv, join, usage } from "../lib/beta.js";
import { MemoryStore } from "../lib/store.js";

const cfg = configFromEnv({
  OPENROUTER_API_KEY: "sk-or-test",
  INVITE_CODES: "FIREFLY,LAMP",
  USES_PER_CODE: "2",
  DAILY_LIMIT_USD: "0.10",
  MONTHLY_CAP_USD: "5",
});

async function tokenFor(store: MemoryStore, code = "firefly"): Promise<string> {
  const res = await join(store, cfg, code);
  expect(res.status).toBe(200);
  return ((await res.json()) as { token: string }).token;
}

function chatRequest(token: string, body: Record<string, unknown>): Request {
  return new Request("https://beta.pyxi.app/api/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function sse(lines: string[]): Response {
  const enc = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(c) {
        for (const l of lines) c.enqueue(enc.encode(l));
        c.close();
      },
    }),
    { status: 200 },
  );
}

describe("invite codes", () => {
  it("turn into tokens, a few devices per code, and nothing else gets in", async () => {
    const store = new MemoryStore();
    const token = await tokenFor(store);
    expect(token).toMatch(/^pyxi_[0-9a-f]{48}$/);
    await tokenFor(store, " FIREFLY ");
    expect((await join(store, cfg, "firefly")).status).toBe(403); // third device
    expect((await join(store, cfg, "nope")).status).toBe(403);
    // Only a hash of the token is stored.
    expect([...store.data.keys()].some((k) => k.includes(token))).toBe(false);
  });
});

describe("the proxy", () => {
  it("passes the stream through, adds the founder's key and counts the exact cost", async () => {
    const store = new MemoryStore();
    const token = await tokenFor(store);
    let sent: { headers: Headers; body: Record<string, unknown> } | null = null;
    const fake = (async (_url: string, init: RequestInit) => {
      sent = { headers: new Headers(init.headers), body: JSON.parse(String(init.body)) };
      return sse([
        'data: {"choices":[{"delta":{"content":"On it."}}]}\n\n',
        'data: {"choices":[],"usage":{"prompt_tokens":900,"cost":0.0031}}\n\n',
        "data: [DONE]\n\n",
      ]);
    }) as typeof fetch;
    const res = await chat(store, cfg, chatRequest(token, { model: "anthropic/claude-haiku-5.5", stream: true, max_tokens: 99999 }), fake);
    const text = await res.text();
    expect(text).toContain("On it.");
    expect(sent!.headers.get("authorization")).toBe("Bearer sk-or-test");
    expect(sent!.body.max_tokens).toBe(8000);
    expect(sent!.body.usage).toEqual({ include: true });
    const who = { id: [...store.data.keys()].find((k) => k.startsWith("tok:"))!.slice(4), code: "FIREFLY" };
    expect((await usage(store, cfg, who)).spent_today_usd).toBeCloseTo(0.0031);
  });

  it("stops at the daily allowance and refuses models outside the beta", async () => {
    const store = new MemoryStore();
    const token = await tokenFor(store);
    const pricey = (async () =>
      new Response(JSON.stringify({ choices: [], usage: { cost: 0.2 } }), { status: 200 })) as unknown as typeof fetch;
    expect((await chat(store, cfg, chatRequest(token, { model: "openai/gpt-9" }), pricey)).status).toBe(400);
    expect((await chat(store, cfg, chatRequest(token, { model: "anthropic/claude-haiku-5.5" }), pricey)).status).toBe(200);
    const over = await chat(store, cfg, chatRequest(token, { model: "anthropic/claude-haiku-5.5" }), pricey);
    expect(over.status).toBe(429);
    expect(JSON.stringify(await over.json())).toContain("allowance");
  });

  it("stops everyone at the monthly cap, and strangers can't use it", async () => {
    const store = new MemoryStore();
    const token = await tokenFor(store);
    const month = new Date().toISOString().slice(0, 7);
    await store.set(`spend:all:${month}`, "5");
    const ok = (async () => new Response("{}", { status: 200 })) as unknown as typeof fetch;
    expect((await chat(store, cfg, chatRequest(token, { model: "anthropic/claude-haiku-5.5" }), ok)).status).toBe(429);
    expect((await chat(store, cfg, chatRequest("pyxi_" + "0".repeat(48), { model: "anthropic/claude-haiku-5.5" }), ok)).status).toBe(401);
  });
});
