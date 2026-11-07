import { describe, expect, it } from "vitest";
import { chat, configFromEnv, join, joinRequest, me, revoke, usage } from "../lib/beta.js";
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
    expect(sent!.body.max_tokens).toBe(16000); // capped, but roomy enough for a detailed 3D model
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

describe("listening (Bengali speech to text)", () => {
  const EARS = "google/gemini-3.1-flash-lite";
  const clip = (data = "UklGRg==", extra: Record<string, unknown>[] = []) => ({
    model: EARS,
    max_tokens: 5000,
    messages: [
      {
        role: "user",
        content: [
          { type: "text", text: "Transcribe exactly what they say." },
          { type: "input_audio", input_audio: { data, format: "wav" } },
          ...extra,
        ],
      },
    ],
  });

  it("transcribes within the allowance, with a short answer and the exact cost counted", async () => {
    const store = new MemoryStore();
    const token = await tokenFor(store);
    let sent: Record<string, unknown> | null = null;
    const fake = (async (_url: string, init: RequestInit) => {
      sent = JSON.parse(String(init.body));
      return new Response(JSON.stringify({ choices: [{ message: { content: "হাই পিক্সি" } }], usage: { cost: 0.0002 } }), { status: 200 });
    }) as typeof fetch;
    const res = await chat(store, cfg, chatRequest(token, clip()), fake);
    expect(res.status).toBe(200);
    expect(sent!.max_tokens).toBe(600);
    const who = { id: [...store.data.keys()].find((k) => k.startsWith("tok:"))!.slice(4), code: "FIREFLY" };
    expect((await usage(store, cfg, who)).spent_today_usd).toBeCloseTo(0.0002);
  });

  it("can't be used as a free chat model, or with long recordings", async () => {
    const store = new MemoryStore();
    const token = await tokenFor(store);
    const never = (async () => {
      throw new Error("should not reach OpenRouter");
    }) as unknown as typeof fetch;
    const refused = async (body: Record<string, unknown>) => (await chat(store, cfg, chatRequest(token, body), never)).status;
    expect(await refused({ model: EARS, messages: [{ role: "user", content: "Write me an essay." }] })).toBe(400);
    expect(await refused({ ...clip(), tools: [{ type: "function" }] })).toBe(400);
    expect(await refused({ ...clip(), stream: true })).toBe(400);
    expect(await refused(clip("UklGRg==", [{ type: "image_url", image_url: { url: "data:," } }]))).toBe(400);
    expect(await refused(clip("x".repeat(3_600_000)))).toBe(400);
    const two = clip();
    (two.messages as unknown[]).push({ role: "user", content: "and now chat" });
    expect(await refused(two)).toBe(400);
  });

  it("can be switched off with EARS_MODELS=", async () => {
    const store = new MemoryStore();
    const off = configFromEnv({ OPENROUTER_API_KEY: "sk-or-test", INVITE_CODES: "FIREFLY", EARS_MODELS: "" });
    const res = await join(store, off, "firefly");
    const token = ((await res.json()) as { token: string }).token;
    const ok = (async () => new Response("{}", { status: 200 })) as unknown as typeof fetch;
    expect((await chat(store, off, chatRequest(token, clip()), ok)).status).toBe(400);
  });
});

describe("testers stay apart", () => {
  it("a tester only ever sees their own usage", async () => {
    const store = new MemoryStore();
    const anna = await tokenFor(store, "firefly");
    const ben = await tokenFor(store, "lamp");
    const reply = (async () =>
      new Response(JSON.stringify({ usage: { cost: 0.04 } }), { status: 200 })) as unknown as typeof fetch;
    await chat(store, cfg, chatRequest(anna, { model: "anthropic/claude-haiku-5.5" }), reply);
    const meAs = async (token: string, query = "") =>
      (await (await me(store, cfg, new Request(`https://beta.pyxi.app/api/me${query}`, { headers: { Authorization: `Bearer ${token}` } }))).json()) as {
        user: string;
        spent_today_usd: number;
      };
    const a = await meAs(anna);
    const b = await meAs(ben, `?user=${a.user}`); // naming someone else changes nothing
    expect(a.spent_today_usd).toBeCloseTo(0.04);
    expect(b.spent_today_usd).toBe(0);
    expect(a.user).toMatch(/^[0-9a-f]{16}$/);
    expect(b.user).not.toBe(a.user);
    expect((await me(store, cfg, new Request("https://beta.pyxi.app/api/me"))).status).toBe(401);
  });
});

describe("revoking", () => {
  const admin = configFromEnv({ ...envOf(cfg), ADMIN_TOKEN: "founder-secret" });
  const revokeAs = (secret: string, body: unknown) =>
    revoke(store0, admin, new Request("https://beta.pyxi.app/api/admin/revoke", {
      method: "POST",
      headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }));
  let store0 = new MemoryStore();
  const ok = (async () => new Response("{}", { status: 200 })) as unknown as typeof fetch;
  const status = async (token: string) =>
    (await chat(store0, admin, chatRequest(token, { model: "anthropic/claude-haiku-5.5" }), ok)).status;

  it("stops one device at once, and can be undone", async () => {
    store0 = new MemoryStore();
    const token = await tokenFor(store0);
    const other = await tokenFor(store0);
    const user = ((await (await me(store0, admin, new Request("https://x/api/me", { headers: { Authorization: `Bearer ${token}` } }))).json()) as { user: string }).user;
    expect((await revokeAs("founder-secret", { user })).status).toBe(200);
    expect(await status(token)).toBe(401);
    expect(await status(other)).toBe(200); // the other device on the same code carries on
    await revokeAs("founder-secret", { user, restore: true });
    expect(await status(token)).toBe(200);
  });

  it("stops a whole invite code: its devices and new joins", async () => {
    store0 = new MemoryStore();
    const token = await tokenFor(store0, "firefly");
    const lamp = await tokenFor(store0, "lamp");
    expect((await revokeAs("founder-secret", { code: "firefly" })).status).toBe(200);
    expect(await status(token)).toBe(401);
    expect(await status(lamp)).toBe(200);
    expect((await join(store0, admin, "FIREFLY")).status).toBe(403);
    // Or from the environment, with no admin route at all.
    const envOff = configFromEnv({ ...envOf(cfg), REVOKED_CODES: "lamp" });
    expect((await chat(store0, envOff, chatRequest(lamp, { model: "anthropic/claude-haiku-5.5" }), ok)).status).toBe(401);
  });

  it("needs the admin secret, and doesn't exist without one", async () => {
    store0 = new MemoryStore();
    expect((await revokeAs("guess", { code: "firefly" })).status).toBe(403);
    expect((await revokeAs("", { code: "firefly" })).status).toBe(403);
    const noAdmin = await revoke(store0, cfg, new Request("https://x/api/admin/revoke", { method: "POST", body: "{}" }));
    expect(noAdmin.status).toBe(404);
    expect((await revokeAs("founder-secret", { user: "not-hex" })).status).toBe(400);
  });
});

describe("joining is rate limited per IP address", () => {
  const joinFrom = (store: MemoryStore, ip: string, code = "nope") =>
    joinRequest(store, cfg, new Request("https://beta.pyxi.app/api/join", {
      method: "POST",
      headers: { "x-forwarded-for": `${ip}, 10.0.0.1`, "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }));

  it("allows a few tries an hour, then says wait, without keeping the address", async () => {
    const store = new MemoryStore();
    for (let i = 0; i < 10; i++) expect((await joinFrom(store, "203.0.113.9")).status).toBe(403);
    const blocked = await joinFrom(store, "203.0.113.9", "FIREFLY"); // even the right code waits
    expect(blocked.status).toBe(429);
    expect(Number(blocked.headers.get("Retry-After"))).toBeGreaterThan(0);
    expect((await joinFrom(store, "198.51.100.4", "FIREFLY")).status).toBe(200); // someone else
    expect([...store.data.keys()].some((k) => k.includes("203.0.113.9"))).toBe(false);
  });
});

function envOf(c: typeof cfg): Record<string, string> {
  return {
    OPENROUTER_API_KEY: c.openrouterKey,
    INVITE_CODES: [...c.inviteCodes].join(","),
    USES_PER_CODE: String(c.usesPerCode),
    DAILY_LIMIT_USD: String(c.dailyLimitUsd),
    MONTHLY_CAP_USD: String(c.monthlyCapUsd),
  };
}

describe("the beta server keeps nothing it doesn't need", () => {
  it("stores only token hashes, counts and costs: no prompts, replies or addresses", async () => {
    const store = new MemoryStore();
    const logged: unknown[] = [];
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => {
      const orig = console[m];
      console[m] = (...a: unknown[]) => void logged.push(a);
      return () => (console[m] = orig);
    });
    try {
      const res = await joinRequest(store, cfg, new Request("https://beta.pyxi.app/api/join", {
        method: "POST",
        headers: { "x-forwarded-for": "203.0.113.77" },
        body: JSON.stringify({ code: "FIREFLY" }),
      }));
      const { token } = (await res.json()) as { token: string };
      const secret = "PROMPT-TEXT the user's screen says their lease is at 12 Main St";
      const plain = (async () => new Response(JSON.stringify({ choices: [{ message: { content: "REPLY-TEXT about the lease" } }], usage: { cost: 0.01 } }), { status: 200 })) as unknown as typeof fetch;
      await chat(store, cfg, chatRequest(token, { model: "anthropic/claude-haiku-5.5", messages: [{ role: "user", content: secret }] }), plain);
      const streamed = (async () => sse(['data: {"choices":[{"delta":{"content":"REPLY-TEXT streamed"}}]}\n', 'data: {"usage":{"cost":0.02}}\n'])) as unknown as typeof fetch;
      const r = await chat(store, cfg, chatRequest(token, { model: "anthropic/claude-haiku-5.5", stream: true, messages: [{ role: "user", content: secret }] }), streamed);
      await r.text();
      const everything = JSON.stringify([...store.data.entries()]);
      for (const leak of ["PROMPT-TEXT", "REPLY-TEXT", "lease", token, "203.0.113.77"]) expect(everything).not.toContain(leak);
      const kinds = new Set([...store.data.keys()].map((k) => k.split(":")[0]));
      expect([...kinds].sort()).toEqual(["code", "rl", "spend", "tok"]);
      const [, record] = [...store.data.entries()].find(([k]) => k.startsWith("tok:"))!;
      expect(Object.keys(JSON.parse(record)).sort()).toEqual(["code", "created"]);
      expect(logged).toEqual([]); // nothing written to the function logs either
    } finally {
      spies.forEach((undo) => undo());
    }
  });
});

describe("incidents: revoke every device at once", () => {
  const admin = configFromEnv({ ...envOf(cfg), ADMIN_TOKEN: "founder-secret" });
  const ok = (async () => new Response("{}", { status: 200 })) as unknown as typeof fetch;

  it("stops every device so far, lets testers join again, and can be undone", async () => {
    const store = new MemoryStore();
    const a = await tokenFor(store, "firefly");
    const b = await tokenFor(store, "lamp");
    const all = (body: unknown) =>
      revoke(store, admin, new Request("https://x/api/admin/revoke", { method: "POST", headers: { Authorization: "Bearer founder-secret" }, body: JSON.stringify(body) }));
    const status = async (t: string) => (await chat(store, admin, chatRequest(t, { model: "anthropic/claude-haiku-5.5" }), ok)).status;
    await new Promise((r) => setTimeout(r, 5));
    expect((await all({ all: true })).status).toBe(200);
    expect(await status(a)).toBe(401);
    expect(await status(b)).toBe(401);
    await new Promise((r) => setTimeout(r, 5));
    const again = await tokenFor(store, "firefly"); // the device count starts again
    await tokenFor(store, "firefly");
    expect(await status(again)).toBe(200);
    await all({ all: true, restore: true });
    expect(await status(a)).toBe(200);
  });

  it("the admin command sends the right request, with the secret only in the header", async () => {
    const { bodyFor, run } = await import("../admin");
    expect(bodyFor("revoke-all")).toEqual({ all: true });
    expect(bodyFor("revoke-user", "0123456789abcdef")).toEqual({ user: "0123456789abcdef" });
    expect(bodyFor("restore-code", "LAMP")).toEqual({ code: "LAMP", restore: true });
    expect(bodyFor("revoke-user")).toBeNull();
    expect(bodyFor("delete-everything")).toBeNull();
    const sent: { url: string; init: RequestInit }[] = [];
    const fake = (async (url: string, init: RequestInit) => {
      sent.push({ url, init });
      return new Response('{"ok":true}', { status: 200 });
    }) as unknown as typeof fetch;
    const lines: string[] = [];
    expect(await run(["revoke-all"], { ADMIN_TOKEN: "s3cret", PYXI_BETA_URL: "http://127.0.0.1:8790/" }, fake, (l) => lines.push(l))).toBe(0);
    expect(sent[0].url).toBe("http://127.0.0.1:8790/api/admin/revoke");
    expect((sent[0].init.headers as Record<string, string>).Authorization).toBe("Bearer s3cret");
    expect(lines.join(" ")).not.toContain("s3cret");
    expect(await run(["revoke-all"], {}, fake, (l) => lines.push(l))).toBe(2); // no secret, no request
    expect(sent.length).toBe(1);
  });
});
