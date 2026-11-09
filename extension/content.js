// Pyxi in your browser: the part that runs in the page.
//
// The background script puts this file into a tab only when Pyxi has been asked to read or
// work there, and only on a site the browser has let the extension into. It answers a small
// set of requests: read the page, click, type, pick an option, press a key, scroll, show or
// hide the "Pyxi is working here" marker.
//
// What it refuses, here in the page, whatever the brain asks:
//   - typing into, changing or reading a password field, a card or bank details field, or an
//     ID number field (SIN, SSN, passport and the like);
//   - typing anything that looks like a card number;
//   - choosing files.
// It never touches the page's cookies or storage, and it runs no code sent from outside: the
// only things it can do are the ones written in this file.
(() => {
  "use strict";
  if (globalThis.__pyxiInTab) {
    globalThis.__pyxiInTab.revive(); // put in again after the site was allowed again
    return;
  }
  let retired = false; // the site was taken away in the popup: answer nothing until put in again
  globalThis.__pyxiInTab = {
    revive() {
      retired = false;
    },
  };

  const api = globalThis.browser || globalThis.chrome;

  class Refusal extends Error {
    constructor(code, message) {
      super(message);
      this.code = code;
    }
  }

  // ------------------------------------------------------------------ refs
  let counter = 0;
  const byRef = new Map(); // "e12" -> WeakRef(element)
  const refOf = new WeakMap(); // element -> "e12"

  function refFor(el) {
    let ref = refOf.get(el);
    if (!ref) {
      ref = "e" + ++counter;
      refOf.set(el, ref);
      byRef.set(ref, new WeakRef(el));
    }
    return ref;
  }

  function elementOf(ref) {
    const el = byRef.get(String(ref))?.deref();
    if (!el || !el.isConnected) {
      throw new Refusal("gone", "That element isn't on the page any more.");
    }
    return el;
  }

  function forgetGone() {
    for (const [ref, weak] of byRef) {
      const el = weak.deref();
      if (!el || !el.isConnected) byRef.delete(ref);
    }
  }

  // ------------------------------------------------------------------ private fields
  const PASSWORD =
    /\b(password|passwd|passcode|passphrase|pwd|pin|otp|totp|2fa|mfa)\b|one time (code|password|pin)|verification code|security (answer|question)|secret (key|word|answer)/;
  const CARD =
    /\b(card|cardholder|cardnumber|ccn|ccnum|ccnumber|cvv2?|cvc2?|csc|cvn|iban|swift|bic)\b|\bcc (num|number|no|exp|csc|cvc|cvv|name)|credit card|debit card|security code|expir(y|ation)|\bexp (date|month|year|mm|yy)|routing( number)?|sort code|account (number|no)\b|bank account/;
  const GOV_ID =
    /\b(ssn|sin|nino|tfn|itin|aadhaar|passport|medicare)\b|social (security|insurance)|national (id|identity|insurance)|driv(er'?s?|ing) ?licen[cs]e|licen[cs]e (number|no)\b|tax (id|number|file)|employer identification|health card|pan (number|card)|identity (number|card)|\bid number|personal (id|identity) (number|code)/;
  const everPassword = new WeakSet(); // "show password" turns one into a text field: still private

  function words(...parts) {
    return parts
      .filter(Boolean)
      .join(" ")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/[_\-.\[\]:/]+/g, " ")
      .toLowerCase();
  }

  function isField(el) {
    const tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  /** "password", "card", "id", "file" or "" for an element Pyxi may work with. */
  function privateKind(el) {
    if (!isField(el)) return "";
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (el.tagName === "INPUT" && type === "file") return "file";
    if (el.tagName === "INPUT" && type === "password") {
      everPassword.add(el);
      return "password";
    }
    if (everPassword.has(el)) return "password";
    if (el.tagName === "INPUT" && /^(checkbox|radio|submit|button|reset|image|range|color)$/.test(type)) {
      return "";
    }
    const auto = (el.getAttribute("autocomplete") || "").toLowerCase();
    if (/current-password|new-password|one-time-code/.test(auto)) return "password";
    if (/\bcc-/.test(auto)) return "card";
    const hay = words(
      el.getAttribute("name"),
      el.id,
      el.getAttribute("placeholder"),
      el.getAttribute("aria-label"),
      el.getAttribute("data-testid"),
      el.getAttribute("title"),
      fieldLabel(el),
    );
    if (PASSWORD.test(hay)) return "password";
    if (CARD.test(hay)) return "card";
    if (GOV_ID.test(hay)) return "id";
    return "";
  }

  const PRIVATE_WORDS = {
    password: "a password or code field",
    card: "a card or bank details field",
    id: "an ID number field",
    file: "a file picker",
  };

  function refuseIfPrivate(el) {
    const kind = privateKind(el);
    if (kind) {
      throw new Refusal("sensitive", `That's ${PRIVATE_WORDS[kind]}. Pyxi leaves those to you.`);
    }
  }

  function cardNumber(text) {
    for (const run of String(text || "").match(/(?<!\d)(?:\d[ -]?){13,19}(?!\d)/g) || []) {
      const digits = run.replace(/\D/g, "");
      if (digits.length < 13 || digits.length > 19) continue;
      let total = 0;
      for (let i = 0; i < digits.length; i++) {
        let d = Number(digits[digits.length - 1 - i]);
        if (i % 2) d = d > 4 ? d * 2 - 9 : d * 2;
        total += d;
      }
      if (total % 10 === 0) return true;
    }
    return false;
  }

  function hideNumbers(text) {
    return String(text || "")
      .replace(/(?<!\d)(?:\d[ -]?){13,19}(?!\d)/g, (m) => (cardNumber(m) ? "[card number hidden]" : m))
      .replace(/(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)/g, "[ID number hidden]");
  }

  // ------------------------------------------------------------------ describing elements
  const clean = (text, max = 90) => String(text || "").replace(/\s+/g, " ").trim().slice(0, max);

  /** A label's own words, without the text of a field it wraps (a dropdown's options). */
  function labelText(label) {
    let out = "";
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) out += child.nodeValue;
        else if (child.nodeType === Node.ELEMENT_NODE && !/^(SELECT|TEXTAREA|INPUT|BUTTON|SCRIPT|STYLE)$/.test(child.tagName)) {
          walk(child);
        }
      }
    };
    walk(label);
    return out;
  }

  function fieldLabel(el) {
    try {
      if (el.labels && el.labels.length) {
        return clean([...el.labels].map(labelText).join(" "));
      }
    } catch {
      /* not a labelable element */
    }
    return "";
  }

  function labelOf(el) {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim()) return clean(aria);
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const text = by
        .split(/\s+/)
        .map((id) => el.ownerDocument.getElementById(id)?.textContent || "")
        .join(" ");
      if (text.trim()) return clean(text);
    }
    const label = fieldLabel(el);
    if (label) return label;
    const tag = el.tagName;
    if (tag === "INPUT" && /^(submit|button|reset)$/i.test(el.type)) return clean(el.value || el.type);
    if (isField(el)) {
      return clean(el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("name") || "");
    }
    if (tag === "IMG") return clean(el.alt || el.title);
    const text = clean(el.textContent);
    if (text) return text;
    const inner = el.querySelector("img[alt], [aria-label], svg title, [title]");
    return clean(
      el.getAttribute("title") ||
        inner?.getAttribute("alt") ||
        inner?.getAttribute("aria-label") ||
        inner?.getAttribute("title") ||
        inner?.textContent ||
        el.getAttribute("name") ||
        "",
    );
  }

  const ROLE_KIND = {
    button: "button",
    link: "link",
    checkbox: "checkbox",
    radio: "radio button",
    tab: "tab",
    menuitem: "menu item",
    menuitemcheckbox: "menu item",
    menuitemradio: "menu item",
    option: "option",
    switch: "switch",
    combobox: "dropdown box",
    textbox: "text field",
    searchbox: "search box",
    slider: "slider",
    treeitem: "tree item",
  };

  function kindOf(el) {
    const tag = el.tagName;
    const role = (el.getAttribute("role") || "").toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "SELECT") return "dropdown";
    if (tag === "TEXTAREA") return "text area";
    if (tag === "INPUT") {
      if (/^(button|submit|reset|image)$/.test(type)) return "button";
      if (type === "checkbox") return role === "switch" ? "switch" : "checkbox";
      if (type === "radio") return "radio button";
      if (type === "file") return "file picker";
      if (type === "range") return "slider";
      if (type === "search" || role === "searchbox") return "search box";
      return role === "combobox" ? "dropdown box" : "text field";
    }
    if (ROLE_KIND[role]) return ROLE_KIND[role];
    if (tag === "A") return "link";
    if (tag === "BUTTON") return "button";
    if (tag === "SUMMARY") return "expander";
    if (el.isContentEditable) return "editable text";
    return "clickable";
  }

  function shown(el) {
    if (!el.isConnected) return false;
    if (typeof el.checkVisibility === "function") {
      return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    }
    const style = getComputedStyle(el);
    return style.display !== "none" && style.visibility === "visible" && style.opacity !== "0";
  }

  function onScreenBox(el) {
    if (!shown(el)) return null;
    const box = el.getBoundingClientRect();
    return box.width > 1 && box.height > 1 ? box : null;
  }

  /** Where the element can be seen. A checkbox or radio button is often hidden behind its
   * styled label: then the label's box counts. */
  function boxOf(el) {
    const own = onScreenBox(el);
    if (own) return own;
    if (el.tagName === "INPUT" && /^(checkbox|radio)$/i.test(el.type)) {
      for (const label of el.labels || []) {
        const box = onScreenBox(label);
        if (box) return box;
      }
    }
    return null;
  }

  function searchForm(form) {
    if (!form) return false;
    if ((form.getAttribute("role") || "").toLowerCase() === "search") return true;
    if (form.querySelector('input[type="search"], [role="searchbox"]')) return true;
    return /search|find|filter|lookup/.test(
      words(form.getAttribute("action"), form.id, form.getAttribute("name"), form.className),
    );
  }

  function searchy(el) {
    const type = (el.getAttribute("type") || "").toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (type === "search" || role === "searchbox") return true;
    if (el.form && searchForm(el.form)) return true;
    return /\b(search|find|filter|query)\b/.test(words(el.getAttribute("name"), el.id, labelOf(el)));
  }

  function submits(el) {
    const tag = el.tagName;
    const type = (el.getAttribute("type") || "").toLowerCase();
    const isSubmit =
      (tag === "BUTTON" && (type === "submit" || type === "")) ||
      (tag === "INPUT" && (type === "submit" || type === "image"));
    return isSubmit && !!el.form && !searchForm(el.form);
  }

  function describe(el) {
    const item = { ref: refFor(el), kind: kindOf(el), label: labelOf(el) };
    const secret = privateKind(el);
    if (secret) {
      // Listed so Pyxi knows it's there and leaves it to the user; its value is never read.
      item.sensitive = secret;
      if (secret === "password") item.kind = "password field";
      return item;
    }
    const tag = el.tagName;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "INPUT" && (type === "checkbox" || type === "radio")) {
      item.checked = !!el.checked;
    } else if (tag === "SELECT") {
      item.value = clean(el.selectedOptions[0]?.textContent || "", 60);
      item.options = [...el.options].slice(0, 14).map((o) => clean(o.textContent, 40));
    } else if (tag === "INPUT" || tag === "TEXTAREA") {
      if (!/^(button|submit|reset|image)$/.test(type) && el.value) {
        item.value = hideNumbers(clean(el.value, 120));
      }
      if (searchy(el)) item.search = true;
    } else if (el.isContentEditable) {
      // Its own text is what's in it, not what it's called.
      item.label = clean(
        el.getAttribute("aria-label") || el.getAttribute("aria-placeholder") || el.getAttribute("data-placeholder") || "text box",
      );
      const text = clean(el.textContent, 120);
      if (text) item.value = hideNumbers(text);
    } else {
      const state = el.getAttribute("aria-checked") ?? el.getAttribute("aria-pressed") ?? el.getAttribute("aria-selected");
      if (state === "true" || state === "false") item.checked = state === "true";
    }
    if (el.disabled || el.getAttribute("aria-disabled") === "true") item.disabled = true;
    if (submits(el)) item.submits = true;
    const link = tag === "A" ? el : null;
    if (link && link.href) {
      try {
        const to = new URL(link.href);
        if (/^https?:$/.test(to.protocol)) {
          item.href = (to.host === location.host ? "" : to.host) + to.pathname.slice(0, 60);
        }
      } catch {
        /* not an address */
      }
    }
    return item;
  }

  // ------------------------------------------------------------------ reading the page
  const CLICKABLE =
    'a[href], button, input:not([type="hidden"]), select, textarea, summary, [contenteditable=""], ' +
    '[contenteditable="true"], [contenteditable="plaintext-only"], [onclick], [role="button"], [role="link"], [role="checkbox"], [role="radio"], ' +
    '[role="tab"], [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"], ' +
    '[role="option"], [role="switch"], [role="combobox"], [role="textbox"], [role="searchbox"], ' +
    '[role="treeitem"], [tabindex]:not([tabindex="-1"])';
  const NO_TEXT = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "TEXTAREA", "SELECT", "OPTION", "TITLE", "IFRAME", "OBJECT", "SVG", "svg", "CANVAS", "HEAD"]);
  const BLOCKS =
    "p,div,li,tr,h1,h2,h3,h4,h5,h6,section,article,header,footer,nav,main,aside,blockquote,pre,dd,dt,figcaption,td,th,label,form,table,ul,ol,details,summary,address,button,option";

  /** The page, its open shadow trees and the frames it may look into (same site only). */
  function roots() {
    const found = [document];
    let looked = 0;
    for (let i = 0; i < found.length && found.length < 40; i++) {
      for (const el of found[i].querySelectorAll("*")) {
        if (++looked > 30000) return found;
        if (el.shadowRoot) found.push(el.shadowRoot);
        else if (el.tagName === "IFRAME") {
          try {
            if (el.contentDocument?.body) found.push(el.contentDocument);
          } catch {
            /* another site's frame: not ours to read */
          }
        }
      }
    }
    return found;
  }

  function inside(el, set) {
    for (let up = el.parentElement; up; up = up.parentElement) if (set.has(up)) return true;
    return false;
  }

  function collect(max) {
    const picked = new Set();
    const every = roots();
    for (const root of every) {
      for (const el of root.querySelectorAll(CLICKABLE)) {
        if (el === markerHost) continue;
        picked.add(el);
      }
    }
    // Things that are only clickable because a script listens on them show a hand cursor.
    if (picked.size < 400) {
      let looked = 0;
      for (const root of every) {
        for (const el of root.querySelectorAll("div, span, li, td, img, label, p, i, svg")) {
          if (++looked > 3000) break;
          if (picked.has(el) || el === markerHost) continue;
          if (getComputedStyle(el).cursor !== "pointer") continue;
          const up = el.parentElement;
          if (up && getComputedStyle(up).cursor === "pointer") continue; // the outermost one
          if (inside(el, picked) || el.querySelector(CLICKABLE)) continue;
          picked.add(el);
        }
      }
    }
    const here = [];
    const beyond = [];
    const height = innerHeight;
    for (const el of picked) {
      const box = boxOf(el);
      if (!box) continue;
      if (el.tagName === "LABEL" && el.control) continue; // its field is listed
      (box.bottom > 0 && box.top < height ? here : beyond).push(el);
    }
    const all = [...here, ...beyond]; // what's on screen first
    return { items: all.slice(0, max).map(describe), more: all.length > max };
  }

  function pageText(max, fromView) {
    const out = [];
    let size = 0;
    let more = false;
    let lastBlock = null;
    const seenBlocks = new Map();
    const above = (block) => {
      if (!fromView || !block) return false;
      let is = seenBlocks.get(block);
      if (is === undefined) {
        is = block.getBoundingClientRect().bottom < 0;
        seenBlocks.set(block, is);
      }
      return is;
    };
    const walk = (node) => {
      for (let child = node.firstChild; child && !more; child = child.nextSibling) {
        if (child.nodeType === Node.TEXT_NODE) {
          const raw = child.nodeValue;
          if (!raw || !raw.trim()) continue;
          const parent = child.parentElement;
          const block = parent ? parent.closest(BLOCKS) : null;
          if (above(block)) continue;
          if (block !== lastBlock && out.length) out.push("\n");
          lastBlock = block;
          const piece = raw.replace(/\s+/g, " ");
          out.push(piece);
          size += piece.length;
          if (size >= max) more = true;
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          if (child === markerHost || NO_TEXT.has(child.tagName)) {
            if (child.tagName === "IFRAME") {
              try {
                if (child.contentDocument?.body && shown(child)) walk(child.contentDocument.body);
              } catch {
                /* another site's frame */
              }
            }
            continue;
          }
          if (!shown(child)) continue; // hidden: not something the user sees, so not read
          if (child.shadowRoot) walk(child.shadowRoot);
          walk(child);
        }
      }
    };
    if (document.body) walk(document.body);
    const text = hideNumbers(out.join("").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim());
    return { text: text.slice(0, max), more };
  }

  function scroller() {
    const root = document.scrollingElement || document.documentElement;
    if (root.scrollHeight - root.clientHeight > 40) return root;
    // An app that scrolls a panel instead of the page: the biggest scrolling box on screen.
    let best = null;
    let area = 0;
    let looked = 0;
    for (const el of document.querySelectorAll("main, div, section, article, ul, ol, [role=main], [role=feed]")) {
      if (++looked > 4000) break;
      if (el.scrollHeight - el.clientHeight < 40) continue;
      if (!/(auto|scroll)/.test(getComputedStyle(el).overflowY)) continue;
      const box = el.getBoundingClientRect();
      if (box.width * box.height > area) {
        area = box.width * box.height;
        best = el;
      }
    }
    return best || root;
  }

  function scrollState() {
    const el = scroller();
    return { y: Math.round(el.scrollTop), max: Math.max(0, Math.round(el.scrollHeight - el.clientHeight)) };
  }

  function read({ max_chars = 4000, max_items = 90, from_view = true } = {}) {
    forgetGone();
    const text = pageText(Math.min(20000, Math.max(200, max_chars)), from_view);
    const list = collect(Math.min(200, Math.max(1, max_items)));
    return {
      text: text.text,
      more_text: text.more,
      items: list.items,
      more_items: list.more,
      scroll: scrollState(),
    };
  }

  // ------------------------------------------------------------------ the marker
  let markerHost = null;
  let parts = null;
  let working = false;

  const MARKER_CSS = `
    :host { all: initial; }
    .frame { position: fixed; inset: 0; border: 2px solid rgba(255, 190, 80, 0.85);
      box-shadow: inset 0 0 18px rgba(255, 190, 80, 0.35); pointer-events: none; }
    .pill { position: fixed; right: 14px; bottom: 14px; display: flex; align-items: center; gap: 8px;
      padding: 7px 8px 7px 12px; border-radius: 999px; background: rgba(10, 11, 15, 0.94);
      color: #f4f7fb; font: 500 12.5px/1.2 system-ui, "Segoe UI", sans-serif; letter-spacing: 0;
      box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35), 0 0 0 1px rgba(255, 255, 255, 0.12);
      pointer-events: auto; user-select: none; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #ffbe50;
      box-shadow: 0 0 8px #ffbe50; animation: glow 1.4s ease-in-out infinite; }
    @keyframes glow { 50% { opacity: 0.45; } }
    .hint { color: rgba(232, 238, 248, 0.62); }
    button { all: unset; cursor: pointer; padding: 3px 10px; border-radius: 999px;
      background: rgba(255, 255, 255, 0.12); color: #f4f7fb; font: inherit; }
    button:hover { background: rgba(255, 255, 255, 0.22); }
    .ring { position: fixed; border: 2px solid #ffbe50; border-radius: 6px; pointer-events: none;
      box-shadow: 0 0 0 4px rgba(255, 190, 80, 0.25); opacity: 0; transition: opacity 0.25s; }
    .hidden { display: none; }
  `;

  function ui() {
    if (markerHost && markerHost.isConnected) return parts;
    markerHost = document.createElement("pyxi-marker");
    markerHost.style.cssText =
      "all: initial; position: fixed; inset: 0; pointer-events: none; z-index: 2147483647;";
    const shadow = markerHost.attachShadow({ mode: "closed" });
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(MARKER_CSS);
      shadow.adoptedStyleSheets = [sheet];
    } catch {
      const style = document.createElement("style");
      style.textContent = MARKER_CSS;
      shadow.appendChild(style);
    }
    const make = (tag, cls, text) => {
      const el = document.createElement(tag);
      el.className = cls;
      if (text) el.textContent = text;
      shadow.appendChild(el);
      return el;
    };
    const frame = make("div", "frame hidden");
    const pill = make("div", "pill hidden");
    const ring = make("div", "ring");
    const dot = document.createElement("span");
    dot.className = "dot";
    const say = document.createElement("span");
    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = "Esc to stop";
    const stop = document.createElement("button");
    stop.type = "button";
    stop.textContent = "Stop";
    stop.addEventListener("click", (e) => {
      if (e.isTrusted) halt("button");
    });
    pill.append(dot, say, hint, stop);
    document.documentElement.appendChild(markerHost);
    parts = { frame, pill, ring, say };
    show(); // a page that removed the marker gets it back, as it was
    return parts;
  }

  let markerText = "";
  function show() {
    parts.say.textContent = markerText || "Pyxi is working in this tab";
    parts.frame.classList.toggle("hidden", !working);
    parts.pill.classList.toggle("hidden", !working);
  }

  function marker(on, text) {
    working = !!on;
    if (text) markerText = String(text).slice(0, 80);
    if (!on && !markerHost) return { on: false }; // never shown: nothing to hide
    ui();
    show();
    return { on: working };
  }

  let ringTimer = 0;
  function flash(box) {
    const p = ui();
    Object.assign(p.ring.style, {
      left: `${box.left - 3}px`,
      top: `${box.top - 3}px`,
      width: `${box.width + 2}px`,
      height: `${box.height + 2}px`,
      opacity: "1",
    });
    clearTimeout(ringTimer);
    ringTimer = setTimeout(() => (p.ring.style.opacity = "0"), 700);
  }

  function halt(by) {
    if (!working) return;
    marker(false);
    try {
      api.runtime.sendMessage({ to: "pyxi-bg", type: "stopped", by });
    } catch {
      /* the extension was reloaded: nothing to tell */
    }
  }

  // Esc in the tab stops Pyxi. Only a real key press counts: the page can't fake one, and
  // Pyxi's own "press Escape" (made by this script) doesn't stop itself.
  window.addEventListener(
    "keydown",
    (e) => {
      if (!working || e.key !== "Escape" || !e.isTrusted) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      halt("esc");
    },
    true,
  );

  // ------------------------------------------------------------------ acting
  function usable(el) {
    refuseIfPrivate(el);
    if (el.disabled || el.getAttribute("aria-disabled") === "true") {
      throw new Refusal("failed", "That's switched off on the page right now (disabled).");
    }
  }

  function bringIntoView(el) {
    try {
      el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
    } catch {
      el.scrollIntoView();
    }
    const box = boxOf(el) || el.getBoundingClientRect();
    flash(box);
    return box;
  }

  function sameOrInside(a, b) {
    // Is a inside b, shadow trees included?
    for (let node = a; node; node = node.parentNode || node.host) if (node === b) return true;
    return false;
  }

  function click({ ref }) {
    const el = elementOf(ref);
    usable(el);
    const box = bringIntoView(el);
    const x = box.left + box.width / 2;
    const y = box.top + box.height / 2;
    const link = el.closest ? el.closest("a[href]") : null;
    if (link && link.target === "_blank") {
      // A page can't open a new tab for a click it didn't get from a person, so the
      // background script opens the address instead.
      if (/^https?:$/.test(new URL(link.href, location.href).protocol)) return { open: link.href };
    }
    const top = (el.getRootNode().elementFromPoint || document.elementFromPoint).call(el.getRootNode(), x, y);
    const checkable = el.tagName === "INPUT" && /^(checkbox|radio)$/i.test(el.type);
    const covered = !!top && !checkable && !sameOrInside(top, el) && !sameOrInside(el, top);
    const at = { bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y, button: 0 };
    const pointer = { ...at, pointerId: 1, pointerType: "mouse", isPrimary: true };
    el.dispatchEvent(new PointerEvent("pointerover", pointer));
    el.dispatchEvent(new MouseEvent("mouseover", at));
    el.dispatchEvent(new PointerEvent("pointerdown", { ...pointer, buttons: 1 }));
    el.dispatchEvent(new MouseEvent("mousedown", { ...at, buttons: 1 }));
    if (typeof el.focus === "function") el.focus({ preventScroll: true });
    el.dispatchEvent(new PointerEvent("pointerup", pointer));
    el.dispatchEvent(new MouseEvent("mouseup", at));
    el.dispatchEvent(new MouseEvent("click", at));
    return covered ? { covered: true } : {};
  }

  const TYPEABLE = /^(|text|search|email|url|tel|number|date|time|datetime-local|month|week)$/;

  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
  }

  function type({ ref, text = "", enter = false }) {
    const el = elementOf(ref);
    usable(el);
    text = String(text);
    if (cardNumber(text)) {
      throw new Refusal("sensitive", "That looks like a card number. Pyxi never types those.");
    }
    const tag = el.tagName;
    const kind = (el.getAttribute("type") || "").toLowerCase();
    let value;
    if ((tag === "INPUT" && TYPEABLE.test(kind)) || tag === "TEXTAREA") {
      if (el.readOnly) throw new Refusal("failed", "That field can't be changed (read only).");
      bringIntoView(el);
      el.focus({ preventScroll: true });
      const max = el.maxLength > 0 ? el.maxLength : Infinity;
      setValue(el, text.slice(0, max));
      el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      value = el.value;
    } else if (el.isContentEditable) {
      bringIntoView(el);
      el.focus({ preventScroll: true });
      const selection = getSelection();
      selection.selectAllChildren(el);
      let done = false;
      try {
        done = document.execCommand("insertText", false, text);
      } catch {
        done = false;
      }
      if (!done) {
        el.textContent = text;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
      }
      value = el.textContent;
    } else {
      throw new Refusal("failed", "That isn't something text can be typed into. Click it instead.");
    }
    const out = { value: hideNumbers(clean(value, 200)) };
    if (enter) press({ key: "Enter", ref });
    return out;
  }

  function select({ ref, option = "" }) {
    const el = elementOf(ref);
    usable(el);
    const want = clean(option).toLowerCase();
    if (el.tagName === "SELECT") {
      const options = [...el.options];
      const text = (o) => clean(o.textContent).toLowerCase();
      const hit =
        options.find((o) => text(o) === want || o.value.toLowerCase() === want) ||
        options.find((o) => text(o).startsWith(want)) ||
        options.find((o) => want && text(o).includes(want));
      if (!hit) {
        throw new Refusal(
          "failed",
          "No option like that. The options are: " + options.slice(0, 14).map((o) => clean(o.textContent, 40)).join(", "),
        );
      }
      if (hit.disabled) throw new Refusal("failed", "That option is switched off on the page.");
      bringIntoView(el);
      el.focus({ preventScroll: true });
      el.value = hit.value;
      hit.selected = true;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { value: clean(hit.textContent, 60) };
    }
    if (el.tagName === "INPUT" && /^(checkbox|radio)$/i.test(el.type)) {
      const on = !/^(off|no|false|uncheck|unchecked|0)$/.test(want);
      if (el.checked !== on) click({ ref });
      return { checked: el.checked };
    }
    throw new Refusal("failed", "That isn't a plain dropdown. Click it, read the page again, then click the option.");
  }

  const KEYS = {
    enter: ["Enter", "Enter", 13],
    return: ["Enter", "Enter", 13],
    esc: ["Escape", "Escape", 27],
    escape: ["Escape", "Escape", 27],
    tab: ["Tab", "Tab", 9],
    space: [" ", "Space", 32],
    backspace: ["Backspace", "Backspace", 8],
    delete: ["Delete", "Delete", 46],
    up: ["ArrowUp", "ArrowUp", 38],
    down: ["ArrowDown", "ArrowDown", 40],
    left: ["ArrowLeft", "ArrowLeft", 37],
    right: ["ArrowRight", "ArrowRight", 39],
    arrowup: ["ArrowUp", "ArrowUp", 38],
    arrowdown: ["ArrowDown", "ArrowDown", 40],
    arrowleft: ["ArrowLeft", "ArrowLeft", 37],
    arrowright: ["ArrowRight", "ArrowRight", 39],
    pageup: ["PageUp", "PageUp", 33],
    pagedown: ["PageDown", "PageDown", 34],
    home: ["Home", "Home", 36],
    end: ["End", "End", 35],
  };

  function keyOf(combo) {
    const bits = String(combo || "").trim().split("+").map((b) => b.trim()).filter(Boolean);
    const name = (bits.pop() || "").toLowerCase();
    const mods = new Set(bits.map((b) => b.toLowerCase()));
    let key, code, keyCode;
    if (KEYS[name]) [key, code, keyCode] = KEYS[name];
    else if (name.length === 1) {
      key = mods.has("shift") ? name.toUpperCase() : name;
      code = /[a-z]/.test(name) ? "Key" + name.toUpperCase() : /\d/.test(name) ? "Digit" + name : "";
      keyCode = name.toUpperCase().charCodeAt(0);
    } else throw new Refusal("failed", `There's no key called "${combo}".`);
    return {
      key,
      code,
      keyCode,
      which: keyCode,
      ctrlKey: mods.has("ctrl") || mods.has("control"),
      shiftKey: mods.has("shift"),
      altKey: mods.has("alt"),
      metaKey: mods.has("meta") || mods.has("cmd") || mods.has("command"),
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
    };
  }

  function deepActive() {
    let el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    return el || document.body;
  }

  function press({ key, ref }) {
    const el = ref ? elementOf(ref) : deepActive();
    refuseIfPrivate(el);
    const init = keyOf(key);
    if (ref && typeof el.focus === "function") el.focus({ preventScroll: true });
    const went = el.dispatchEvent(new KeyboardEvent("keydown", init));
    const plain = !init.ctrlKey && !init.altKey && !init.metaKey;
    if (went && plain) {
      // What the browser itself would do for a real key, for the keys that matter.
      const tag = el.tagName;
      if (init.key === "Enter") {
        if (tag === "INPUT" && el.form && !/^(button|submit|reset|checkbox|radio|file)$/i.test(el.type)) {
          if (typeof el.form.requestSubmit === "function") el.form.requestSubmit();
          else el.form.submit();
        } else if (tag === "BUTTON" || tag === "A" || tag === "SUMMARY" || el.getAttribute("role") === "button") {
          el.click();
        }
      } else if (init.key === " ") {
        if (tag === "BUTTON" || (tag === "INPUT" && /^(checkbox|radio|button|submit)$/i.test(el.type))) el.click();
      } else if (init.key === "PageDown" || init.key === "PageUp" || init.key === "Home" || init.key === "End") {
        scroll({ direction: { PageDown: "down", PageUp: "up", Home: "top", End: "bottom" }[init.key] });
      } else if ((init.key === "ArrowDown" || init.key === "ArrowUp") && tag === "SELECT") {
        const next = el.selectedIndex + (init.key === "ArrowDown" ? 1 : -1);
        if (next >= 0 && next < el.options.length) {
          el.selectedIndex = next;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
    }
    el.dispatchEvent(new KeyboardEvent("keyup", init));
    return {};
  }

  function scroll({ direction = "down", ref } = {}) {
    if (ref) {
      const el = elementOf(ref);
      bringIntoView(el);
      return { ...scrollState(), moved: true };
    }
    const el = scroller();
    const before = el.scrollTop;
    const page = Math.max(120, el.clientHeight * 0.85);
    const top =
      direction === "top" ? 0 : direction === "bottom" ? el.scrollHeight : before + (direction === "up" ? -page : page);
    el.scrollTo({ top, behavior: "instant" });
    return { ...scrollState(), moved: Math.abs(el.scrollTop - before) > 1 };
  }

  // ------------------------------------------------------------------ requests
  const DOES = { read, click, type, select, press, scroll };

  api.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (!msg || msg.to !== "pyxi-content") return undefined;
    let answer;
    try {
      if (msg.cmd === "retire") {
        retired = true;
        answer = { ok: true, data: marker(false) };
      } else if (retired) answer = { ok: false, code: "retired", error: "Pyxi was taken off this site." };
      else if (msg.cmd === "marker") answer = { ok: true, data: marker(msg.args?.on, msg.args?.text) };
      else if (msg.cmd === "stop") answer = { ok: true, data: marker(false) };
      else if (DOES[msg.cmd]) answer = { ok: true, data: DOES[msg.cmd](msg.args || {}) };
      else answer = { ok: false, code: "failed", error: "Pyxi's extension doesn't know how to do that." };
    } catch (err) {
      answer = { ok: false, code: err instanceof Refusal ? err.code : "failed", error: String(err?.message || err) };
    }
    respond(answer);
    return undefined;
  });
})();
