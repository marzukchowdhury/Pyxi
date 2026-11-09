// Pyxi in your browser: the popup. Pair this browser with Pyxi, and choose where Pyxi may work.
"use strict";

const api = globalThis.browser || globalThis.chrome;
const $ = (id) => document.getElementById(id);
const ALL_SITES = { origins: ["http://*/*", "https://*/*"] };
const TAB_NAMES = { permissions: ["tabs"] };

const STATUS = {
  on: "Connected",
  connecting: "Connecting",
  unpaired: "Not paired",
  off: "Not connected",
};

const ask = (message) => api.runtime.sendMessage({ to: "pyxi-bg", ...message });

/** The site of the tab this popup was opened on, as a permission the browser understands. */
async function thisSite() {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) return null;
  let url;
  try {
    url = new URL(tab.url);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return { host: url.host, pattern: `${url.protocol}//${url.hostname}/*` };
}

async function draw() {
  const now = await ask({ type: "status" }).catch(() => null);
  if (!now) return;
  const status = $("status");
  status.textContent = STATUS[now.state] || "Not connected";
  status.className = "status" + (now.state === "on" ? " on" : "");
  $("detail").textContent = now.detail || "";
  $("detail").hidden = !now.detail;
  $("pair").hidden = now.paired;
  $("more").hidden = !now.paired;
  if (!$("port").value) $("port").value = now.port;

  const site = now.paired ? await thisSite() : null;
  $("site").hidden = !site;
  if (site) {
    const kept = await api.permissions.contains({ origins: [site.pattern] });
    $("site-line").textContent = kept
      ? `Pyxi may work on ${site.host}.`
      : `Pyxi may work in this tab until you leave ${site.host}, because you clicked its icon here.`;
    $("site-allow").hidden = kept;
    $("site-remove").hidden = !kept;
    // The browser only shows its own "Allow?" prompt for a request made straight from a click.
    $("site-allow").onclick = () => api.permissions.request({ origins: [site.pattern] }).then(draw, draw);
    $("site-remove").onclick = () => api.permissions.remove({ origins: [site.pattern] }).then(draw, draw);
  }
  if (!now.paired) return;

  const everywhere = await api.permissions.contains(ALL_SITES);
  $("all-sites").checked = everywhere;
  $("tab-names").checked = await api.permissions.contains(TAB_NAMES);
  const granted = (await api.permissions.getAll()).origins || [];
  const list = $("sites");
  list.replaceChildren();
  for (const pattern of granted) {
    if (ALL_SITES.origins.includes(pattern) || pattern === "<all_urls>") continue;
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = pattern.replace(/^\w+:\/\//, "").replace(/\/\*$/, "");
    const remove = document.createElement("button");
    remove.className = "ghost";
    remove.textContent = "Remove";
    remove.onclick = () => api.permissions.remove({ origins: [pattern] }).then(draw, draw);
    row.append(name, remove);
    list.append(row);
  }
  if (!list.children.length) {
    const row = document.createElement("li");
    row.className = "muted";
    row.textContent = everywhere
      ? "Every site."
      : "No site kept yet. Click this icon in a tab to let Pyxi work there.";
    list.append(row);
  }
}

$("pair-form").addEventListener("submit", (event) => {
  event.preventDefault();
  ask({ type: "pair", code: $("code").value, port: Number($("port").value) || undefined });
  $("code").value = "";
});
$("all-sites").addEventListener("change", (event) => {
  (event.target.checked ? api.permissions.request(ALL_SITES) : api.permissions.remove(ALL_SITES)).then(draw, draw);
});
$("tab-names").addEventListener("change", (event) => {
  (event.target.checked ? api.permissions.request(TAB_NAMES) : api.permissions.remove(TAB_NAMES)).then(draw, draw);
});
$("forget").addEventListener("click", () => ask({ type: "forget" }).then(draw, draw));

api.runtime.onMessage.addListener((msg) => {
  if (msg && msg.to === "pyxi-popup") draw();
});
ask({ type: "reconnect" }).catch(() => {});
draw();
