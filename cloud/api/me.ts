// GET /api/me (Bearer token) -> how much of today's allowance is left.
import { configFromEnv, identify, json, usage } from "../lib/beta.js";
import { storeFromEnv } from "../lib/store.js";

export async function GET(req: Request): Promise<Response> {
  const store = storeFromEnv();
  const who = await identify(store, req);
  if (!who) return json({ error: "not signed in" }, 401);
  return json(await usage(store, configFromEnv(), who));
}
