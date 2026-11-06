// POST /api/join {"code": "..."} -> {"token": "pyxi_..."}: an invite code becomes a beta token.
import { configFromEnv, join, json } from "../lib/beta.js";
import { storeFromEnv } from "../lib/store.js";

export async function POST(req: Request): Promise<Response> {
  const body = (await req.json().catch(() => ({}))) as { code?: string };
  return join(storeFromEnv(), configFromEnv(), body.code);
}

export function GET(): Response {
  return json({ ok: true, service: "pyxi-beta" });
}
