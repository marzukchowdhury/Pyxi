/**
 * The Pyxi account as the windows know it: what the brain last said (one `account` event,
 * pyxi/account.py), how the last thing they tried went, and whether the plan picker is open.
 * Settings draws it (panel/Account.tsx); the notch's cards and the tour read it too.
 *
 * The words here are plain functions, so they can be tested without a screen (account.test.ts).
 */
import { create } from "zustand";
import type { AccountEvent, AccountState, ClientMessage, PaidPlan } from "./protocol";
import { brain } from "./socket";

export interface AccountNote {
  ok: boolean;
  text: string;
  /** What to send again when "Try again" is pressed. */
  retry: ClientMessage | null;
}

interface AccountStore {
  account: AccountState | null;
  note: AccountNote | null;
  /** The plan picker is open (Settings; "Choose a plan" on a card opens it too). */
  picking: boolean;
}

export const useAccount = create<AccountStore>(() => ({ account: null, note: null, picking: false }));

/** What they last asked for, so "Try again" can ask for exactly that. */
let last: ClientMessage | null = null;
export function ask(msg: ClientMessage): void {
  last = msg;
  useAccount.setState({ note: null });
  brain.send(msg);
}

/** Open the plans (from a card's "Choose a plan" or "See plans", or from Settings). */
export function showPlans(open = true): void {
  useAccount.setState({ picking: open, note: null });
}

/** What an `account` event changes in the store. */
export function taken(e: AccountEvent): Partial<AccountStore> {
  const next: Partial<AccountStore> = { account: e.account };
  if (e.what === "config") return next; // only what the server offers: nothing to say
  if (e.message) next.note = { ok: !!e.ok, text: e.message, retry: e.retry ? last : null };
  else if (e.what && e.cancelled) next.note = null;
  if (e.pick) next.picking = true;
  // The plan is theirs now (they paid in the browser): the picker has done its work.
  if (e.what === "plan" && e.ok) next.picking = false;
  if (e.what === "checkout" && e.ok) next.picking = false;
  return next;
}

brain.on((e) => {
  if (e.type === "hello" && e.account) {
    useAccount.setState({ account: e.account });
    brain.send({ type: "account", action: "get" }); // the plan and the allowance, from the server
  } else if (e.type === "account") useAccount.setState(taken(e));
  // A turn or a job just spent some of the allowance (the brain asks the server at most twice a minute).
  else if ((e.type === "turn_done" || e.type === "job_ready") && useAccount.getState().account?.signed_in) brain.send({ type: "account", action: "refresh" });
});

// Back from paying in the browser: ask at once, so the plan unlocks without waiting.
if (typeof window !== "undefined")
  window.addEventListener("focus", () => {
    if (useAccount.getState().account?.signed_in) brain.send({ type: "account", action: "refresh" });
  });

// ---------------------------------------------------------------------------- the words
/** The plans and prices, as on the website's pricing page. */
export const PLANS: { id: PaidPlan; name: string; for: string; month: number; year: number }[] = [
  { id: "starter", name: "Starter", for: "For the odd question and a few errands.", month: 9, year: 90 },
  { id: "pro", name: "Pro", for: "For using Pyxi through the working day.", month: 19, year: 180 },
  { id: "max", name: "Max", for: "For handing Pyxi real work, all day.", month: 39, year: 372 },
];

/** "$19 a month", "$180 a year". */
export function price(plan: (typeof PLANS)[number], yearly: boolean): string {
  return yearly ? `$${plan.year} a year` : `$${plan.month} a month`;
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-12-01T00:00:00Z" as "1 December", the way the server writes dates ("" for none). In UTC, as it counts. */
export function friendlyDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

const NAMES: Record<string, string> = { starter: "Starter", pro: "Pro", max: "Max" };

/** "3-day trial, 2 days left" (the tour says it once at the start; Settings always shows it). */
export function trialLine(a: Pick<AccountState, "status" | "days_left">): string {
  if (a.status !== "trial") return "";
  if (a.days_left === null) return "3-day trial";
  return a.days_left <= 0 ? "3-day trial, last day" : `3-day trial, ${a.days_left} day${a.days_left === 1 ? "" : "s"} left`;
}

/** The plan in one line: the trial and its days, the plan and when it renews or ends. */
export function planLine(a: AccountState): string {
  if (a.status === "trial") return trialLine(a);
  const name = a.plan ? NAMES[a.plan] : undefined;
  const when = friendlyDate(a.renews_at);
  if (a.status === "ended" || !name) return a.trial_ends_at ? "Your trial has ended" : "Your plan has ended"; // the server drops the trial date once they have paid
  if (a.status === "past_due") return `${name} plan. Your last payment didn't go through`;
  if (a.cancels) return `${name} plan${when ? `, ends on ${when}` : ""}`;
  return `${name} plan${when ? `, renews on ${when}` : ""}`;
}

/** How much of the month's allowance is used, 0 to 100 (null: nothing to show). */
export function allowanceShare(a: AccountState): number | null {
  const al = a.allowance;
  if (!al || !(al.limit_usd > 0)) return null;
  return Math.max(0, Math.min(100, Math.round((al.used_usd / al.limit_usd) * 100)));
}

/** "42 percent used. Resets on 1 December." */
export function allowanceLine(a: AccountState): string {
  const share = allowanceShare(a);
  if (share === null) return "";
  const resets = friendlyDate(a.allowance?.resets_at);
  // The trial's allowance is for the whole trial: there is no month to wait for.
  return `${share} percent used${a.status !== "trial" && resets ? `. Resets on ${resets}` : ""}.`;
}

/** Do they have a paid plan to change or manage (on Stripe's own page)? */
export function hasPlan(a: AccountState): boolean {
  return (a.status === "active" || a.status === "past_due") && !!a.plan && a.plan in NAMES;
}

/** The buttons a card's actions stand for (overlay/Notch.tsx): the label, and what it does. */
export type CardButton = { label: string; does: "plans" | "portal" | "sign_in" };
export const CARD_BUTTONS: Record<string, CardButton> = {
  choose_plan: { label: "Choose a plan", does: "plans" },
  see_plans: { label: "See plans", does: "plans" },
  update_card: { label: "Update card", does: "portal" },
  pyxi_sign_in: { label: "Sign in", does: "sign_in" },
};
export function cardButton(actions: string[]): CardButton | null {
  const found = actions.find((a) => a in CARD_BUTTONS);
  return found ? CARD_BUTTONS[found] : null;
}
