/**
 * The home page's scroll story, and the small things that ride on scroll:
 *  - the story: a pinned section of five statements. As you scroll, each one's words light up
 *    one by one, and the swarm behind them gathers into what it says (swarm.js);
 *  - scrub: any [data-scrub] heading lights word by word as it rises through the viewport;
 *  - the rail: a thread of light down the left edge that fills as you go, one node per section;
 *  - spell: at the end, type a word and the fireflies spell it.
 * Reduced motion: the statements are a plain list, words are simply lit, the rail still tracks.
 */
const clamp = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

/** Wrap every word of el in a span, keeping its markup. Returns the spans in reading order. */
function split(el) {
  const words = [];
  const walk = (node) => {
    for (const c of [...node.childNodes]) {
      if (c.nodeType === 3) {
        const frag = document.createDocumentFragment();
        for (const part of c.textContent.split(/(\s+)/)) {
          if (!part) continue;
          if (/^\s+$/.test(part)) {
            frag.append(" ");
            continue;
          }
          const s = document.createElement("span");
          s.className = "sw";
          s.textContent = part;
          words.push(s);
          frag.append(s);
        }
        c.replaceWith(frag);
      } else if (c.nodeType === 1) {
        if (c.tagName === "KBD") {
          c.classList.add("sw");
          words.push(c);
        } else if (c.tagName !== "BR") walk(c);
      }
    }
  };
  walk(el);
  return words;
}
const light = (words, p) => {
  const n = words.length;
  words.forEach((w, i) =>
    w.style.setProperty(
      "--o",
      (0.16 + 0.84 * clamp(p * (n + 1) - i)).toFixed(2),
    ),
  );
};

function story(reduced, swarm) {
  const track = document.querySelector(".story-track");
  if (!track) return;
  const steps = [...track.querySelectorAll(".story-step")].map((el) => ({
    el,
    line: split(el.querySelector(".story-line")),
    sub: split(el.querySelector(".story-sub")),
  }));
  const bars = [...track.querySelectorAll(".story-bar i")];
  if (reduced) {
    // a plain list of the five statements; the swarm stays a quiet field behind them
    steps.forEach((s) => s.el.classList.add("on"));
    track.dataset.swarm = "field";
    return;
  }
  let shown = -1;
  let raf = 0;
  const update = () => {
    raf = 0;
    const vh = innerHeight;
    const r = track.getBoundingClientRect();
    const p = clamp(-r.top / (r.height - vh));
    const f = p * steps.length;
    const idx = Math.min(steps.length - 1, Math.floor(f));
    const local = clamp(f - idx);
    const inView = r.top < vh * 0.62 && r.bottom > vh * 0.45;
    const lead = clamp(1 - r.top / (vh * 0.62)); // the first line starts lighting as the section arrives
    steps.forEach((s, i) => {
      const on = inView && i === idx;
      s.el.classList.toggle("on", on);
      if (!on) return;
      light(s.line, Math.max(i === 0 ? lead * 0.7 : 0, local / 0.36));
      light(s.sub, (local - 0.22) / 0.42);
    });
    bars.forEach((b, i) =>
      b.style.setProperty(
        "--p",
        i < idx ? "1" : i === idx ? local.toFixed(3) : "0",
      ),
    );
    track.classList.toggle("started", p > 0.02);
    track.dataset.swarm = steps[idx].el.dataset.shape;
    swarm?.refresh();
    if (inView && idx !== shown) {
      shown = idx;
      window.pyxi?.say?.(steps[idx].el.dataset.say);
    }
    if (!inView) shown = -1;
  };
  const queue = () => (raf ||= requestAnimationFrame(update));
  addEventListener("scroll", queue, { passive: true });
  addEventListener("resize", queue);
  update();
}

function scrub(reduced) {
  const items = [...document.querySelectorAll("[data-scrub]")].map((el) => ({
    el,
    words: split(el),
  }));
  if (!items.length || reduced) return;
  let raf = 0;
  const update = () => {
    raf = 0;
    const vh = innerHeight;
    for (const it of items) {
      const r = it.el.getBoundingClientRect();
      if (r.bottom < -100 || r.top > vh + 100) continue;
      light(it.words, (vh * 0.92 - r.top) / (vh * 0.42));
    }
  };
  const queue = () => (raf ||= requestAnimationFrame(update));
  addEventListener("scroll", queue, { passive: true });
  addEventListener("resize", queue);
  update();
}

function rail() {
  const el = document.querySelector(".rail");
  if (!el) return;
  const links = [...el.querySelectorAll("a")];
  const targets = links.map((a) =>
    document.querySelector(a.getAttribute("href")),
  );
  if (targets.some((t) => !t)) return;
  let raf = 0;
  const update = () => {
    raf = 0;
    const mid = innerHeight * 0.5;
    const tops = targets.map((t) => t.getBoundingClientRect().top);
    let at = -1;
    tops.forEach((y, i) => y <= mid && (at = i));
    let p = 0;
    if (at >= 0) {
      const next = tops[at + 1];
      const frac =
        next === undefined
          ? 0
          : clamp((mid - tops[at]) / Math.max(1, next - tops[at]));
      p = (at + frac) / (links.length - 1);
    }
    el.style.setProperty("--p", clamp(p).toFixed(4));
    el.classList.toggle("on", at >= 0);
    links.forEach((a, i) => {
      a.classList.toggle("done", i <= at);
      a.classList.toggle("now", i === at);
      if (i === at) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
  };
  const queue = () => (raf ||= requestAnimationFrame(update));
  addEventListener("scroll", queue, { passive: true });
  addEventListener("resize", queue);
  update();
}

function spell(swarm) {
  const form = document.getElementById("spell");
  const input = document.getElementById("spell-input");
  const cue = form?.closest("[data-swarm]");
  if (!form || !input || !cue) return;
  if (!swarm) {
    form.hidden = true; // no WebGL, nothing to spell with
    return;
  }
  form.addEventListener("submit", (e) => e.preventDefault());
  // typing at the very end of the page: bring the place the word forms into view
  input.addEventListener("focus", () => {
    const stage = document.getElementById("spell-stage");
    const top = stage?.getBoundingClientRect().top ?? 200;
    if (top < 100) scrollTo({ top: scrollY + top - 110, behavior: "smooth" });
  });
  input.addEventListener("input", () => {
    const text = input.value.trim().slice(0, 12);
    cue.dataset.swarm = text ? `text:${text}` : "firefly";
    swarm.refresh();
  });
}

export function initStory({ reduced = false, swarm = null } = {}) {
  story(reduced, swarm);
  scrub(reduced);
  rail();
  spell(swarm);
}
