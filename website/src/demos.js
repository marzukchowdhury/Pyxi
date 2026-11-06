/**
 * "Watch it work": three short scripted demos, drawn in Pyxi's own UI language.
 *
 *   explain  a cue card writes itself beside a social post; numbered outlines and threads of
 *            light tie each point to the exact part of the post it means
 *   email    Pyxi drafts a reply, asks first, and sends only after your cursor presses Allow
 *   meet     Pyxi finds the free slot, adds the event, asks, then sends the invite
 *
 * Each demo plays when it's on screen, ticks through its numbered steps, and hands over to the
 * next tab. Each stage is a fixed 880 x 560 canvas scaled to fit. With reduced motion, each demo
 * jumps straight to its finished state.
 */
const SVGNS = "http://www.w3.org/2000/svg";
const W = 880;

export function initDemos({ reduced = false } = {}) {
  const root = document.getElementById("demos");
  if (!root) return;
  const tabs = [...root.querySelectorAll('[role="tab"]')];
  const panels = tabs.map((t) => document.getElementById(t.getAttribute("aria-controls")));

  // Scale each 880-wide canvas to its stage.
  const fit = new ResizeObserver((entries) => {
    for (const e of entries) if (e.contentRect.width) e.target.style.setProperty("--s", String(e.contentRect.width / W));
  });
  panels.forEach((p) => fit.observe(p.querySelector(".demo-stage")));

  let current = 0;
  let run = 0;
  let visible = false;

  const sleep = (ms) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));

  function select(i, { focus = false } = {}) {
    current = (i + tabs.length) % tabs.length;
    tabs.forEach((t, k) => {
      const on = k === current;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      t.style.setProperty("--p", "0");
      panels[k].hidden = !on;
      panels[k].classList.toggle("is-on", on);
    });
    if (focus) tabs[current].focus();
  }

  async function play(i) {
    const my = ++run;
    select(i);
    await sleep(60); // let the panel lay out (and scale) before measuring anything
    const ok = () => my === run;
    const panel = panels[current];
    const stage = rig(panel, ok, sleep, reduced);
    const steps = [...panel.querySelectorAll(".demo-steps li")];
    const tab = tabs[current];
    stage.reset();
    const step = (n) => {
      steps.forEach((li, k) => {
        li.classList.toggle("done", k < n);
        li.classList.toggle("now", k === n);
      });
      tab.style.setProperty("--p", String((n + 1) / steps.length));
    };
    step(-1);
    await sleep(500);
    const finished = await SCRIPTS[panel.dataset.demo](stage, step, ok);
    if (!finished || !ok() || reduced) return;
    await sleep(3200);
    if (ok() && visible && !document.hidden) play(current + 1);
  }

  tabs.forEach((t, k) => {
    t.addEventListener("click", () => play(k));
    t.addEventListener("keydown", (e) => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      select(current + d, { focus: true });
      play(current);
    });
  });
  new IntersectionObserver(
    ([e]) => {
      const was = visible;
      visible = e.isIntersecting;
      if (visible && !was) play(current);
      if (!visible && was) run++; // stop; it starts over when you come back
    },
    { threshold: 0.3 },
  ).observe(root.querySelector(".demo-tabs").parentElement);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) run++;
    else if (visible) play(current);
  });
}

/** Helpers for one stage: positions in canvas units, the notch, typing, the firefly and cursor. */
function rig(panel, ok, sleep, reduced) {
  const ds = panel.querySelector(".ds");
  const $ = (s) => ds.querySelector(s);
  const notch = $(".ds-notch");
  const nText = notch.querySelector("span");
  const fly = $(".ds-fly");
  const cursor = $(".ds-cursor");
  const scale = () => parseFloat(panel.querySelector(".demo-stage").style.getPropertyValue("--s")) || 1;
  const box = (el) => {
    const r = el.getBoundingClientRect();
    const b = ds.getBoundingClientRect();
    const k = scale();
    return { x: (r.left - b.left) / k, y: (r.top - b.top) / k, w: r.width / k, h: r.height / k };
  };
  const lines = (el) => {
    const b = ds.getBoundingClientRect();
    const k = scale();
    return [...el.getClientRects()].map((r) => ({ x: (r.left - b.left) / k, y: (r.top - b.top) / k, w: r.width / k, h: r.height / k }));
  };
  const place = (el, x, y) => {
    el.style.setProperty("--x", `${x.toFixed(1)}px`);
    el.style.setProperty("--y", `${y.toFixed(1)}px`);
  };
  const s = {
    $,
    panel,
    ok,
    sleep,
    box,
    lines,
    reset() {
      const marks = ["on", "open", "gone", "sent", "press", "pressed", "typing", "done", "pick"];
      ds.querySelectorAll(marks.map((m) => `.${m}`).join(",")).forEach((el) => el.classList.remove(...marks));
      ds.querySelectorAll("[data-text]").forEach((el) => (el.textContent = ""));
      const ink = $(".ds-ink");
      if (ink) ink.replaceChildren();
      notch.classList.remove("listening");
      nText.textContent = "Hold Ctrl Alt and just talk";
      place(fly, 820, 500);
      if (cursor) place(cursor, 760, 600);
    },
    /** The notch: plain status, or what you said (typed out, in quotes). */
    async say(text, { heard = false } = {}) {
      notch.classList.toggle("listening", heard);
      nText.className = heard ? "q" : "";
      if (!heard || reduced) {
        nText.textContent = text;
        return ok();
      }
      nText.textContent = "";
      for (let i = 1; i <= text.length; i++) {
        if (!ok()) return false;
        nText.textContent = text.slice(0, i);
        await sleep(26);
      }
      notch.classList.remove("listening");
      return ok();
    },
    async type(el, text = el.dataset.text) {
      el.classList.add("typing");
      if (reduced) el.textContent = text;
      else
        for (let i = 1; i <= text.length; i++) {
          if (!ok()) return false;
          el.textContent = text.slice(0, i);
          await sleep(text[i - 1] === " " ? 34 : 22);
        }
      el.classList.remove("typing");
      return ok();
    },
    /** The firefly flies next to an element (dx, dy from its top-left corner). */
    flyTo(el, dx = -14, dy = -14) {
      const b = box(el);
      place(fly, b.x + dx, b.y + dy);
    },
    /** Your cursor moves to an element and clicks it. */
    async click(el) {
      const b = box(el);
      cursor.classList.add("on");
      await sleep(80);
      place(cursor, b.x + b.w / 2 - 2, b.y + b.h / 2 - 2);
      await sleep(1000);
      if (!ok()) return false;
      cursor.classList.add("press");
      el.classList.add("pressed");
      await sleep(260);
      el.classList.remove("pressed");
      return ok();
    },
  };
  return s;
}

// ------------------------------------------------------------------ ink, for the cue card
function svg(name, attrs) {
  const el = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}
/** A slightly wobbly hand-drawn underline along one line of text. */
function underline(r) {
  const y = r.y + r.h + 2;
  let d = `M ${r.x - 2} ${y.toFixed(1)}`;
  for (let x = r.x + 18, i = 0; x < r.x + r.w + 4; x += 18, i++) d += ` L ${x.toFixed(1)} ${(y + (i % 2 ? 1.1 : -0.9)).toFixed(1)}`;
  return d;
}
const roundRect = (b, pad = 5, r = 7) => {
  const x = b.x - pad, y = b.y - pad, w = b.w + pad * 2, h = b.h + pad * 2;
  return `M ${x + r} ${y} H ${x + w - r} Q ${x + w} ${y} ${x + w} ${y + r} V ${y + h - r} Q ${x + w} ${y + h} ${x + w - r} ${y + h} H ${x + r} Q ${x} ${y + h} ${x} ${y + h - r} V ${y + r} Q ${x} ${y} ${x + r} ${y} Z`;
};
async function draw(path, sleep) {
  void path.getBoundingClientRect();
  path.classList.add("on");
  await sleep(950);
}

const SCRIPTS = {
  async movie(s, step, ok) {
    const hud = s.$(".hud");
    const hudText = s.$(".hud-text");
    const hudStep = s.$(".hud-step");
    const report = s.$(".notch-card");
    const req = s.$(".ds-req");
    const progress = (n, text) => {
      hudStep.textContent = `Step ${n}`;
      hudText.textContent = text;
    };
    step(0);
    if (!(await s.say("“Find what's on tonight at the closest cinema. Don't interrupt me.”", { heard: true }))) return false;
    await s.sleep(300);
    step(1);
    await s.say("Scout's on it. I'll stay quiet until it's ready.");
    progress(1, "Searching cinemas near you");
    hud.classList.add("on");
    s.flyTo(hud, 300, 10);
    await s.sleep(1800);
    if (!ok()) return false;
    step(2);
    await s.say("Scout is working");
    const typing = s.type(s.$(".doc-typed")); // you, carrying on with your own work
    await s.sleep(1500);
    progress(2, "Northgate Cinema, 1.2 km away");
    await s.sleep(1600);
    progress(3, "Reading tonight's showtimes");
    await s.sleep(1600);
    if (!ok() || !(await typing)) return false;
    progress(4, "Done: 3 movies tonight");
    hud.classList.add("done");
    step(3);
    report.classList.add("on");
    s.flyTo(report, -14, 30);
    await s.say("Scout found 3 movies tonight");
    await s.sleep(2400);
    if (!ok()) return false;
    step(4);
    if (!(await s.say("“Get two tickets for the 7:40.”", { heard: true }))) return false;
    report.querySelector("li").classList.add("pick");
    await s.sleep(500);
    req.classList.add("on");
    s.flyTo(req, -12, -10);
    await s.say("Waiting for your OK");
    await s.sleep(1200);
    if (!(await s.click(s.$(".t-allow")))) return false;
    req.classList.add("gone");
    progress(5, "Seats F7 and F8 picked, at checkout");
    await s.sleep(250);
    s.$(".ds-toast").classList.add("on");
    await s.say("Checkout is ready for you to pay");
    return ok();
  },

  async explain(s, step, ok) {
    const ink = s.$(".ds-ink");
    const card = s.$(".fn");
    const text = s.panel.dataset;
    step(0);
    if (!(await s.say(text.ask, { heard: true }))) return false;
    await s.sleep(400);
    step(1);
    await s.say(text.look);
    s.flyTo(s.$(".post, .map-area"), 240, 200);
    await s.sleep(1300);
    if (!ok()) return false;
    step(2);
    card.classList.add("on");
    s.flyTo(card, -10, 18);
    await s.say(text.write);
    await s.sleep(900);
    const items = [...card.querySelectorAll("li")];
    for (const [n, li] of items.entries()) {
      if (!ok()) return false;
      if (n === 0) step(3);
      if (n === items.length - 1 && items.length > 2) step(4);
      const target = s.$(`.${li.dataset.for}`);
      const t = s.box(target);
      let end;
      if (li.classList.contains("take")) {
        // The caption gets a hand-drawn underline, one stroke per line of text.
        for (const r of s.lines(target)) {
          const u = svg("path", { d: underline(r), class: "ink", pathLength: 1 });
          ink.append(u);
          draw(u, s.sleep);
        }
        const last = s.lines(target).at(-1);
        end = { x: last.x + last.w + 2, y: last.y + last.h + 2 };
      } else {
        const o = svg("path", { d: roundRect(t), class: "ink dash" });
        const badge = svg("g", { class: "badge", transform: `translate(${t.x - 5} ${t.y - 5})` });
        badge.append(svg("circle", { r: 9 }), Object.assign(svg("text", {}), { textContent: String(n + 1) }));
        ink.append(o, badge);
        void o.getBoundingClientRect();
        o.classList.add("on");
        badge.classList.add("on");
        end = { x: t.x + t.w + 5, y: t.y + t.h / 2 };
      }
      s.flyTo(target, t.w / 2, -16);
      li.classList.add("on");
      const l = s.box(li);
      const sx = l.x + 6;
      const sy = l.y + 10;
      const thread = svg("path", {
        d: `M ${sx} ${sy} C ${sx - 70} ${sy}, ${end.x + 80} ${end.y}, ${end.x} ${end.y}`,
        class: "ink thread",
        pathLength: 1,
      });
      ink.append(thread);
      await draw(thread, s.sleep);
      await s.sleep(650);
    }
    s.flyTo(card, card.offsetWidth - 26, card.offsetHeight - 22);
    step(4);
    await s.say(text.end);
    return ok();
  },

  async email(s, step, ok) {
    const mail = s.$(".mail");
    const compose = s.$(".compose");
    const req = s.$(".ds-req");
    step(0);
    if (!(await s.say("“Reply to Sam: thanks, dates are right, signed copy Friday.”", { heard: true }))) return false;
    await s.sleep(400);
    step(1);
    await s.say("Looking at your email");
    s.flyTo(s.$(".t-sam"), 230, 14);
    await s.sleep(700);
    mail.classList.add("open");
    await s.sleep(900);
    if (!ok()) return false;
    step(2);
    compose.classList.add("on");
    await s.say("Writing your reply");
    await s.sleep(500);
    s.flyTo(s.$(".c-body"), 300, 4);
    if (!(await s.type(s.$(".c-body")))) return false;
    await s.sleep(500);
    step(3);
    req.classList.add("on");
    s.flyTo(req, -12, -10);
    await s.say("Waiting for your OK");
    await s.sleep(1300);
    if (!ok()) return false;
    step(4);
    if (!(await s.click(s.$(".t-allow")))) return false;
    req.classList.add("gone");
    compose.classList.add("sent");
    await s.sleep(250);
    s.$(".ds-toast").classList.add("on");
    await s.say("Sent to Sam Rivera");
    return ok();
  },

  async meet(s, step, ok) {
    const req = s.$(".ds-req");
    step(0);
    if (!(await s.say("“Book a call with Priya next Tuesday at 3, and invite her.”", { heard: true }))) return false;
    await s.sleep(400);
    step(1);
    await s.say("Checking your calendar");
    const slot = s.$(".t-slot");
    s.flyTo(slot, -12, -12);
    await s.sleep(800);
    slot.classList.add("on");
    await s.say("Tuesday at 3 is free");
    await s.sleep(1300);
    if (!ok()) return false;
    step(2);
    slot.classList.remove("on");
    s.$(".t-new").classList.add("on");
    await s.say("Adding the event");
    await s.sleep(1400);
    if (!ok()) return false;
    step(3);
    req.classList.add("on");
    s.flyTo(req, -12, -10);
    await s.say("Waiting for your OK");
    await s.sleep(1300);
    if (!ok()) return false;
    step(4);
    if (!(await s.click(s.$(".t-allow")))) return false;
    req.classList.add("gone");
    await s.sleep(250);
    s.$(".ds-toast").classList.add("on");
    await s.say("Invite sent to Priya");
    return ok();
  },
};
