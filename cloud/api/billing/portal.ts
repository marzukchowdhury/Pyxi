// POST /api/billing/portal (Bearer) -> {"url"}: Stripe's own page to change plan or card, cancel,
// or get receipts, for the caller's own account. 409 no_subscription if they never had a plan.
import { configFromEnv } from "../../lib/beta.js";
import { portal, stripeFromConfig } from "../../lib/billing.js";
import { storeFromEnv } from "../../lib/store.js";
import { cors, preflight } from "../../lib/util.js";

export async function POST(req: Request): Promise<Response> {
  const cfg = configFromEnv();
  return cors(await portal(storeFromEnv(), cfg, req, stripeFromConfig(cfg)), cfg.siteUrl);
}

export function OPTIONS(): Response {
  return preflight(configFromEnv().siteUrl);
}
