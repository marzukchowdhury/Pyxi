// POST /api/join {"code": "..."} -> {"token": "pyxi_..."}: an invite code becomes a beta token.
// A few tries per IP address an hour, so invite codes can't be guessed.
import { configFromEnv, joinRequest, json } from "../lib/beta.js";
import { storeFromEnv } from "../lib/store.js";

export async function POST(req: Request): Promise<Response> {
  return joinRequest(storeFromEnv(), configFromEnv(), req);
}

export function GET(): Response {
  return json({ ok: true, service: "pyxi-beta" });
}
