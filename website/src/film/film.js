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
import { createStage } from "../build3d/stage.js";
import engine from "../build3d/models/engine.json";
import seat from "../build3d/models/ejection_seat.json";

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
  {
    id: "b1",
    scene: "build",
    text: "Ask how something works, and I build it in 3D, part by part, on top of whatever you have open.",
  },
  { id: "b2", scene: "build", text: "Turn it around.", hold: 1.5 },
  { id: "b3", scene: "build", text: "Look inside.", hold: 1.5 },
  { id: "b4", scene: "build", text: "Ask for a change, and the same model changes.", hold: 1.2 },
  { id: "b5", scene: "build", text: "Then I tell the story, step by step, like a short video." },
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
  { name: "intro", pre: 2.4, post: 1.5, chapter: "" },
  { name: "point", pre: 0.8, post: 0.9, chapter: "Points at it" },
  { name: "notes", pre: 0.8, post: 1.3, chapter: "Writes notes for you" },
  { name: "picture", pre: 0.8, post: 2.2, chapter: "Explains any picture" },
  { name: "build", pre: 0.9, post: 7.2, chapter: "Builds it in 3D" },
  { name: "act", pre: 0.8, post: 2.0, chapter: "Does the clicking" },
  { name: "team", pre: 0.9, post: 1.2, chapter: "A team of twelve" },
  { name: "control", pre: 0.8, post: 1.3, chapter: "You stay in control" },
  { name: "outro", pre: 1.6, post: 3.4, chapter: "" },
];
const GAP = 0.32; // between two lines of one scene
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

const picture = initOnPicture({ pace: 0.36, auto: false });

const stage = createStage($("#b3-view"), {
  onBuild: (shown, total) => {
    $("#b3-count").textContent =
      shown < total ? `Building it: ${shown} of ${total} parts` : `${total} parts`;
    $("#b3-bar").classList.toggle("on", shown >= total);
  },
  onBeat: (i, beat) => ($("#b3-step").textContent = beat.title),
});
stage.setInsets(70, 84);

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
    v.play();
  },
  async notes() {
    const v = $("#sc-notes video");
    v.currentTime = 1.6;
    v.play();
  },
  async picture(sc) {
    await at(sc.start + 1.0);
    picture.play(0);
  },
  async build(sc, L) {
    stage.setActive(true);
    await at(sc.start + 0.25);
    $("#b3-ask").textContent = "How does a car engine work?";
    stage.load(engine);
    await at(L.b2.start - 0.1);
    stage.turn(-2.3, L.b3.start - L.b2.start - 0.2);
    await at(L.b3.start);
    stage.setBeat(1); // from the front, with the casing turned to glass
    await at(L.b4.start + 0.5);
    stage.setStyle("blueprint");
    await at(L.b5.start - 0.35);
    $("#sc-build").classList.add("swap");
    await at(L.b5.start - 0.05);
    stage.setStyle("studio");
    $("#b3-ask").textContent = "How does a pilot eject from a jet?";
    stage.load(seat, { assemble: false });
    $("#sc-build").classList.remove("swap");
    await at(L.b5.start + 1.3);
    stage.setBeat(1); // the canopy goes
    await at(L.b5.start + 3.6);
    stage.setBeat(2); // the seat fires
    await at(L.b5.start + 6.3);
    stage.setBeat(3);
    await at(L.b5.start + 7.8);
    stage.setBeat(4); // the parachute opens
    await at(sc.end + 0.6);
    stage.setActive(false);
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
  // warm the 3D stage (shaders, the studio light) so its first frame in the film is not a stall
  stage.setActive(true);
  stage.load(engine, { assemble: false });
  await new Promise((done) => setTimeout(done, 1200));
  stage.setActive(false);
  return true;
})();

window.film = { lines: LINES, ready, start, schedule };
if (new URLSearchParams(location.search).has("play"))
  ready.then(() =>
    start(Object.fromEntries(LINES.map((l) => [l.id, l.text.split(/\s+/).length * 0.36 + 0.5]))),
  );
