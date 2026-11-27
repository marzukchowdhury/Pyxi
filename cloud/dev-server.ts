// Run the server on this PC, for testing before deploying: node dev-server.ts
// The model key comes from ../.env (OPENROUTER_API_KEY), invite codes from INVITE_CODES
// (default LOCALTEST). Sign-in and plans work when their settings are in the environment (see
// README, "Going on sale"). Data lives in memory and is gone when it stops.
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { registerHooks } from "node:module";

// The server's files import each other as "./x.js" (what Vercel's build wants); here Node runs
// the .ts files directly, so point those imports at the .ts file.
registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (err) {
      if (specifier.startsWith(".") && specifier.endsWith(".js")) return next(specifier.slice(0, -3) + ".ts", context);
      throw err;
    }
  },
});
const { chat, configFromEnv, joinRequest, json, me, revoke } = await import("./lib/beta.ts");
const { adminAccount, config, signIn, signInWeb, signOut } = await import("./lib/accounts.ts");
const { checkout, portal, stripeFromConfig, webhook } = await import("./lib/billing.ts");
const { googleKeys } = await import("./lib/google.ts");
const { MemoryStore } = await import("./lib/store.ts");

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
const stripe = stripeFromConfig(cfg);
const keys = googleKeys();
const port = Number(env.PORT ?? 8790);

createServer(async (req, res) => {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const request = new Request(`http://127.0.0.1:${port}${req.url}`, {
    method: req.method,
    headers: req.headers as Record<string, string>,
    body: req.method === "GET" ? undefined : Buffer.concat(chunks),
  });
  const post = req.method === "POST";
  let response: Response;
  if (req.url === "/api/join" && post) {
    response = await joinRequest(store, cfg, request);
  } else if (req.url === "/api/me") {
    response = await me(store, cfg, request);
  } else if (req.url === "/api/config") {
    response = config(cfg);
  } else if (req.url === "/api/auth/google" && post) {
    response = await signIn(store, cfg, request, keys);
  } else if (req.url === "/api/auth/web" && post) {
    response = await signInWeb(store, cfg, request, keys);
  } else if (req.url === "/api/auth/signout" && post) {
    response = await signOut(store, request);
  } else if (req.url === "/api/billing/checkout" && post) {
    response = await checkout(store, cfg, request, stripe);
  } else if (req.url === "/api/billing/portal" && post) {
    response = await portal(store, cfg, request, stripe);
  } else if (req.url === "/api/billing/webhook" && post) {
    response = await webhook(store, cfg, request, stripe);
  } else if (req.url === "/api/admin/revoke" && post) {
    response = await revoke(store, cfg, request);
  } else if (req.url === "/api/admin/account" && post) {
    response = await adminAccount(store, cfg, request);
  } else if (req.url === "/api/v1/chat/completions" && post) {
    response = await chat(store, cfg, request);
  } else {
    response = json({ ok: true, service: "pyxi-beta (local)" });
  }
  console.log(`${req.method} ${req.url} -> ${response.status}`);
  res.writeHead(response.status, Object.fromEntries(response.headers));
  if (response.body) for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>) res.write(chunk);
  res.end();
}).listen(port, "127.0.0.1", () =>
  console.log(
    `pyxi beta (local) on http://127.0.0.1:${port}, key ${cfg.openrouterKey ? "loaded" : "MISSING"}, ` +
      `sign-in ${cfg.googleClientId ? "on" : "off"}, plans ${stripe ? "on" : "off"}`,
  ),
);
