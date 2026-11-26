// POST /api/billing/checkout (Bearer) {"plan": "starter"|"pro"|"max", "yearly": bool} -> {"url"}:
// a Stripe Checkout page for the caller's own account. 503 billing_off until Stripe is set up.
import { configFromEnv } from "../../lib/beta.js";
import { checkout, stripeFromConfig } from "../../lib/billing.js";
import { storeFromEnv } from "../../lib/store.js";
import { cors, preflight } from "../../lib/util.js";

export async function POST(req: Request): Promise<Response> {
  const cfg = configFromEnv();
  return cors(await checkout(storeFromEnv(), cfg, req, stripeFromConfig(cfg)), cfg.siteUrl);
}

export function OPTIONS(): Response {
  return preflight(configFromEnv().siteUrl);
}
