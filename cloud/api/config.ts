// GET /api/config -> {"sign_in", "google_client_id", "google_web_client_id", "invite_codes"}: what the app should offer.
import { config } from "../lib/accounts.js";
import { configFromEnv } from "../lib/beta.js";
import { cors, preflight } from "../lib/util.js";

export function GET(): Response {
  const cfg = configFromEnv();
  return cors(config(cfg), cfg.siteUrl);
}

export function OPTIONS(): Response {
  return preflight(configFromEnv().siteUrl);
}
