// GET /api/me (Bearer token) -> how much of today's allowance is left, for the caller only.
import { configFromEnv, me } from "../lib/beta.js";
import { storeFromEnv } from "../lib/store.js";

export async function GET(req: Request): Promise<Response> {
  return me(storeFromEnv(), configFromEnv(), req);
}
