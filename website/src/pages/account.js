/**
 * /account: see your plan, choose one, or open Stripe's page for the one you have, without
 * opening the app.
 *
 * Signing in here uses Google's own button (the only place the site loads anything from Google,
 * and only once the server says this page is switched on). The server answers with a sign-in
 * that lasts an hour and can do two things: read the account and open Stripe's pages. It is kept
 * in this page's memory only (never in storage), so closing the tab ends it.
 * It never starts a trial: that happens in the app, the first time someone signs in there.
 */
import "./account.css";
import { PLAN_NAMES, readPick } from "../pick.js";

const PLANS = [
  { id: "starter", for: "For the odd question and a few errands.", month: 9, year: 90 },
  { id: "pro", for: "For using Pyxi through the working day.", month: 19, year: 180 },
  { id: "max", for: "For handing Pyxi real work, all day.", month: 39, year: 372 },
];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const OFFLINE = "That didn't reach Pyxi's server. Check your internet and try again.";

/** "2026-12-01T00:00:00Z" as "1 December" (in UTC, as the server counts). */
export function friendlyDate(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}` : "";
}

/** The plan in one line, the way the app's Settings says it. */
export function planLine(a) {
  const name = PLAN_NAMES[a.plan];
  const when = friendlyDate(a.renews_at);
  if (a.status === "trial") {
    const ends = friendlyDate(a.trial_ends_at);
    return `3-day trial of Pro${ends ? `, ends on ${ends}` : ""}`;
  }
  if (a.status === "ended" || !name) return a.trial_ends_at ? "Your trial has ended" : "Your plan has ended";
  if (a.status === "past_due") return `${name} plan. Your last payment didn't go through`;
  return `${name} plan${when ? `, ${a.cancels ? "ends" : "renews"} on ${when}` : ""}`;
}

export const hasPlan = (a) => (a.status === "active" || a.status === "past_due") && a.plan in PLAN_NAMES;

if (document.body.classList.contains("page-account")) init();

function init() {
  const api = (document.body.dataset.api || "").replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const cards = ["acct-wait", "acct-off", "acct-in", "acct-you"];
  const show = (id) => cards.forEach((c) => ($(c).hidden = c !== id));
  const say = (el, text) => {
    el.textContent = text || "";
    el.hidden = !text;
  };
  let token = ""; // this page's memory only
  let yearly = false;
  let busy = false;

  async function call(path, body) {
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(api + path, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
      return { ok: false, status: 0, message: OFFLINE };
    }
    const data = await res.json().catch(() => ({}));
    const err = data && data.error;
    return { ok: res.ok, status: res.status, data, code: (err && err.code) || "", message: (err && err.message) || (res.ok ? "" : "That didn't work. Try again in a moment.") };
  }

  // ---- the plans, with the one pressed on the pricing page marked and its billing chosen
  const pick = readPick();
  yearly = !!(pick && pick.yearly);
  function drawPlans(account) {
    const box = $("acct-plans");
    box.textContent = "";
    for (const p of PLANS) {
      const tag = pick && pick.plan === p.id ? "Your pick" : p.id === "pro" && (account.status === "trial" || account.trial_ends_at) ? (account.status === "trial" ? "Your trial is on this" : "What your trial was on") : "";
      const row = document.createElement("div");
      row.className = `acct-plan${(pick ? pick.plan === p.id : p.id === "pro") ? " lit" : ""}`;
      const text = document.createElement("div");
      const name = document.createElement("b");
      name.textContent = PLAN_NAMES[p.id];
      if (tag) {
        const em = document.createElement("em");
        em.textContent = tag;
        name.append(em);
      }
      const what = document.createElement("span");
      what.textContent = p.for;
      text.append(name, what);
      const price = document.createElement("span");
      price.className = "acct-price";
      price.textContent = yearly ? `$${p.year} a year` : `$${p.month} a month`;
      if (yearly) {
        const save = document.createElement("i");
        save.textContent = `save $${p.month * 12 - p.year}`;
        price.append(save);
      }
      const go = document.createElement("button");
      go.type = "button";
      go.className = "btn btn-fly btn-sm";
      go.textContent = "Choose";
      go.addEventListener("click", () => open("/api/billing/checkout", { plan: p.id, yearly }));
      row.append(text, price, go);
      box.append(row);
    }
    document.querySelectorAll(".acct-bill button").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.bill === "year") === yearly)));
  }

  /** Ask the server for one of Stripe's pages and go there. */
  async function open(path, body) {
    if (busy) return;
    busy = true;
    say($("acct-note"), "Opening Stripe's page.");
    const r = await call(path, body);
    busy = false;
    if (r.ok && r.data.url && /^https:\/\//.test(r.data.url)) return location.assign(r.data.url);
    if (r.status === 401) return signedOut("That sign-in ran out. Sign in again.");
    say($("acct-note"), r.message);
  }

  function signedIn(account) {
    $("acct-email").textContent = account.email;
    $("acct-plan").textContent = planLine(account);
    say($("acct-note"), "");
    const paid = hasPlan(account);
    $("acct-paid").hidden = !paid;
    $("acct-pick").hidden = paid;
    if (!paid) {
      document.querySelectorAll(".acct-bill button").forEach((b) => (b.onclick = () => ((yearly = b.dataset.bill === "year"), drawPlans(account))));
      drawPlans(account);
    }
    show("acct-you");
  }

  function signedOut(note = "") {
    token = "";
    say($("acct-in-note"), note);
    show("acct-in");
  }

  $("acct-portal").addEventListener("click", () => open("/api/billing/portal", {}));
  $("acct-out").addEventListener("click", async () => {
    await call("/api/auth/signout", {});
    try {
      window.google?.accounts.id.disableAutoSelect();
    } catch {}
    signedOut();
  });

  // ---- is this page switched on? (the server names the website's own Google client)
  (async () => {
    if (!api) return show("acct-off");
    const cfg = await call("/api/config");
    const clientId = cfg.ok && cfg.data.sign_in ? cfg.data.google_web_client_id : null;
    if (!clientId) return show("acct-off");
    const gsi = document.createElement("script");
    gsi.src = "https://accounts.google.com/gsi/client";
    gsi.async = true;
    gsi.onerror = () => show("acct-off");
    gsi.onload = () => {
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: async (answer) => {
          say($("acct-in-note"), "Signing you in.");
          const r = await call("/api/auth/web", { id_token: answer.credential });
          if (!r.ok || !r.data.token) return signedOut(r.message);
          token = r.data.token;
          signedIn(r.data.account);
        },
      });
      window.google.accounts.id.renderButton($("acct-google"), { theme: "filled_black", size: "large", shape: "pill", text: "continue_with" });
      show("acct-in");
    };
    document.head.append(gsi);
  })();
}
