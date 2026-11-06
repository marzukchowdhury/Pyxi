// Run the beta server on this PC, for testing before deploying: node dev-server.ts
// The model key comes from ../.env (OPENROUTER_API_KEY), invite codes from INVITE_CODES
// (default LOCALTEST). Data lives in memory and is gone when it stops.
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { chat, configFromEnv, identify, join, json, usage } from "./lib/beta.ts";
import { MemoryStore } from "./lib/store.ts";

const env: Record<string, string | undefined> = { ...process.env };
try {
  for (const line of readFileSync(new URL("../.env", import.meta.url), "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && !env[m[1]]) env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
} catch {
  /* no .env: the key must be in the environment */
}
env.INVITE_CODES ??= "LOCALTEST";
const cfg = configFromEnv(env);
const store = new MemoryStore();
const port = Number(env.PORT ?? 8790);

createServer(async (req, res) => {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const request = new Request(`http://127.0.0.1:${port}${req.url}`, {
    method: req.method,
    headers: req.headers as Record<string, string>,
    body: req.method === "GET" ? undefined : Buffer.concat(chunks),
  });
  let response: Response;
  if (req.url === "/api/join" && req.method === "POST") {
    response = await join(store, cfg, ((await request.json().catch(() => ({}))) as { code?: string }).code);
  } else if (req.url === "/api/me") {
    const who = await identify(store, request);
    response = who ? json(await usage(store, cfg, who)) : json({ error: "not signed in" }, 401);
  } else if (req.url === "/api/v1/chat/completions" && req.method === "POST") {
    response = await chat(store, cfg, request);
  } else {
    response = json({ ok: true, service: "pyxi-beta (local)" });
  }
  console.log(`${req.method} ${req.url} -> ${response.status}`);
  res.writeHead(response.status, Object.fromEntries(response.headers));
  if (response.body) for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>) res.write(chunk);
  res.end();
}).listen(port, "127.0.0.1", () => console.log(`pyxi beta (local) on http://127.0.0.1:${port}, key ${cfg.openrouterKey ? "loaded" : "MISSING"}`));
