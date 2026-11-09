/**
 * The Mac's first-run card: which permissions Pyxi has, what each is for, and a button that
 * opens the right page of System Settings. Pyxi never flips a switch itself; the card checks
 * again whenever the user comes back to it.
 *
 * Plain DOM (no React): it's one small window. In a normal browser (the web preview) a stand-in
 * bridge shows a sample state.
 */
import "./permissions.css";

interface Row {
  key: "screen" | "accessibility" | "microphone" | "input";
  title: string;
  why: string;
  granted: boolean | null;
}

interface MacBridge {
  macPermissions(): Promise<Row[]>;
  openMacSettings(key: string): void;
  askMicrophone(): Promise<boolean>;
  macPermissionsLater(): void;
  restartPyxi(): void;
}

const sample: Row[] = [
  { key: "screen", title: "Screen Recording", why: "So Pyxi can see what's on your screen when you ask about it.", granted: false },
  { key: "accessibility", title: "Accessibility", why: "So Pyxi can read buttons and fields, arrange windows, hear hold-to-talk, and use the mouse and keyboard when you allow it.", granted: true },
  { key: "microphone", title: "Microphone", why: "So you can talk to Pyxi.", granted: false },
  { key: "input", title: "Input Monitoring", why: "Some macOS versions also ask for this before hold-to-talk can hear the keys.", granted: null },
];

const bridge: MacBridge =
  (window as unknown as { pyxiBridge?: MacBridge }).pyxiBridge ?? {
    macPermissions: async () => sample,
    openMacSettings: (key) => console.log("would open System Settings:", key),
    askMicrophone: async () => false,
    macPermissionsLater: () => console.log("later"),
    restartPyxi: () => console.log("restart"),
  };

let screenWasMissing = false;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(granted: boolean | null): HTMLElement {
  if (granted === true) return el("span", "badge ok", "Allowed");
  if (granted === false) return el("span", "badge todo", "Not yet");
  return el("span", "badge maybe", "Check");
}

function render(rows: Row[]): void {
  const root = document.getElementById("root")!;
  root.replaceChildren();
  root.append(el("h1", "", "A few permissions, and Pyxi is ready"));
  root.append(
    el(
      "p",
      "lead",
      "macOS asks you, not Pyxi, to allow these. Each button opens the right page of System Settings: turn Pyxi on there, then come back. Pyxi never changes a setting itself.",
    ),
  );
  const list = el("ul", "rows");
  for (const row of rows) {
    const li = el("li", "row");
    const text = el("div", "text");
    const head = el("div", "head");
    head.append(el("strong", "", row.title), badge(row.granted));
    text.append(head, el("p", "why", row.why));
    li.append(text);
    if (row.granted !== true) {
      const button = el("button", "open", row.key === "microphone" ? "Allow" : "Open Settings");
      button.addEventListener("click", async () => {
        if (row.key === "microphone") {
          // macOS shows its own prompt the first time; after a "Don't Allow" only Settings can.
          const ok = await bridge.askMicrophone();
          if (!ok) bridge.openMacSettings("microphone");
        } else bridge.openMacSettings(row.key);
        if (row.key === "screen") screenWasMissing = true;
      });
      li.append(button);
    }
    list.append(li);
  }
  root.append(list);

  const missing = rows.filter((r) => r.granted === false).length;
  const screenNow = rows.find((r) => r.key === "screen")?.granted === true;
  const foot = el("div", "foot");
  if (screenWasMissing && screenNow) {
    foot.append(el("p", "note", "Screen Recording starts working once Pyxi opens again."));
    const restart = el("button", "primary", "Restart Pyxi");
    restart.addEventListener("click", () => bridge.restartPyxi());
    foot.append(restart);
  } else if (missing === 0) {
    foot.append(el("p", "note", "All set. You can close this window."));
    const close = el("button", "primary", "Close");
    close.addEventListener("click", () => window.close());
    foot.append(close);
  } else {
    const later = el("button", "quiet", "Later");
    later.title = "Pyxi works without these, with less to go on. The menu bar icon opens this card again.";
    later.addEventListener("click", () => bridge.macPermissionsLater());
    foot.append(later);
  }
  root.append(foot);
}

async function refresh(): Promise<void> {
  try {
    render(await bridge.macPermissions());
  } catch (err) {
    console.warn("[pyxi] permissions check failed", err);
  }
}

// Coming back from System Settings: check again (and every few seconds while it's open).
window.addEventListener("focus", () => void refresh());
setInterval(() => void refresh(), 3000);
void refresh();
