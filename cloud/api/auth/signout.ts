// POST /api/auth/signout (Bearer token) -> {"ok": true}: this device's token stops working.
import { signOut } from "../../lib/accounts.js";
import { configFromEnv } from "../../lib/beta.js";
import { storeFromEnv } from "../../lib/store.js";
import { cors, preflight } from "../../lib/util.js";

export async function POST(req: Request): Promise<Response> {
  return cors(await signOut(storeFromEnv(), req), configFromEnv().siteUrl);
}

export function OPTIONS(): Response {
  return preflight(configFromEnv().siteUrl);
}
