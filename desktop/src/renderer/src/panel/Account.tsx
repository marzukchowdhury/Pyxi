/**
 * Settings, Account: who is signed in, the plan (the trial and its days left, or when the plan
 * renews), how much of this month's allowance is used, and the buttons: choose or change a
 * plan, manage the subscription, sign out. The plans open here; paying happens on Stripe's own
 * page in the browser, and the plan unlocks here by itself a few seconds later.
 *
 * Nothing is shown to someone on an invite code (their beta card is unchanged), or when the
 * server has no sign-in on.
 */
import { useEffect, useState } from "react";
import { allowanceLine, allowanceShare, ask, hasPlan, planLine, PLANS, price, showPlans, useAccount } from "../shared/account";
import { brain } from "../shared/socket";
import { usePanel } from "./store";

export function AccountCard() {
  const account = useAccount((s) => s.account);
  const note = useAccount((s) => s.note);
  const picking = useAccount((s) => s.picking);
  const joined = usePanel((s) => !!s.hello?.beta?.joined);
  useEffect(() => brain.send({ type: "account", action: "get" }), []);
  if (!account) return null;
  const busy = account.busy;
  const message = note && (
    <p className={note.ok ? "beta-ok" : "beta-error"} role="status">
      {note.text}{" "}
      {note.retry && <button type="button" className="account-link" onClick={() => note.retry && ask(note.retry)}>Try again</button>}
    </p>
  );

  // The sign-in ran out (or was ended from another device): one button brings it back.
  if (account.gate === "signed_out")
    return (
      <div className="beta-card account-card">
        <b>Account</b>
        <p>You've been signed out of Pyxi. Sign in to carry on.</p>
        <div className="beta-row"><SignInButton busy={busy === "sign_in"} label="Sign in" /></div>
        {message}
      </div>
    );

  if (!account.signed_in) {
    // On an invite code, or the server has no sign-in on: nothing changes here.
    if (joined || !account.offered) return null;
    return (
      <div className="beta-card account-card">
        <b>Account</b>
        <p>Sign in to start your 3-day trial of Pro. No card needed.</p>
        <div className="beta-row"><SignInButton busy={busy === "sign_in"} label="Continue with Google" /></div>
        {message}
      </div>
    );
  }

  const share = allowanceShare(account);
  const paid = hasPlan(account);
  return (
    <div className="beta-card account-card">
      <b>Account</b>
      {account.email && <div className="kv"><span>Signed in as</span><b>{account.email}</b></div>}
      {account.status && <div className="kv"><span>Plan</span><b>{planLine(account)}</b></div>}
      {share !== null && (
        <div className="account-allowance">
          <div className="kv"><span>{account.status === "trial" ? "Trial allowance" : "This month's allowance"}</span><b>{allowanceLine(account)}</b></div>
          <div className={`account-bar ${share >= 100 ? "full" : share >= 80 ? "near" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={share} aria-label="Allowance used"><i style={{ width: `${share}%` }} /></div>
        </div>
      )}
      <div className="beta-row account-actions">
        {paid ? (
          <>
            {account.status === "past_due" && <button type="button" className="primary" disabled={!!busy} onClick={() => ask({ type: "account", action: "portal" })}>Update card</button>}
            <button type="button" className={account.status === "past_due" ? "ghost" : "primary"} disabled={!!busy} onClick={() => ask({ type: "account", action: "portal" })}>Change plan</button>
            <button type="button" className="ghost" disabled={!!busy} onClick={() => ask({ type: "account", action: "portal" })}>Manage subscription</button>
          </>
        ) : (
          account.status && <button type="button" className="primary" disabled={!!busy} onClick={() => showPlans(!picking)}>{picking ? "Hide plans" : "Choose a plan"}</button>
        )}
        <span className="account-gap" />
        <button type="button" className="ghost" disabled={!!busy} onClick={() => ask({ type: "account", action: "sign_out" })}>{busy === "sign_out" ? "Signing out" : "Sign out"}</button>
      </div>
      {busy === "portal" && <p role="status">Opening your subscription page.</p>}
      {message}
      {picking && !paid && <PlanPicker busy={busy === "checkout"} />}
    </div>
  );
}

function SignInButton({ busy, label }: { busy: boolean; label: string }) {
  return (
    <>
      <button type="button" className="primary" disabled={busy} onClick={() => ask({ type: "account", action: "sign_in" })}>{busy ? "Check your browser" : label}</button>
      {busy && <button type="button" className="ghost" onClick={() => brain.send({ type: "account", action: "cancel_sign_in" })}>Cancel</button>}
    </>
  );
}

/** Starter, Pro, Max; monthly or yearly. A press opens Stripe Checkout in the browser. */
export function PlanPicker({ busy }: { busy: boolean }) {
  const [yearly, setYearly] = useState(false);
  return (
    <div className="plan-picker">
      <div className="plan-bill" role="group" aria-label="Billing">
        <button type="button" aria-pressed={!yearly} className={yearly ? "" : "on"} onClick={() => setYearly(false)}>Monthly</button>
        <button type="button" aria-pressed={yearly} className={yearly ? "on" : ""} onClick={() => setYearly(true)}>Yearly <i>two months free</i></button>
      </div>
      <div className="plan-list">
        {PLANS.map((p) => (
          <div key={p.id} className={`plan-row ${p.id === "pro" ? "pro" : ""}`}>
            <div className="plan-text">
              <b>{p.name}</b>
              <span>{p.for}</span>
            </div>
            <span className="plan-price">{price(p, yearly)}</span>
            <button type="button" className="primary" disabled={busy} onClick={() => ask({ type: "account", action: "checkout", plan: p.id, yearly })}>Choose</button>
          </div>
        ))}
      </div>
      <p>{busy ? "Opening checkout in your browser." : "Prices in US dollars. You pay on Stripe's page in your browser. Pyxi never sees your card. Your plan starts here a few seconds after you pay."}</p>
    </div>
  );
}
