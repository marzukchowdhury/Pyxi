// POST /api/admin/account (Bearer ADMIN_TOKEN): look an account up by email, extend its trial,
// switch it off or on, or erase its details. See cloud/README.md.
import { adminAccount } from "../../lib/accounts.js";
import { configFromEnv } from "../../lib/beta.js";
import { storeFromEnv } from "../../lib/store.js";

export async function POST(req: Request): Promise<Response> {
  return adminAccount(storeFromEnv(), configFromEnv(), req);
}
