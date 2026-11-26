// POST /api/billing/webhook: Stripe's events, signature checked -> {"received": true}.
// This is a Web-standard handler (it gets a Request), so Vercel hands over the body untouched:
// the signature is checked over the raw bytes. Don't turn this into a (req, res) handler that
// reads req.body: that body is already parsed, and the signature would never match.
import { configFromEnv } from "../../lib/beta.js";
import { stripeFromConfig, webhook } from "../../lib/billing.js";
import { storeFromEnv } from "../../lib/store.js";

export async function POST(req: Request): Promise<Response> {
  const cfg = configFromEnv();
  return webhook(storeFromEnv(), cfg, req, stripeFromConfig(cfg));
}
