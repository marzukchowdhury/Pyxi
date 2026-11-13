/**
 * The film: every scene of the Pyxi video on one clock, at 1920 x 1080 (film/index.html).
 *
 * LINES is the whole script, in order, in Pyxi's own words. tools/film.py reads it from here, has
 * Pyxi's voice say each line, and hands back how long each one took; start(durations) then lays
 * the scenes out round the lines and plays them once, in real time, while the frames are recorded.
 * Nothing here is a mock of the product that the site does not already show: the recordings are
 * real, the 3D stage and the picture replay are the site's own, and every claim is from its copy.
 */
import "@fontsource-variable/inter";
import "@fontsource-variable/fraunces/wght.css";
import "../styles.css";
import "../story.css";
import "../build3d.css";
import "../onpicture.css";
import "./film.css";
import { initSwarm } from "../swarm.js";
import { initOnPicture } from "../onpicture.js";

export const LINES = [
  { id: "hi", scene: "intro", text: "Hi. I'm Pyxi. A little firefly that lives beside your cursor." },
  {
    id: "ask",
    scene: "point",
    text: "Hold Control and Alt, and ask me about anything on your screen.",
    cap: "Hold Ctrl Alt, and ask me about anything on your screen.",
  },
  { id: "point", scene: "point", text: "I answer out loud, and point right at it." },
  {
    id: "notes",
    scene: "notes",
    text: "Ask me to explain what you're reading, and I write notes beside the exact words.",
  },
  { id: "pic1", scene: "picture", text: "Show me a picture, and I explain it on the picture." },
  {
    id: "pic2",
    scene: "picture",
    text: "Every part gets its name, and I trace how things move from one to the next.",
  },
  { id: "b1", scene: "build", text: "Ask how to do something, and I build it in 3D, right on your screen.", hold: 3.2 },
  { id: "b2", scene: "build", text: "Open the two clips, line up the notch, and press down until both clips click." },
  { id: "a1", scene: "act", text: "I can do the clicking too. I'll write the reply and get it ready." },
  {
    id: "a2",
    scene: "act",
    text: "But anything that reaches another person, or costs money, waits until you press Allow.",
  },
  { id: "t1", scene: "team", text: "And I'm not alone. Twelve teammates do the legwork." },
  { id: "t2", scene: "team", text: "Scout researches. Quill writes. Ledger handles your spreadsheets." },
  { id: "t3", scene: "team", text: "They work in the background, while you keep working." },
  { id: "c1", scene: "control", text: "I look when you ask.", hold: 0.6 },
  { id: "c2", scene: "control", text: "Passwords are blacked out before anything is sent.", hold: 0.5 },
  {
    id: "c3",
    scene: "control",
    text: "Move the mouse or press Escape, and you're back in control.",
    cap: "Move the mouse or press Esc, and you're back in control.",
  },
  { id: "bye", scene: "outro", text: "I'm Pyxi. Free in beta, for Windows. Come say hey." },
];

/** Each scene: quiet before its first line, after its last, and the label in the corner. */
const SCENES = [
  { name: "intro", pre: 1.5, post: 0.9, chapter: "" },
  { name: "point", pre: 0.5, post: 0.6, chapter: "Points at it" },
  { name: "notes", pre: 0.5, post: 0.8, chapter: "Writes notes for you" },
  { name: "picture", pre: 0.5, post: 1.4, chapter: "Explains any picture" },
  { name: "build", pre: 0.5, post: 2.2, chapter: "Shows you in 3D" },
  { name: "act", pre: 0.5, post: 1.4, chapter: "Does the clicking" },
  { name: "team", pre: 0.6, post: 0.8, chapter: "A team of twelve" },
  { name: "control", pre: 0.5, post: 0.9, chapter: "You stay in control" },
  { name: "outro", pre: 1.1, post: 2.8, chapter: "" },
];
const GAP = 0.22; // between two lines of one scene
const TEAM = [
  ["Scout", "Research and browsing"],
  ["Quill", "Writing"],
  ["Sketch", "3D"],
  ["Pixel", "Files"],
  ["Ledger", "Spreadsheets and documents"],
  ["Atlas", "Travel"],
  ["Penny", "Money"],
  ["Scribe", "Meetings"],
  ["Sage", "Study"],
  ["Patch", "Code"],
  ["Ditto", "Repetitive tasks"],
  ["Trace", "Memory"],
];
const REPLY =
  "Hi Sam, thank you so much! The dates are right. I'll send you the signed copy on Friday.";
const $ = (s) => document.querySelector(s);
const BUG = `<svg viewBox="-24 -24 48 48"><defs>
  <radialGradient id="fft" cx="0.45" cy="0.4" r="0.65"><stop offset="0" stop-color="#fffbea"/><stop offset="0.55" stop-color="#ffe08a"/><stop offset="1" stop-color="#ffb02e"/></radialGradient>
  <radialGradient id="ffh"><stop offset="0" stop-color="#ffe08a" stop-opacity="0.6"/><stop offset="0.45" stop-color="#ffc861" stop-opacity="0.22"/><stop offset="1" stop-color="#ffc861" stop-opacity="0"/></radialGradient></defs>
  <circle r="16" fill="url(#ffh)"/><g transform="rotate(-30)">
  <path class="feel" d="M-0.7 -9.2 Q-1.6 -11.6 -3.4 -12.6 M0.7 -9.2 Q1.6 -11.6 3.4 -12.6"/>
  <g transform="rotate(-38 -2.4 -5)"><ellipse class="wing" cx="-2.4" cy="-5" rx="3.8" ry="1.7"/></g>
  <g transform="rotate(38 2.4 -5)"><ellipse class="wing" cx="2.4" cy="-5" rx="3.8" ry="1.7"/></g>
  <ellipse class="bod" cx="0" cy="-5" rx="2.3" ry="2.6"/><circle class="bod" cx="0" cy="-8.3" r="1.6"/>
  <ellipse cx="0" cy="0" rx="3" ry="3.7" fill="url(#fft)"/></g></svg>`;
for (const el of document.querySelectorAll(".f-brand i, .f-team-me i, .f-req-title i, .f-end h1 i")) {
  el.classList.add("bug");
  el.innerHTML = BUG.replace('viewBox="-24 -24 48 48"', 'viewBox="-11 -16 22 24"'); // in close on the firefly itself; its glow spills past the box
}
const SVG = "http://www.w3.org/2000/svg";

// ------------------------------------------------------------------ the pieces the scenes use
const swarm = initSwarm({ reduced: false });
const cueEl = $("#cue");
const box = $("#swarm-box");
function cue(name, { opacity = 1, place = "mid" } = {}) {
  const moved = box.dataset.place !== place;
  box.dataset.place = place;
  cueEl.dataset.swarm = name;
  cueEl.dataset.swarmOpacity = String(opacity);
  swarm?.refresh();
  if (moved) swarm?.set(name, { force: true });
}

const picture = initOnPicture({ pace: 0.26, auto: false });

// the team: twelve names round Pyxi, each on its own thread
const team = TEAM.map(([name, role], i) => {
  const a = -Math.PI / 2 + (i / TEAM.length) * Math.PI * 2 + 0.26;
  const x = 960 + Math.cos(a) * 690;
  const y = 468 + Math.sin(a) * 318;
  const el = document.createElement("div");
  el.className = "f-mate";
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  el.innerHTML = `<b>${name}</b><span>${role}</span>`;
  $("#team").append(el);
  const line = document.createElementNS(SVG, "path");
  line.setAttribute("d", `M 960 468 L ${x.toFixed(0)} ${y.toFixed(0)}`);
  line.setAttribute("pathLength", "1");
  $("#team-lines").append(line);
  return { name, el, line };
});

// ------------------------------------------------------------------ the clock
let t0 = 0;
let RAM_STEPS = [0, 1.3, 2.3]; // when each step is said within Pyxi's line, from the voice itself
const at = (sec) =>
  new Promise((done) => setTimeout(done, Math.max(0, t0 + sec * 1000 - performance.now())));

function schedule(durations) {
  let t = 0;
  const lines = {};
  const scenes = SCENES.map((sc) => {
    const start = t;
    t += sc.pre;
    const mine = LINES.filter((l) => l.scene === sc.name);
    mine.forEach((l, i) => {
      const dur = durations[l.id];
      lines[l.id] = { start: t, dur, end: t + dur };
      t += dur + (l.hold ?? 0) + (i < mine.length - 1 ? GAP : 0); // hold: time to watch what was just said
    });
    t += sc.post;
    return { ...sc, start, end: t };
  });
  return { scenes, lines, total: t };
}

// ------------------------------------------------------------------ the scenes
const RUN = {
  async intro(sc, L) {
    cue("firefly", { place: "mid" });
    await at(L.hi.start + 1.7);
    cue("text:Pyxi", { place: "mid" });
    await at(L.hi.start + 2.6);
    $("#sc-intro").classList.add("say");
  },
  async point() {
    cue("field", { opacity: 0.55 });
    const v = $("#sc-point video");
    v.currentTime = 0.4;
    v.playbackRate = 1.3;
    v.play();
  },
  async notes() {
    const v = $("#sc-notes video");
    v.currentTime = 1.6;
    v.playbackRate = 1.3;
    v.play();
  },
  async picture(sc) {
    await at(sc.start + 1.0);
    picture.play(0);
  },
  async build(sc, L) {
    // the RAM scene draws itself from a time: its own second 7.7 is where Pyxi starts the steps
    const w = $("#ram").contentWindow;
    const zero = L.b2.start - 7.7;
    const [o1, o2, o3] = RAM_STEPS;
    w.setup({ s1: 7.7 + o1, s2: 7.7 + o2, s3: 7.7 + o3, c: 999, total: 999, env: [], phrases: [] });
    const tick = () => {
      const t = (performance.now() - t0) / 1000 - zero;
      if (t > sc.end - zero + 0.6) return;
      w.draw(Math.max(0, t));
      requestAnimationFrame(tick);
    };
    tick();
  },
  async act(sc, L) {
    const text = $("#mail-text");
    const from = L.a1.start + 0.5;
    const span = Math.max(1.5, L.a1.dur - 0.9);
    for (let i = 1; i <= REPLY.length; i++) {
      text.textContent = REPLY.slice(0, i);
      await at(from + (i / REPLY.length) * span);
    }
    text.classList.add("done");
    await at(L.a2.start + 0.2);
    $("#req").classList.add("on");
    await at(L.a2.start + 1.2);
    const cur = $("#cursor");
    const b = $("#req-allow").getBoundingClientRect();
    cur.classList.add("on");
    cur.style.transform = `translate(${(b.left + b.width * 0.62).toFixed(0)}px, ${(b.top + b.height * 0.55).toFixed(0)}px)`;
    await at(L.a2.end + 0.25);
    $("#req-allow").classList.add("press");
    await at(L.a2.end + 0.55);
    $("#req").classList.remove("on");
    cur.classList.remove("on");
    $("#toast").classList.add("on");
    $("#sc-act").classList.add("sent");
  },
  async team(sc, L) {
    $("#sc-team").classList.add("go");
    team.forEach((m, i) => {
      const d = 0.5 + i * 0.16;
      m.el.style.transitionDelay = m.line.style.transitionDelay = `${d}s`;
    });
    const hi = async (name, t) => {
      await at(t);
      team.forEach((m) => {
        m.el.classList.toggle("hi", m.name === name);
        m.line.classList.toggle("hi", m.name === name);
      });
    };
    await hi("Scout", L.t2.start + 0.05);
    await hi("Quill", L.t2.start + L.t2.dur * 0.34);
    await hi("Ledger", L.t2.start + L.t2.dur * 0.56);
    await hi("", L.t3.start);
    $("#sc-team").classList.add("busy");
  },
  async control(sc, L) {
    const rules = [...document.querySelectorAll("#rules li")];
    for (const [i, id] of ["c1", "c2", "c3"].entries()) {
      await at(L[id].start - 0.1);
      rules.forEach((li, k) => li.classList.toggle("now", k === i));
      rules[i].classList.add("on");
      $("#sc-control").dataset.rule = String(i + 1);
    }
  },
  async outro(sc) {
    cue("firefly", { place: "top" });
    await at(sc.start + 1.2);
    $("#sc-outro").classList.add("say");
    await at(sc.end - 1.0);
    document.body.classList.add("f-out");
  },
};

async function start(durations) {
  if (durations._ram) RAM_STEPS = durations._ram;
  const plan = schedule(durations);
  const cap = $("#cap");
  const chapter = $("#chapter");
  t0 = performance.now();
  const wall0 = Date.now();
  // captions: each line is on screen for as long as it is said
  for (const l of LINES) {
    const { start: s, end: e } = plan.lines[l.id];
    at(s).then(() => {
      cap.textContent = l.cap ?? l.text;
      cap.classList.add("on");
      cap.dataset.line = l.id;
    });
    at(e + 0.25).then(() => cap.dataset.line === l.id && cap.classList.remove("on"));
  }
  for (const sc of plan.scenes) {
    at(sc.start).then(() => {
      document.querySelectorAll(".sc").forEach((el) => el.classList.toggle("on", el.id === `sc-${sc.name}`));
      chapter.textContent = sc.chapter;
      chapter.classList.toggle("on", !!sc.chapter);
      document.body.dataset.scene = sc.name;
      if (!["intro", "outro"].includes(sc.name)) cue("field", { opacity: 0.5 });
      RUN[sc.name](sc, plan.lines);
    });
  }
  await at(plan.total);
  return { wall0, total: plan.total, lines: plan.lines, scenes: plan.scenes };
}

const ready = (async () => {
  await document.fonts.ready;
  await Promise.all(
    [...document.querySelectorAll("video")].map(
      (v) =>
        new Promise((done) => {
          if (v.readyState >= 3) done();
          else v.addEventListener("canplaythrough", done, { once: true });
          v.load();
        }),
    ),
  );
  // the RAM scene's page: loaded, its fonts in, its own captions off (this film has its own), and one frame drawn to warm it
  const ram = $("#ram");
  await new Promise((done) => {
    const check = () => (ram.contentDocument?.body?.dataset.ready ? done() : setTimeout(check, 100));
    check();
  });
  ram.contentDocument.head.insertAdjacentHTML("beforeend", "<style>#cap, #band { display: none !important; }</style>");
  ram.contentWindow.draw(8);
  await new Promise((done) => setTimeout(done, 800));
  ram.contentWindow.draw(0);
  return true;
})();

window.film = { lines: LINES, ready, start, schedule };
if (new URLSearchParams(location.search).has("play"))
  ready.then(() =>
    start(Object.fromEntries(LINES.map((l) => [l.id, l.text.split(/\s+/).length * 0.36 + 0.5]))),
  );
