import { describe, expect, it, vi } from "vitest";
import type { AccountEvent, AccountState, BrainEvent, ClientMessage } from "./protocol";

// The store listens to the brain as it loads: a stand-in that keeps what was sent.
const wire = vi.hoisted(() => ({ handlers: [] as ((e: unknown) => void)[], sent: [] as unknown[] }));
vi.mock("./socket", () => ({
  brain: { on: (fn: (e: unknown) => void) => void wire.handlers.push(fn), send: (m: unknown) => void wire.sent.push(m) },
}));

const { allowanceLine, allowanceShare, ask, cardButton, friendlyDate, hasPlan, planLine, PLANS, price, showPlans, taken, trialDaysLeft, trialLine, useAccount } = await import("./account");
const fromBrain = (e: unknown) => wire.handlers.forEach((fn) => fn(e as BrainEvent));

const account = (changes: Partial<AccountState> = {}): AccountState => ({
  offered: true, invite_codes: true, signed_in: true, ask_sign_in: false, email: "sam@example.com", plan: "trial", status: "trial",
  trial_ends_at: "2026-11-29T12:00:00Z", days_left: 3, renews_at: null, cancels: false, allowance: { used_usd: 0.3, limit_usd: 1.5, resets_at: null }, busy: "", gate: null, ...changes,
});
const event = (changes: Partial<AccountEvent> = {}): AccountEvent => ({ type: "account", account: account(), ...changes });

describe("the account's words", () => {
  it("counts the trial's days the way people do: 3 on day one, ends today on the last day", () => {
    const HOUR = 3_600_000;
    const ends = Date.parse("2026-11-29T12:00:00Z"); // a trial that began on the 26th at noon
    const at = (hoursIn: number) => planLine(account(), ends - 72 * HOUR + hoursIn * HOUR);
    expect(at(0)).toBe("3-day trial, 3 days left"); // the moment they sign in (the server says 2 here)
    expect(at(1)).toBe("3-day trial, 3 days left");
    expect(at(23.9)).toBe("3-day trial, 3 days left");
    expect(at(24)).toBe("3-day trial, 2 days left");
    expect(at(47)).toBe("3-day trial, 2 days left");
    expect(at(48.5)).toBe("3-day trial, ends today"); // the last day
    expect(at(71.9)).toBe("3-day trial, ends today");
    expect(at(73)).toBe("3-day trial, ends today"); // a moment past the end, before the server has said so
    expect(trialDaysLeft(account(), ends - 72 * HOUR)).toBe(3);
    expect(trialDaysLeft(account(), ends - 1)).toBe(1);
  });

  it("falls back to the server's count of whole days when the end time is missing", () => {
    // The server counts whole days still to come: 2 on day one, 0 on the last day.
    expect(trialLine({ status: "trial", days_left: 2 })).toBe("3-day trial, 3 days left");
    expect(trialLine({ status: "trial", days_left: 1 })).toBe("3-day trial, 2 days left");
    expect(trialLine({ status: "trial", days_left: 0 })).toBe("3-day trial, ends today");
    expect(trialLine({ status: "trial", days_left: null })).toBe("3-day trial");
    expect(trialLine({ status: "trial", days_left: 0, trial_ends_at: "not a date" })).toBe("3-day trial, ends today");
    expect(trialLine({ status: "active", days_left: null })).toBe("");
  });

  it("says the plan and when it renews or ends", () => {
    const pro = account({ plan: "pro", status: "active", days_left: null, renews_at: "2026-12-26T09:00:00Z" });
    expect(planLine(pro)).toBe("Pro plan, renews on 26 December");
    expect(planLine({ ...pro, cancels: true })).toBe("Pro plan, ends on 26 December");
    expect(planLine({ ...pro, status: "past_due" })).toBe("Pro plan. Your last payment didn't go through");
    expect(planLine(account({ plan: "none", status: "ended", days_left: null }))).toBe("Your trial has ended");
    expect(planLine(account({ plan: "none", status: "ended", days_left: null, trial_ends_at: null }))).toBe("Your plan has ended");
    expect(hasPlan(pro)).toBe(true);
    expect(hasPlan(account())).toBe(false);
    expect(hasPlan(account({ plan: "none", status: "ended" }))).toBe(false);
  });

  it("shows the allowance as a share, never as money", () => {
    expect(allowanceShare(account())).toBe(20);
    expect(allowanceLine(account())).toBe("20 percent used."); // a trial has no month to wait for
    const pro = account({ plan: "pro", status: "active", allowance: { used_usd: 7.4, limit_usd: 9, resets_at: "2026-12-01T00:00:00Z" } });
    expect(allowanceShare(pro)).toBe(82);
    expect(allowanceLine(pro)).toBe("82 percent used. Resets on 1 December.");
    expect(allowanceLine(pro)).not.toContain("$");
    expect(allowanceShare(account({ allowance: { used_usd: 30, limit_usd: 9, resets_at: null } }))).toBe(100);
    expect(allowanceShare(account({ allowance: null }))).toBeNull();
    expect(allowanceShare(account({ allowance: { used_usd: 1, limit_usd: 0, resets_at: null } }))).toBeNull();
    expect(friendlyDate("not a date")).toBe("");
    expect(friendlyDate(null)).toBe("");
  });

  it("has the website's plans and prices", () => {
    expect(PLANS.map((p) => [p.id, price(p, false), price(p, true)])).toEqual([
      ["starter", "$9 a month", "$90 a year"],
      ["pro", "$19 a month", "$180 a year"],
      ["max", "$39 a month", "$372 a year"],
    ]);
  });

  it("gives each gate's card its one button", () => {
    expect(cardButton(["choose_plan"])).toEqual({ label: "Choose a plan", does: "plans" });
    expect(cardButton(["see_plans"])).toEqual({ label: "See plans", does: "plans" });
    expect(cardButton(["update_card"])).toEqual({ label: "Update card", does: "portal" });
    expect(cardButton(["pyxi_sign_in"])).toEqual({ label: "Sign in", does: "sign_in" });
    expect(cardButton([])).toBeNull(); // plans not open yet: nothing to press
    expect(cardButton(["google", "open"])).toBeNull(); // other cards keep their own buttons
  });
});

describe("the account store", () => {
  it("takes the account from the hello and asks the server for the rest", () => {
    wire.sent.length = 0;
    fromBrain({ type: "hello", account: account({ plan: null, status: null }) });
    expect(useAccount.getState().account?.signed_in).toBe(true);
    expect(wire.sent).toEqual([{ type: "account", action: "get" }]);
  });

  it("keeps what went wrong, with Try again only when another try may work", () => {
    const checkout: ClientMessage = { type: "account", action: "checkout", plan: "pro", yearly: true };
    ask(checkout);
    fromBrain(event({ what: "checkout", ok: false, message: "I can't reach Pyxi right now. Check your internet and try again.", retry: true }));
    expect(useAccount.getState().note).toEqual({ ok: false, text: "I can't reach Pyxi right now. Check your internet and try again.", retry: checkout });
    fromBrain(event({ what: "checkout", ok: false, message: "Plans aren't open yet. Your trial keeps working for now." }));
    expect(useAccount.getState().note?.retry).toBeNull();
    fromBrain(event({ what: "sign_in", ok: false, message: "", cancelled: true }));
    expect(useAccount.getState().note).toBeNull(); // they cancelled: nothing to say
  });

  it("opens the plans from a card and closes them once the plan is theirs", () => {
    showPlans();
    expect(useAccount.getState().picking).toBe(true);
    fromBrain(event({ what: "config", ok: true })); // only what the server offers
    expect(useAccount.getState().picking).toBe(true);
    fromBrain(event({ what: "plan", ok: true, message: "You're on the Pro plan now. Pyxi is ready.", account: account({ plan: "pro", status: "active" }) }));
    const s = useAccount.getState();
    expect([s.picking, s.note?.ok, s.note?.text, s.account?.plan]).toEqual([false, true, "You're on the Pro plan now. Pyxi is ready.", "pro"]);
    expect(taken(event({ what: "portal", ok: false, message: "You don't have a plan yet. Choose one first.", pick: true })).picking).toBe(true);
  });

  it("asks about the allowance after a turn, only when signed in", () => {
    useAccount.setState({ account: account() });
    wire.sent.length = 0;
    fromBrain({ type: "turn_done" });
    expect(wire.sent).toEqual([{ type: "account", action: "refresh" }]);
    useAccount.setState({ account: account({ signed_in: false }) });
    fromBrain({ type: "turn_done" });
    expect(wire.sent).toHaveLength(1);
  });
});

describe("the plan picker's labels", () => {
  it("marks the plan pressed on the website, and what the trial runs on", async () => {
    const { planLit, PLANS, planTag, yearlySaving } = await import("./account");
    const trial = { status: "trial" as const, trial_ends_at: "2026-11-29T12:00:00Z", picked: { plan: "starter" as const, yearly: true } };
    expect(planTag("starter", trial)).toBe("Your pick");
    expect(planTag("pro", trial)).toBe("Your trial is on this");
    expect(planTag("max", trial)).toBe("");
    expect(planLit("starter", trial)).toBe(true);
    expect(planLit("pro", trial)).toBe(false);
    // no pick: Pro is the row that stands out, as before
    expect(planLit("pro", { picked: null })).toBe(true);
    expect(planTag("pro", { status: "ended", trial_ends_at: "2026-11-29T12:00:00Z", picked: null })).toBe("What your trial was on");
    expect(planTag("pro", { status: "ended", trial_ends_at: null, picked: null })).toBe("");
    expect(PLANS.map(yearlySaving)).toEqual([18, 48, 96]);
  });
});
