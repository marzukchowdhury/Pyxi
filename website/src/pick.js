/**
 * The plan someone pressed on the pricing page, kept in this browser only (localStorage), so it
 * can follow them into the app: at the end of the Google sign-in the app sends the browser to
 * /signed-in, which hands the pick back to the app on their own PC (signed-in.html). The site
 * never learns who they are, and nothing about the pick is sent to a server.
 */
const KEY = "pyxi.pick";
const KEEP_MS = 30 * 86_400_000; // a pick older than a month is no longer what they meant
export const PLAN_NAMES = { starter: "Starter", pro: "Pro", max: "Max" };

export function savePick(plan, yearly) {
  if (!(plan in PLAN_NAMES)) return;
  try {
    localStorage.setItem(KEY, JSON.stringify({ plan, yearly: !!yearly, at: Date.now() }));
  } catch {
    // storage is off (private window, a strict browser): the app simply asks later, as before
  }
}

/** { plan, yearly } or null. */
export function readPick(now = Date.now()) {
  try {
    const got = JSON.parse(localStorage.getItem(KEY) || "null");
    if (!got || !(got.plan in PLAN_NAMES) || !(now - Number(got.at) < KEEP_MS)) return null;
    return { plan: got.plan, yearly: !!got.yearly };
  } catch {
    return null;
  }
}

/**
 * Where /signed-in sends the browser next: back to the small server the app opened on this PC
 * for the sign-in, with the pick (or "none"). `hash` is "#p=<port>&k=<one-time key>", written by
 * the app. Null when the hash is not the app's: then the page just says they are signed in.
 */
export function wayBack(hash, pick) {
  const q = new URLSearchParams(String(hash || "").replace(/^#/, ""));
  const port = Number(q.get("p"));
  const key = q.get("k") || "";
  if (!Number.isInteger(port) || port < 1024 || port > 65535 || !/^[A-Za-z0-9_-]{16,128}$/.test(key)) return null;
  const out = new URLSearchParams({ plan: pick ? pick.plan : "none", yearly: pick && pick.yearly ? "1" : "0", k: key });
  return `http://127.0.0.1:${port}/pick?${out}`;
}
