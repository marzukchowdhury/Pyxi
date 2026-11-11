import "@fontsource-variable/inter";
import "./styles.css";
import "./pages.css";
import "./demos.css";
import "./motion.css";
import "./sections.css";
import "./doors.css";
import "./story.css";
import "@fontsource-variable/fraunces/wght.css";
import { initDemos } from "./demos.js";
import { initMotion } from "./motion.js";
import { initCatalog } from "./catalog.js";
import { Firefly } from "./firefly.js";
import { initHero3D } from "./hero3d.js";
import { initSwarm } from "./swarm.js";
import { initStory } from "./story.js";
import { initBuild3D } from "./build3d.js";
import "./build3d.css";
import { initOnPicture } from "./onpicture.js";
import "./onpicture.css";

document.documentElement.classList.add("js");
// Reduced motion keeps Pyxi calm: no typing, trail or sway, but it still answers and points.
// ?motion=1 shows the full motion anyway (handy for demos on a PC with animations turned off).
// A visitor whose PC has animations off can also choose to play them here (the pill below); that
// choice is remembered on this device.
let chose = false;
try {
  chose = localStorage.getItem("pyxi-motion") === "1";
} catch {
  /* storage blocked: the choice just won't be remembered */
}
const prefersReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const forceMotion = new URLSearchParams(location.search).has("motion") || chose;
if (forceMotion) document.documentElement.classList.add("force-motion");
const reduced = !forceMotion && prefersReduced;
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

// ------------------------------------------------------------------ the notch
const notch = $("#notch");
const sayText = $(".say-text", notch);
let sayTimer = 0;
let lastSaid = "";
/** Stream a line into the notch word by word, the way Pyxi's real notch shows its words. */
function say(text) {
  if (!notch || !text || text === lastSaid) return;
  lastSaid = text;
  clearTimeout(sayTimer);
  sayText.textContent = "";
  text.split(" ").forEach((word, i) => {
    const w = document.createElement("span");
    w.className = "w";
    w.textContent = word;
    w.style.animationDelay = reduced ? "0s" : `${i * 55}ms`;
    sayText.append(w, " ");
  });
  notch.classList.add("saying");
}
const menu = $(".notch-menu", notch);
menu?.addEventListener("click", () => {
  const open = notch.classList.toggle("open");
  menu.setAttribute("aria-expanded", String(open));
});
$$(".notch-links a", notch).forEach((a) =>
  a.addEventListener("click", () => {
    notch.classList.remove("open");
    menu?.setAttribute("aria-expanded", "false");
  }),
);
const here = location.pathname.replace(/\.html$/, "").replace(/\/$/, "") || "/";
$$(".notch-links a").forEach((a) => {
  if (a.getAttribute("href") === here) a.setAttribute("aria-current", "page");
});

// ------------------------------------------------------------------ reveal on scroll
const revealer = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("in");
      revealer.unobserve(e.target);
    }
  },
  { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
);
// Siblings come in one after another, a beat apart.
$$(".reveal").forEach((el) => {
  const sibs = [...el.parentElement.children].filter((c) =>
    c.classList.contains("reveal"),
  );
  el.style.setProperty("--d", `${Math.min(sibs.indexOf(el), 6) * 70}ms`);
  revealer.observe(el);
});

// ------------------------------------------------------------------ the firefly
const fly = new Firefly({ reduced });
window.pyxi = { fly, say }; // handy from the console

// What you're looking at: a section that crosses the middle of the viewport gets a line in the
// notch, and a feature gets the firefly's thread on the exact control its screenshot shows.
const pointed = new WeakSet();
const watcher = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      const el = e.target;
      if (!e.isIntersecting) {
        pointed.delete(el);
        $(".shot", el)?.classList.remove("lit");
        continue;
      }
      if (el.dataset.say && !el.classList.contains("hero")) say(el.dataset.say);
      // Several targets in one place get pointed at in turn: More info, then Run anyway.
      const pts = el.matches(".feature, .step") ? $$(".pt", el) : [];
      if (pts.length && !pointed.has(el)) {
        pointed.add(el);
        pts.forEach((pt, i) =>
          setTimeout(
            () => {
              if (!pointed.has(el)) return;
              fly.point(pt, pt.dataset.label);
              pt.closest(".shot")?.classList.add("lit");
            },
            450 + i * 3000,
          ),
        );
      }
    }
  },
  { rootMargin: "-42% 0px -42% 0px" },
);
$$("[data-say]:not(.slide)").forEach((el) => watcher.observe(el));
// ------------------------------------------------------------------ hero: ask, then show
const stage = $("#stage");
if (stage) {
  const slides = $$(".slide", stage);
  const dots = $$(".stage-dots button");
  const askText = $(".ask-text");
  const hero = $(".hero");
  let heroVisible = true;
  let held = false; // hovering or focusing the stage pauses it
  let run = 0; // bumping this cancels the loop that's running
  const figure = stage.closest(".stage");
  figure.addEventListener("pointerenter", () => (held = true));
  figure.addEventListener("pointerleave", () => (held = false));
  figure.addEventListener("focusin", () => (held = true));
  figure.addEventListener("focusout", () => (held = false));

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  /** Erase the old request and type the new one. False if the loop was cancelled meanwhile. */
  async function type(text, my) {
    while (askText.textContent.length) {
      if (my !== run) return false;
      askText.textContent = askText.textContent.slice(0, -1);
      await sleep(16);
    }
    for (let i = 1; i <= text.length; i++) {
      if (my !== run) return false;
      askText.textContent = text.slice(0, i);
      await sleep(30 + Math.random() * 30);
    }
    return my === run;
  }
  /** One loop at a time: type the request, show its screenshot, point at it, wait, next. */
  async function play(start, typed) {
    const my = ++run;
    for (let i = start; ; i++) {
      const index = i % slides.length;
      const slide = slides[index];
      dots.forEach((d, k) =>
        d.setAttribute("aria-pressed", String(k === index)),
      );
      if (typed && !reduced) {
        if (!(await type(slide.dataset.ask, my))) return;
        await sleep(260);
      } else askText.textContent = slide.dataset.ask;
      if (my !== run) return;
      slides.forEach((s) => s.classList.toggle("is-on", s === slide));
      figure.dataset.on = String(index);
      const img = slide.querySelector("img");
      figure.style.setProperty("--img", `url("${img.currentSrc || img.src}")`);
      await sleep(450);
      if (my !== run) return;
      if (heroVisible) {
        say(slide.dataset.say);
        const pt = $(".pt", slide);
        fly.point(pt, pt.dataset.label, { hold: 3600 });
      }
      await sleep(5600);
      // Hold still while the hero is scrolled away, the tab is hidden, or you're looking closely.
      while (!heroVisible || document.hidden || held) {
        await sleep(400);
        if (my !== run) return;
      }
      if (my !== run) return;
      typed = true;
    }
  }
  dots.forEach((d, k) => d.addEventListener("click", () => play(k, false)));
  new IntersectionObserver(([e]) => (heroVisible = e.isIntersecting), {
    threshold: 0.35,
  }).observe(hero);
  // The first slide is already up; say hello, then start pointing.
  say(hero.dataset.say);
  const first = slides[0]?.querySelector("img");
  if (first)
    figure.style.setProperty(
      "--img",
      `url("${first.currentSrc || first.src}")`,
    );
  setTimeout(() => play(0, false), 900);
}

// ------------------------------------------------------------------ hero: tilt and dust
initHero3D({ reduced });

// ------------------------------------------------------------------ chores: windows tile
const windows = $(".art-windows");
if (windows && !reduced) {
  let n = 2;
  let on = false;
  new IntersectionObserver(([e]) => (on = e.isIntersecting)).observe(windows);
  setInterval(() => {
    if (!on || document.hidden) return;
    n = n === 4 ? 2 : n + 1;
    windows.classList.toggle("n3", n === 3);
    windows.classList.toggle("n4", n === 4);
  }, 2400);
}

// ------------------------------------------------------------------ watch it work
initDemos({ reduced });

// ------------------------------------------------------------------ the latest version
// Downloads always go to GitHub's latest release (a link that never changes), so the version
// and size shown are read from it too. The values baked in at build time stay if it can't answer.
fetch("https://api.github.com/repos/marzukchowdhury/Pyxi/releases/latest")
  .then((r) => (r.ok ? r.json() : null))
  .then((rel) => {
    const asset = rel?.assets?.find((a) => a.name === "Pyxi-Setup.exe");
    if (!rel || !asset) return;
    const version = String(rel.tag_name || "").replace(/^v/, "");
    const size = `${Math.round(asset.size / 2 ** 20)} MB`;
    document
      .querySelectorAll("[data-version]")
      .forEach((el) => (el.textContent = version));
    document
      .querySelectorAll("[data-size]")
      .forEach((el) => (el.textContent = size));
  })
  .catch(() => {});

// ------------------------------------------------------------------ clips and the 3D layers
initMotion({ reduced });

// ------------------------------------------------------------------ ask anything, the team, a day
initCatalog({ reduced });

// ------------------------------------------------------------------ the swarm and its story
const swarm = initSwarm({ reduced });
window.pyxi.swarm = swarm;
initStory({ reduced, swarm });

// ------------------------------------------------------------------ explained in 3D, live
initBuild3D({ reduced });

// ------------------------------------------------------------------ explains any picture, on the picture
initOnPicture({ reduced });

// ------------------------------------------------------------------ animations off on this PC?
// Then say so, and offer to play them anyway. One click, remembered, and one click to undo.
if (prefersReduced) {
  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = "motion-pill";
  pill.innerHTML = reduced
    ? "<i></i>Animations are off on this PC <b>Play them</b>"
    : "<i></i>Animations on <b>Turn off</b>";
  pill.addEventListener("click", () => {
    try {
      if (reduced) localStorage.setItem("pyxi-motion", "1");
      else localStorage.removeItem("pyxi-motion");
    } catch {
      /* nothing to remember with */
    }
    const url = new URL(location.href);
    url.searchParams.delete("motion");
    if (reduced && !chose) url.searchParams.set("motion", "1"); // works even if storage is blocked
    location.replace(url);
  });
  document.body.append(pill);
}
