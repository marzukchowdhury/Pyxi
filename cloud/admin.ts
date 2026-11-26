// The founder's admin command for the server: stop devices at once (an incident, a leaked
// token, a tester who left), and look after accounts. Needs ADMIN_TOKEN, the same secret set in
// Vercel.
//
//   ADMIN_TOKEN=... node admin.ts revoke-all              every device that has joined so far
//   ADMIN_TOKEN=... node admin.ts revoke-user 0123456789abcdef
//   ADMIN_TOKEN=... node admin.ts revoke-code FIREFLY-ANNA
//   ADMIN_TOKEN=... node admin.ts restore-all | restore-user <id> | restore-code <CODE>
//
//   ADMIN_TOKEN=... node admin.ts account anna@example.com          plan, status, spend
//   ADMIN_TOKEN=... node admin.ts extend-trial anna@example.com 7   seven more days of trial
//   ADMIN_TOKEN=... node admin.ts revoke-account anna@example.com   off on every device
//   ADMIN_TOKEN=... node admin.ts restore-account anna@example.com  back on
//   ADMIN_TOKEN=... node admin.ts erase-account anna@example.com    remove their details
//
// PYXI_BETA_URL picks the server (default https://api.heypyxi.com). The secret is sent only
// in the Authorization header, never printed.
import { pathToFileURL } from "node:url";

export const USAGE =
  "usage: node admin.ts revoke-all | revoke-user <id> | revoke-code <CODE> | restore-all | restore-user <id> | restore-code <CODE>\n" +
  "       node admin.ts account <email> | extend-trial <email> <days> | revoke-account <email> | restore-account <email> | erase-account <email>";

/** The revoke route's body for a command, or null if the command isn't one. */
export function bodyFor(command: string, arg = ""): Record<string, unknown> | null {
  const [verb, what] = command.split("-");
  if ((verb !== "revoke" && verb !== "restore") || !["all", "user", "code"].includes(what)) return null;
  if (what !== "all" && !arg.trim()) return null;
  const body: Record<string, unknown> = what === "all" ? { all: true } : { [what]: arg.trim() };
  if (verb === "restore") body.restore = true;
  return body;
}

/** The account route's body for a command, or null if the command isn't one. */
export function accountBodyFor(command: string, email = "", extra = ""): Record<string, unknown> | null {
  const who = email.trim();
  if (!who.includes("@")) return null;
  switch (command) {
    case "account":
      return { email: who };
    case "extend-trial": {
      const days = Number(extra);
      return Number.isInteger(days) && days >= 1 && days <= 365 ? { email: who, extend_trial_days: days } : null;
    }
    case "revoke-account":
      return { email: who, revoke: true };
    case "restore-account":
      return { email: who, restore: true };
    case "erase-account":
      return { email: who, erase: true };
    default:
      return null;
  }
}

export async function run(
  argv: string[],
  env: Record<string, string | undefined>,
  fetchImpl: typeof fetch = fetch,
  out: (line: string) => void = console.log,
): Promise<number> {
  const [command = "", arg = "", extra = ""] = argv;
  const account = accountBodyFor(command, arg, extra);
  const body = account ?? bodyFor(command, arg);
  if (!body) {
    out(USAGE);
    return 2;
  }
  const token = env.ADMIN_TOKEN ?? "";
  if (!token) {
    out("Set ADMIN_TOKEN first (the same secret as in Vercel).");
    return 2;
  }
  const url = (env.PYXI_BETA_URL ?? "https://api.heypyxi.com").replace(/\/$/, "");
  const res = await fetchImpl(`${url}/api/admin/${account ? "account" : "revoke"}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  out(`${res.status} ${await res.text()}`);
  return res.ok ? 0 : 1;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  process.exit(await run(process.argv.slice(2), process.env));
}
