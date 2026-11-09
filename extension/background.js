// Pyxi in your browser: the background script (a service worker in Chrome and Edge, an event
// page in Firefox).
//
// It keeps one WebSocket to Pyxi on this PC (ws://127.0.0.1:<port>/ws/browser) and does what
// Pyxi asks through it: list tabs, read a tab, click, type, pick, press, scroll, go to an
// address, open or switch tab. The work in the page is done by content.js, which this script
// puts into a tab only when Pyxi needs that tab and the browser allows it.
//
// What it can't do, by design:
//   - talk to anything but 127.0.0.1 (the manifest's content security policy allows no other
//     connection);
//   - work on a site you haven't let it into (it has no site access when installed: clicking
//     its icon lets it into that tab, "Always allow on this site" keeps it);
//   - run code sent by Pyxi or anyone else: the requests below are the whole list.
"use strict";

const api = globalThis.browser || globalThis.chrome;
const DEFAULT_PORT = 8766;
const VERSION = api.runtime.getManifest().version;

let socket = null;
let state = "off"; // off | connecting | on | unpaired
let detail = ""; // why, for the popup
const acting = new Map(); // tabId -> { run, text }: tabs wearing the "Pyxi is working" marker
const halted = []; // runs the user stopped in the tab: nothing more is done for them
const nudged = new Map(); // tabId -> when the "!" badge went on

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

function problem(code, message, host = "") {
  const err = new Error(message);
  err.code = code;
  err.host = host;
  return err;
}

function browserName() {
  const ua = navigator.userAgent;
  if (/Firefox\//.test(ua)) return "Firefox";
  if (/Edg\//.test(ua)) return "Edge";
  if (/OPR\//.test(ua)) return "Opera";
  if (/Chrome\//.test(ua)) return "Chrome";
  return "Browser";
}

// ---------------------------------------------------------------------- the link to Pyxi
function setState(next, why = "") {
  state = next;
  detail = why;
  const title = {
    on: "Pyxi: connected",
    connecting: "Pyxi: connecting",
    unpaired: "Pyxi: not paired yet. Click to pair.",
    off: "Pyxi: not connected (is Pyxi running?)",
  }[next];
  api.action.setTitle({ title }).catch(() => {});
  api.runtime.sendMessage({ to: "pyxi-popup", type: "state" }).catch(() => {}); // if it's open
}

async function connect(code = "") {
  if (!code && socket && socket.readyState <= 1) return; // already open or opening
  const { token, port } = await api.storage.local.get(["token", "port"]);
  if (!token && !code) {
    setState("unpaired");
    return;
  }
  if (socket) {
    const old = socket;
    socket = null;
    try {
      old.close();
    } catch {
      /* already closed */
    }
  }
  setState("connecting");
  let ws;
  try {
    ws = new WebSocket(`ws://127.0.0.1:${Number(port) || DEFAULT_PORT}/ws/browser`);
  } catch (err) {
    setState("off", String(err?.message || err));
    return;
  }
  socket = ws;
  const hello = { browser: browserName(), version: VERSION };
  ws.onopen = () => {
    ws.send(JSON.stringify(code ? { type: "pair", code, ...hello } : { type: "hello", token, ...hello }));
  };
  ws.onmessage = (event) => onMessage(ws, event.data);
  ws.onerror = () => {};
  ws.onclose = () => {
    if (socket !== ws) return;
    socket = null;
    if (state === "on" || state === "connecting") {
      setState("off", "Pyxi isn't running on this PC right now. It reconnects by itself.");
    }
    clearMarkers(); // nothing is being done for Pyxi any more
  };
}

async function onMessage(ws, raw) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  if (msg.type === "welcome") {
    setState("on");
  } else if (msg.type === "paired" && msg.token) {
    await api.storage.local.set({ token: msg.token });
    setState("on");
  } else if (msg.type === "refused") {
    const { token } = await api.storage.local.get("token");
    if (msg.reason === "unpaired") {
      await api.storage.local.remove("token");
      setState("unpaired", "This browser isn't paired with Pyxi any more. Pair it again.");
    } else if (msg.reason === "wrong_code") {
      setState(token ? "off" : "unpaired", "That code didn't work or ran out. Ask Pyxi for a new one.");
    } else {
      setState(token ? "off" : "unpaired", "Pyxi refused the connection.");
    }
  } else if (msg.type === "cmd") {
    run(ws, msg);
  } else if (msg.type === "stop") {
    clearMarkers();
  }
}

function tell(message) {
  if (socket && socket.readyState === 1) socket.send(JSON.stringify(message));
}

// A message every 20 seconds keeps the link (and, in Chrome, this service worker) alive.
setInterval(() => tell({ type: "ping" }), 20000);

// If Pyxi wasn't running, or the browser put this script to sleep, try again twice a minute.
api.alarms.create("pyxi-link", { periodInMinutes: 0.5 });
api.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "pyxi-link") connect();
});
api.runtime.onStartup.addListener(() => connect());
api.runtime.onInstalled.addListener(() => connect());
connect();

// Pyxi asks the browser the user was in last.
if (api.windows?.onFocusChanged) {
  api.windows.onFocusChanged.addListener((id) => {
    if (id !== api.windows.WINDOW_ID_NONE) tell({ type: "event", name: "focus" });
  });
}

// ---------------------------------------------------------------------- tabs
function brief(tab) {
  return {
    id: tab.id,
    title: tab.title || "",
    url: tab.url || "",
    active: !!tab.active,
    window: tab.windowId,
  };
}

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

async function activeTab() {
  let [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true, windowType: "normal" });
  if (!tab) [tab] = await api.tabs.query({ active: true, windowType: "normal" });
  if (!tab) throw problem("failed", "There's no browser tab open.");
  return tab;
}

async function getTab(id) {
  try {
    return await api.tabs.get(Number(id));
  } catch {
    throw problem("gone", "That tab was closed.");
  }
}

/** The tab Pyxi means: the one in front, a tab id, or words from a tab's title or address. */
async function resolveTab(which) {
  if (which === undefined || which === null || which === "") return activeTab();
  if (typeof which === "number" || /^\d+$/.test(String(which))) return getTab(which);
  const words = String(which).toLowerCase().split(/\s+/).filter(Boolean);
  const tabs = await api.tabs.query({ windowType: "normal" });
  const hits = tabs.filter((tab) => {
    const hay = `${tab.title || ""} ${tab.url || ""}`.toLowerCase();
    return hay.trim() && words.every((w) => hay.includes(w));
  });
  if (!hits.length) {
    const unseen = tabs.some((tab) => !tab.url);
    throw problem(
      "failed",
      `There's no open tab like "${which}".` +
        (unseen
          ? " Pyxi only sees the names of tabs on sites you've allowed (the extension's popup can let it see all tab names)."
          : ""),
    );
  }
  hits.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0) || Number(b.active) - Number(a.active));
  return hits[0];
}

async function settled(tabId, max = 8000) {
  await sleep(250);
  const until = Date.now() + max;
  while (Date.now() < until) {
    const tab = await api.tabs.get(tabId).catch(() => null);
    if (!tab || tab.status !== "loading") return;
    await sleep(150);
  }
}

function webAddress(url) {
  let parsed;
  try {
    parsed = new URL(String(url));
  } catch {
    throw problem("failed", "That isn't a web address.");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw problem("failed", "Pyxi only opens http and https addresses.");
  }
  return parsed.href;
}

// ---------------------------------------------------------------------- into the page
const NOT_THERE = /Receiving end does not exist|Could not establish connection/i;
const BROWSER_PAGE =
  /chrome:\/\/|edge:\/\/|about:|chrome-extension:\/\/|moz-extension:\/\/|extensions gallery|web store|error page|view-source|devtools|cannot be scripted/i;

async function inject(tab) {
  const now = await getTab(tab.id);
  const url = now.url || now.pendingUrl || "";
  if (url && !/^https?:/i.test(url)) {
    throw problem("browser_page", "That's one of the browser's own pages, which extensions can't work in.");
  }
  try {
    await api.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  } catch (err) {
    const text = String(err?.message || err);
    if (BROWSER_PAGE.test(text)) {
      throw problem("browser_page", "That's one of the browser's own pages, which extensions can't work in.");
    }
    // The browser hasn't let this extension into the site: the user decides, in the popup.
    nudge(tab.id);
    throw problem(
      "no_access",
      "The browser hasn't let Pyxi into this site yet. Click the Pyxi icon in the toolbar to allow it.",
      hostOf(url),
    );
  }
  unnudge(tab.id);
  const wearing = acting.get(tab.id);
  if (wearing) {
    await api.tabs
      .sendMessage(tab.id, { to: "pyxi-content", cmd: "marker", args: { on: true, text: wearing.text } }, { frameId: 0 })
      .catch(() => {});
  }
}

function nudge(tabId) {
  nudged.set(tabId, Date.now());
  api.action.setBadgeBackgroundColor({ color: "#ffbe50" }).catch(() => {});
  api.action.setBadgeText({ tabId, text: "!" }).catch(() => {});
  api.action.setTitle({ tabId, title: "Pyxi wants to work in this tab. Click to allow it." }).catch(() => {});
}

function unnudge(tabId) {
  if (!nudged.delete(tabId)) return;
  api.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  api.action.setTitle({ tabId, title: "Pyxi: connected" }).catch(() => {});
}

/** Ask content.js in the tab to do one thing; put it there first if it isn't yet. */
async function toContent(tab, cmd, args = {}) {
  const message = { to: "pyxi-content", cmd, args };
  const acts = cmd !== "read" && cmd !== "marker";
  const ask = () => api.tabs.sendMessage(tab.id, message, { frameId: 0 });
  let answer = null;
  try {
    answer = await ask();
  } catch (err) {
    if (acts && !NOT_THERE.test(String(err?.message || err))) {
      throw problem("navigated", "The page moved on while that was being done."); // it took effect
    }
  }
  if (!answer || answer.code === "retired") {
    // Not in the page yet, or put to rest when the site was removed: (re)enter if allowed.
    await inject(tab);
    try {
      answer = await ask();
    } catch {
      if (acts) throw problem("navigated", "The page moved on while that was being done.");
      throw problem("failed", "The page didn't answer. It may still be loading.");
    }
  }
  if (!answer) throw problem("failed", "The page didn't answer.");
  if (answer.ok) return answer.data || {};
  throw problem(answer.code || "failed", answer.error || "That didn't work.");
}

async function act(tab, cmd, args, settle) {
  let data;
  try {
    data = await toContent(tab, cmd, args);
  } catch (err) {
    if (err.code !== "navigated") throw err;
    data = { navigated: true };
    settle = true;
  }
  if (settle) await settled(tab.id);
  const now = await api.tabs.get(tab.id).catch(() => null);
  return { ...data, tab: now ? brief(now) : brief(tab) };
}

async function setMarker(tabId, on, run, text) {
  if (on) acting.set(tabId, { run, text: text || "Pyxi is working in this tab" });
  else acting.delete(tabId);
  const tab = await api.tabs.get(tabId).catch(() => null);
  if (!tab) return;
  if (on) {
    await toContent(tab, "marker", { on: true, text: acting.get(tabId).text });
  } else {
    await api.tabs
      .sendMessage(tabId, { to: "pyxi-content", cmd: "marker", args: { on: false } }, { frameId: 0 })
      .catch(() => {});
  }
}

function clearMarkers() {
  for (const tabId of [...acting.keys()]) setMarker(tabId, false).catch(() => {});
}

// A page that loads while Pyxi is working in its tab gets the marker again (and Esc with it).
api.tabs.onUpdated.addListener((tabId, change) => {
  const wearing = acting.get(tabId);
  if (!wearing || change.status !== "complete") return;
  api.tabs
    .get(tabId)
    .then((tab) => toContent(tab, "marker", { on: true, text: wearing.text }))
    .catch(() => {});
});
api.tabs.onRemoved.addListener((tabId) => {
  acting.delete(tabId);
  nudged.delete(tabId);
});

// A site taken away in the popup: content.js already in its open pages stops answering at
// once (it can only come back by being put in again, which the browser now refuses).
api.permissions.onRemoved.addListener(async () => {
  for (const tab of await api.tabs.query({})) {
    api.tabs.sendMessage(tab.id, { to: "pyxi-content", cmd: "retire" }, { frameId: 0 }).catch(() => {});
    acting.delete(tab.id);
  }
});

// ---------------------------------------------------------------------- what Pyxi can ask
const COMMANDS = {
  async tabs() {
    const tabs = await api.tabs.query({ windowType: "normal" });
    // Without the optional "tabs" permission, the browser only shows this extension the
    // names of tabs on sites it has been let into. The rest are counted, not named.
    return { tabs: tabs.filter((t) => t.url).map(brief), unnamed: tabs.filter((t) => !t.url).length };
  },
  async peek({ tab }) {
    return brief(await resolveTab(tab));
  },
  async read({ tab, max_chars, max_items, from_view }) {
    const target = await resolveTab(tab);
    const data = await toContent(target, "read", { max_chars, max_items, from_view });
    return { ...data, tab: brief(await getTab(target.id)) };
  },
  async click({ tab, ref, settle }) {
    const target = await resolveTab(tab);
    const out = await act(target, "click", { ref }, settle);
    if (!out.open) return out;
    // A link that opens a new tab: a page can't do that for a click a person didn't make.
    const made = await api.tabs.create({ url: webAddress(out.open), active: true, index: target.index + 1 });
    await settled(made.id, 15000);
    return { opened: brief(await getTab(made.id)) };
  },
  async type({ tab, ref, text, enter, settle }) {
    return act(await resolveTab(tab), "type", { ref, text, enter: !!enter }, settle);
  },
  async select({ tab, ref, option, settle }) {
    return act(await resolveTab(tab), "select", { ref, option }, settle);
  },
  async press({ tab, ref, key, settle }) {
    return act(await resolveTab(tab), "press", { ref, key }, settle);
  },
  async scroll({ tab, ref, direction }) {
    return act(await resolveTab(tab), "scroll", { ref, direction }, false);
  },
  async goto({ tab, url }) {
    const target = await resolveTab(tab);
    await api.tabs.update(target.id, { url: webAddress(url) });
    await settled(target.id, 20000);
    return brief(await getTab(target.id));
  },
  async open({ url }) {
    const made = await api.tabs.create({ url: webAddress(url), active: true });
    await settled(made.id, 20000);
    return brief(await getTab(made.id));
  },
  async switch({ tab }) {
    const target = await resolveTab(tab);
    await api.tabs.update(target.id, { active: true });
    if (api.windows?.update) await api.windows.update(target.windowId, { focused: true }).catch(() => {});
    return brief(await getTab(target.id));
  },
  async back({ tab }) {
    const target = await resolveTab(tab);
    await api.tabs.goBack(target.id);
    await settled(target.id);
    return brief(await getTab(target.id));
  },
  async marker({ tab, on, text }, run) {
    const target = await resolveTab(tab);
    await setMarker(target.id, !!on, run, text);
    return { on: !!on };
  },
};

async function run(ws, msg) {
  let reply;
  try {
    const stopped = () => msg.run && halted.includes(msg.run);
    if (stopped()) throw problem("stopped", "You stopped Pyxi in that tab.");
    const does = Object.hasOwn(COMMANDS, msg.name) ? COMMANDS[msg.name] : null;
    if (!does) throw problem("failed", "This version of Pyxi's extension can't do that.");
    const data = await does(msg.args || {}, msg.run || "");
    if (stopped()) throw problem("stopped", "You stopped Pyxi in that tab.");
    reply = { type: "result", id: msg.id, ok: true, data };
  } catch (err) {
    reply = {
      type: "result",
      id: msg.id,
      ok: false,
      code: err.code || "failed",
      error: String(err?.message || err).slice(0, 300),
      host: err.host || "",
    };
  }
  if (ws.readyState === 1) ws.send(JSON.stringify(reply));
}

// ---------------------------------------------------------------------- the page and the popup
api.runtime.onMessage.addListener((msg, sender, respond) => {
  if (!msg || msg.to !== "pyxi-bg" || sender.id !== api.runtime.id) return undefined;
  const ownPage = !!sender.url && sender.url.startsWith(api.runtime.getURL(""));
  if (msg.type === "stopped" && sender.tab) {
    // Esc (or the marker's Stop) in a tab Pyxi was working in.
    const wearing = acting.get(sender.tab.id);
    acting.delete(sender.tab.id);
    if (wearing) {
      halted.push(wearing.run);
      if (halted.length > 50) halted.shift();
      tell({ type: "event", name: "stopped", run: wearing.run, by: msg.by === "button" ? "button" : "esc" });
    }
    return undefined;
  }
  if (!ownPage) return undefined; // the rest is for the extension's own popup only
  if (msg.type === "status") {
    api.storage.local.get(["token", "port"]).then(({ token, port }) => {
      respond({ state, detail, paired: !!token, port: Number(port) || DEFAULT_PORT, browser: browserName() });
    });
    return true;
  }
  if (msg.type === "pair") {
    const port = Number(msg.port);
    const save = port >= 1 && port <= 65535 ? api.storage.local.set({ port }) : Promise.resolve();
    save.then(() => connect(String(msg.code || ""))).then(() => respond({ ok: true }));
    return true;
  }
  if (msg.type === "forget") {
    api.storage.local.remove("token").then(() => {
      const old = socket;
      socket = null;
      try {
        old?.close();
      } catch {
        /* already closed */
      }
      clearMarkers();
      setState("unpaired");
      respond({ ok: true });
    });
    return true;
  }
  if (msg.type === "reconnect") {
    connect().then(() => respond({ ok: true }));
    return true;
  }
  return undefined;
});
