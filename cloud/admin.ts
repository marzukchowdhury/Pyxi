// The founder's admin command for the beta server: stop devices at once (an incident, a leaked
// token, a tester who left). Needs ADMIN_TOKEN, the same secret set in Vercel.
//
//   ADMIN_TOKEN=... node admin.ts revoke-all              every device that has joined so far
//   ADMIN_TOKEN=... node admin.ts revoke-user 0123456789abcdef
//   ADMIN_TOKEN=... node admin.ts revoke-code FIREFLY-ANNA
//   ADMIN_TOKEN=... node admin.ts restore-all | restore-user <id> | restore-code <CODE>
//
// PYXI_BETA_URL picks the server (default https://pyxi-beta.vercel.app). The secret is sent only
// in the Authorization header, never printed.
import { pathToFileURL } from "node:url";

export const USAGE =
  "usage: node admin.ts revoke-all | revoke-user <id> | revoke-code <CODE> | restore-all | restore-user <id> | restore-code <CODE>";

/** The admin route's body for a command, or null if the command isn't one. */
export function bodyFor(command: string, arg = ""): Record<string, unknown> | null {
  const [verb, what] = command.split("-");
  if ((verb !== "revoke" && verb !== "restore") || !["all", "user", "code"].includes(what)) return null;
  if (what !== "all" && !arg.trim()) return null;
  const body: Record<string, unknown> = what === "all" ? { all: true } : { [what]: arg.trim() };
  if (verb === "restore") body.restore = true;
  return body;
}

export async function run(
  argv: string[],
  env: Record<string, string | undefined>,
  fetchImpl: typeof fetch = fetch,
  out: (line: string) => void = console.log,
): Promise<number> {
  const [command = "", arg = ""] = argv;
  const body = bodyFor(command, arg);
  if (!body) {
    out(USAGE);
    return 2;
  }
  const token = env.ADMIN_TOKEN ?? "";
  if (!token) {
    out("Set ADMIN_TOKEN first (the same secret as in Vercel).");
    return 2;
  }
  const url = (env.PYXI_BETA_URL ?? "https://pyxi-beta.vercel.app").replace(/\/$/, "");
  const res = await fetchImpl(`${url}/api/admin/revoke`, {
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
