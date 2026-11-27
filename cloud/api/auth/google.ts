// POST /api/auth/google {"id_token": "..."} -> {"token": "pyxi_...", "account": {...}}.
// The Google ID token is checked against Google's published keys (kept between requests while
// this function stays warm) and then dropped.
import { signIn } from "../../lib/accounts.js";
import { configFromEnv } from "../../lib/beta.js";
import { googleKeys } from "../../lib/google.js";
import { storeFromEnv } from "../../lib/store.js";
import { cors, preflight } from "../../lib/util.js";

const keys = googleKeys();

export async function POST(req: Request): Promise<Response> {
  const cfg = configFromEnv();
  return cors(await signIn(storeFromEnv(), cfg, req, keys), cfg.siteUrl);
}

export function OPTIONS(): Response {
  return preflight(configFromEnv().siteUrl);
}
