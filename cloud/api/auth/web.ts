// POST /api/auth/web {"id_token": "..."} -> {"token": "pyxi_...", "account": {...}}: the website's
// account page. The token lasts an hour and only reads the account and opens Stripe's pages.
import { signInWeb } from "../../lib/accounts.js";
import { configFromEnv } from "../../lib/beta.js";
import { googleKeys } from "../../lib/google.js";
import { storeFromEnv } from "../../lib/store.js";
import { cors, preflight } from "../../lib/util.js";

const keys = googleKeys();

export async function POST(req: Request): Promise<Response> {
  const cfg = configFromEnv();
  return cors(await signInWeb(storeFromEnv(), cfg, req, keys), cfg.siteUrl);
}

export function OPTIONS(): Response {
  return preflight(configFromEnv().siteUrl);
}
