// GET /api/me (Bearer token) -> today's figures and the account, for the caller only.
import { configFromEnv, me } from "../lib/beta.js";
import { storeFromEnv } from "../lib/store.js";
import { cors, preflight } from "../lib/util.js";

export async function GET(req: Request): Promise<Response> {
  const cfg = configFromEnv();
  return cors(await me(storeFromEnv(), cfg, req), cfg.siteUrl);
}

export function OPTIONS(): Response {
  return preflight(configFromEnv().siteUrl);
}
