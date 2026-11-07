// POST /api/admin/revoke (Bearer ADMIN_TOKEN): stop one device or one invite code at once.
// {"user": "<16 hex>"} or {"code": "..."}, plus "restore": true to undo. See cloud/README.md.
import { configFromEnv, revoke } from "../../lib/beta.js";
import { storeFromEnv } from "../../lib/store.js";

export async function POST(req: Request): Promise<Response> {
  return revoke(storeFromEnv(), configFromEnv(), req);
}
