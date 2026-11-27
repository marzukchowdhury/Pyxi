/**
 * The storyboard (film2/storyboard.html): one still per beat of every scene, from story.js.
 * Each frame is a 960 x 540 picture (half the film's size) built from a few small pieces:
 * big type, a floating window with a made-up screen in it, the ask pill, Pyxi's card, the firefly.
 */
import "@fontsource-variable/inter";
import "@fontsource-variable/fraunces/wght.css";
import "@fontsource-variable/fraunces/wght-italic.css";
import "./storyboard.css";
import { STORY } from "./story.js";
import heartSvg from "../../partials/heart.html?raw";

const amber = (line) => line.replace(/\[(.+?)\]/g, "<em>$1</em>");
const type = (lines, small = "") => {
  // long lines get smaller type, so every line stays on one line
  const longest = Math.max(...lines.map((l) => l.replace(/[\[\]]/g, "").length));
  const size = small ? "" : ` style="font-size:${Math.min(92, Math.round(1500 / longest))}px"`;
  return `<div class="type ${small}">${lines.map((l) => `<p${size}>${amber(l)}</p>`).join("")}</div>`;
};
// Pyxi itself: the app's own firefly (wings, feelers, a dark body and the glowing tail), never a plain dot
const BUG = `<svg viewBox="-24 -24 48 48"><circle r="16" fill="url(#ffh)"/><g transform="rotate(-30)"><path class="feel" d="M-0.7 -9.2 Q-1.6 -11.6 -3.4 -12.6 M0.7 -9.2 Q1.6 -11.6 3.4 -12.6"/><g transform="rotate(-38 -2.4 -5)"><ellipse class="wing" cx="-2.4" cy="-5" rx="3.8" ry="1.7"/></g><g transform="rotate(38 2.4 -5)"><ellipse class="wing" cx="2.4" cy="-5" rx="3.8" ry="1.7"/></g><ellipse class="bod" cx="0" cy="-5" rx="2.3" ry="2.6"/><circle class="bod" cx="0" cy="-8.3" r="1.6"/><ellipse cx="0" cy="0" rx="3" ry="3.7" fill="url(#fft)"/></g></svg>`;
const fly = (x, y, on = "") => `<i class="fly ${on}" style="left:${x}px;top:${y}px">${BUG}</i>`;
const ptr = (x, y) => `<svg class="ptr" style="left:${x}px;top:${y}px" viewBox="0 0 28 34"><path d="M3 2v24.800l6.700-5.900 4.400 9.600 4.100-1.800-4.400-9.600h8.200z"/></svg>`;
const pill = (text, x, y) => `<div class="pill" style="left:${x}px;top:${y}px"><b><s></s><s></s><s></s><s></s></b>${text}</div>`;
const cap = (text) => `<p class="cap">${text}</p>`;
const win = (title, body, cls = "") => `<div class="lap"><div class="win ${cls}"><div class="bar"><u></u><span>${title}</span></div><div class="body">${body}</div></div><i class="hinge"></i><i class="base"></i></div>`;
const card = (title, rows, x, y, extra = "") =>
  `<div class="card" style="left:${x}px;top:${y}px"><h4>${title}</h4>${rows.map((r) => `<p>${r}</p>`).join("")}${extra}</div>`;
const allow = (what, x, y, file = "") =>
  `<div class="card ask" style="left:${x}px;top:${y}px"><h5><i></i>Pyxi needs your OK</h5><p>${what}</p>${file ? `<p class="file">${file}</p>` : ""}<div><b>Allow</b><span>Not now</span></div></div>`;
const lines = (n, w = [96, 88, 92, 70]) => Array.from({ length: n }, (_, i) => `<s style="width:${w[i % w.length]}%"></s>`).join("");

const notch = (text, wave = false) => `<div class="notch"><i>${BUG}</i><span>${text}</span>${wave ? "<b><s></s><s></s><s></s><s></s><s></s><s></s></b>" : ""}</div>`;

// ---- the made-up screens
const post = (marks = false) =>
  win("Halden Home on Lumen", `<div class="lumen"><aside><b>lumen</b>${lines(5, [70, 55, 80, 50, 62])}</aside>
    <article><h6><u></u>Halden Home <span>3 hours ago</span></h6><b>A NOTE ABOUT OUR K2 KETTLE.</b>
    <p>${marks ? "<mark>We are recalling the K2 kettle</mark>" : "We are recalling the K2 kettle"} after reports that its handle can crack near the hinge.</p>
    <p>This affects ${marks ? "<mark>kettles made before March</mark>" : "kettles made before March"}. The date is on the base, under the serial number.</p>
    <p>Please stop using it. ${marks ? "<mark>We will send a free replacement</mark>" : "We will send a free replacement"}, and you keep the old one until it arrives.</p>
    <p>${marks ? "<mark>Claim at halden.example/k2</mark>" : "Claim at halden.example/k2"} with your serial number. It takes two minutes.</p></article></div>`);
const upload = (size, done = false) =>
  win("Rental application", `<div class="form"><h6>Upload your lease</h6><p>PDF only. 2 MB max.</p>
    <div class="drop ${done ? "ok" : "bad"}"><i class="pdf"></i><b>Lease agreement.pdf</b><span>${size}</span>${done ? "<em>Uploaded</em>" : "<em>Too large</em>"}</div>${lines(3)}</div>`);
const doc = (kind, changed = false) =>
  `<div class="page ${kind}"><b>${kind === "pdf" ? "PDF" : "Word"}</b><h6>Offer letter</h6><p>Dear Dana,</p><p>We are pleased to offer you the role of Junior Designer.</p>
   <p>Your start date is ${changed ? "<ins>21 March</ins>" : kind === "pdf" ? "<mark>14 March</mark>" : "14 March"}.</p>${lines(4)}</div>`;
const profile = (step) =>
  win("Lumen", `<div class="lumen set"><aside><b>lumen</b>${lines(5, [70, 55, 80, 50, 62])}</aside>
    <article><h6>${step < 2 ? "Home" : step < 3 ? "Settings" : "Privacy"}</h6>
    ${step < 2 ? lines(6) : step < 3 ? "<p>Account</p><p class='hit'>Privacy</p><p>Notifications</p><p>Language</p>" : `<p class="row">Private profile <i class="sw ${step > 3 ? "on" : ""}"></i></p><p class="row">Show activity <i class="sw on"></i></p>${lines(2)}`}</article>
    <u class="avatar ${step === 1 ? "hit" : ""}"></u></div>`);
const editor = (found, done = false) =>
  win("Reelbox", `<div class="reel ${found ? "dim" : ""}"><div class="tools">${Array.from({ length: 40 }, (_, i) => `<i class="${found && i === 17 ? "hit" : ""}"></i>`).join("")}</div>
    <div class="view"><span>${done ? "And that is how you fold a paper crane." : ""}</span></div><div class="track"><s></s><s></s><s></s></div></div>`);
const ROWS = [["Pacific", "Coffee Beans", "1,850"], ["Pacific", "Espresso Machine", "2,310"], ["Pacific", "Mug", "420"], ["Prairies", "Grinder", "980"], ["Prairies", "Coffee Beans", "1,240"], ["Prairies", "Mug", "275"], ["Ontario", "Espresso Machine", "2,150"], ["Ontario", "Grinder", "765"]];
const PIVOT = [["Atlantic", "2,365"], ["Ontario", "2,915"], ["Pacific", "4,580"], ["Prairies", "2,495"], ["Quebec", "1,820"]];
const sheet = (n) =>
  win("Sales, Q3", `<div class="sheet"><table><tr><th>Region</th><th>Product</th><th>Sales</th></tr>${ROWS.map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>$${r[2]}</td></tr>`).join("")}</table>
    ${n ? `<table class="pv"><tr><th>Region</th><th>Sum of Sales</th></tr>${PIVOT.slice(0, n).map((r) => `<tr><td>${r[0]}</td><td>$${r[1]}</td></tr>`).join("")}${n >= 5 ? "<tr class='tot'><td>Total</td><td>$14,175</td></tr>" : ""}</table>` : ""}</div>`);
const desk = (tidy) =>
  `<div class="desk">${tidy ? ["Screenshots", "Documents", "Pictures", "Installers", "Videos", "Everything else"].map((f) => `<i class="folder"><b>${f}</b></i>`).join("") : Array.from({ length: 84 }, (_, i) => `<i class="f${i % 4}"></i>`).join("")}</div>`;
const mail = (open) =>
  win("Mail", `<div class="mail"><ul><li class="on"><b>Sam Rivera</b><span>Recommendation letter</span><em>Tue</em></li><li><b>Priya Shah</b><span>Thursday call</span><em>Mon</em></li><li><b>Library</b><span>Your loan is due</span><em>Sun</em></li><li><b>Dana Lee</b><span>Photos from Saturday</span><em>Sat</em></li></ul>
    ${open ? `<article><h6>Recommendation letter.docx</h6><p><del>31 November</del> <ins>30 November</ins> 2026</p><p>I am writing to recommend Dana Lee. <del>Dnaa</del> <ins>Dana</ins> worked with me for two years, ran weekly reviews, and <ins>delivered</ins> every stage on schedule.</p></article>` : `<article>${lines(7)}</article>`}</div>`);

const TAGS = [["Superior vena cava", 16, 14], ["Right atrium", 14, 45], ["Right ventricle", 14, 71], ["Pulmonary artery", 87, 25], ["Left atrium", 89, 59], ["Left ventricle", 87, 79], ["Aorta", 49, 9]];
const ROUTES = ["M206 50V216", "M206 216Q210 266 226 306", "M226 306Q232 362 262 452", "M262 452C286 392 276 290 314 232S400 210 506 215", "M546 303H426", "M426 300Q416 362 410 462", "M410 462C350 380 330 250 334 192S400 100 466 160"];
const heart = (n) =>
  win("Biology, chapter 4", `<div class="book"><div class="pic">${heartSvg}<svg class="ink" viewBox="0 0 600 660">${ROUTES.slice(0, n).map((d) => `<path d="${d}"/>`).join("")}</svg>
    ${TAGS.slice(0, n).map(([t, x, y]) => `<b class="tag" style="left:${x}%;top:${y}%">${t}</b>`).join("")}</div><article><h6>The heart</h6>${lines(9)}</article></div>`);
const chip = (img, tags = "") => `<div class="chip3d"><img src="/img/${img}" alt="" />${tags}</div>`;
const engine = (n, clear = false) =>
  `<div class="eng ${clear ? "clear" : ""}">${["head", "block", "piston", "rod", "crank", "case"].slice(0, n).map((p) => `<i class="${p}"></i>`).join("")}
   ${n >= 6 ? ["Spark plug", "Piston", "Connecting rod", "Crankshaft"].map((t, k) => `<b style="top:${[6, 34, 56, 78][k]}%;${k % 2 ? "left:66%" : "right:66%"}">${t}</b>`).join("") : ""}</div>`;

// ---- the beats of each scene: [label, picture]
export const BEATS = {
  bug: BUG,
  open: (s) => [
    ["The notch drops in", notch(s.notch[0])],
    ["Its hint, and the keys", notch(s.notch[1]) + `<div class="keys"><kbd>Ctrl</kbd><kbd>Alt</kbd></div>`],
    ["The keys go down", notch(s.notch[2], true) + `<div class="keys down"><kbd>Ctrl</kbd><kbd>Alt</kbd></div>`],
    ["Type", type(s.problem)],
    ["Type", type(s.problem2)],
    ["Payoff", fly(300, 262, "big") + type(s.payoff.type, "right")],
  ],
  post: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", post() + ptr(520, 300) + fly(548, 296) + pill(s.ask, 566, 280) + cap(`“${s.ask}”`)],
    ["Pyxi at work", post(true) + fly(628, 104, "on") + card("What this post says", ["The K2 kettle is recalled.", "Only ones made before March."], 640, 120)],
    ["Payoff", post(true) + fly(628, 104, "on") + card("What this post says", ["The K2 kettle is recalled.", "Only ones made before March.", "A free replacement is coming.", "<em>Claim at halden.example/k2</em>"], 640, 120) + cap(s.reply.split(". ")[0] + ".")],
  ],
  heart: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", heart(0) + ptr(600, 300) + fly(628, 296) + pill("Explain how the heart pumps…", 646, 280) + cap(`“${s.ask}”`)],
    ["Pyxi at work", heart(3) + fly(250, 330, "on") + cap("In from the body, out to the lungs.")],
    ["Payoff", heart(7) + fly(330, 110, "on") + type(s.payoff.type, "low")],
  ],
  engine: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", win("Notes", `<div class="blank">${lines(8)}</div>`) + ptr(480, 300) + fly(508, 296) + pill(s.ask, 526, 280) + cap(`“${s.ask}”`)],
    ["It builds, part by part", `<img class="full" src="/film2/ram-build.jpg" alt="" />`],
    ["Line up the notch", `<img class="full" src="/film2/ram-notch.jpg" alt="" />`],
    ["Payoff", `<img class="full" src="/film2/ram-done.jpg" alt="" />`],
  ],
  shrink: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", upload("14.2 MB") + ptr(560, 330) + fly(588, 326) + pill(s.ask, 606, 310) + cap(`“${s.ask}”`)],
    ["Pyxi at work", upload("6.4 MB") + fly(600, 250, "on") + `<div class="count">6.4<span>MB</span></div>`],
    ["Payoff", upload("1.8 MB", true) + fly(600, 250, "on") + cap(s.reply)],
  ],
  word: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", `<div class="pages one">${doc("pdf")}</div>` + ptr(610, 300) + fly(638, 296) + pill(s.ask, 656, 280) + cap(`“${s.ask}”`)],
    ["Pyxi at work", `<div class="pages turn">${doc("pdf")}${doc("word")}</div>` + fly(480, 110, "on")],
    ["Payoff", `<div class="pages two">${doc("pdf")}${doc("word", true)}</div>` + ptr(700, 262) + cap(s.reply)],
  ],
  setting: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", profile(0) + ptr(430, 300) + fly(458, 296) + pill(s.ask, 476, 280) + cap(`“${s.ask}”`)],
    ["Step 1 of 3", profile(1) + `<b class="step" style="left:742px;top:126px">1 of 3</b>` + ptr(800, 108) + cap(s.reply)],
    ["Step 2 of 3", profile(2) + `<b class="step" style="left:520px;top:188px">2 of 3</b>` + ptr(470, 214)],
    ["Payoff", profile(4) + `<b class="step" style="left:640px;top:150px">3 of 3</b>` + ptr(742, 172) + type(s.payoff.type, "low")],
  ],
  editor: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", editor(false) + ptr(500, 320) + fly(528, 316) + pill(s.ask, 546, 300) + cap(`“${s.ask}”`)],
    ["Pyxi at work", editor(true) + fly(330, 150, "on") + cap(s.reply)],
    ["Payoff", editor(true, true) + ptr(300, 190)],
  ],
  pivot: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", sheet(0) + ptr(560, 300) + fly(588, 296) + pill(s.ask, 440, 330) + cap(`“${s.ask}”`)],
    ["Pyxi at work", sheet(3) + fly(560, 150, "on")],
    ["Payoff", sheet(5) + fly(560, 150, "on") + cap("Pacific is your best region.")],
  ],
  desktop: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", desk(false) + ptr(470, 280) + fly(498, 276) + pill(s.ask, 516, 260) + cap(`“${s.ask}”`)],
    ["The plan, then Allow", desk(false) + card("Tidy the desktop", ["Screenshots <i>96</i>", "Documents <i>41</i>", "Pictures <i>38</i>", "Installers <i>17</i>", "Videos <i>12</i>", "Everything else <i>10</i>"], 330, 96, `<div class="btns"><b>Allow</b><span>Not now</span></div>`) + ptr(420, 386) + cap(s.reply)],
    ["Payoff", desk(true) + fly(700, 150, "on")],
  ],
  letter: (s) => [
    ["The problem", type(s.problem)],
    ["The ask", mail(false) + ptr(520, 320) + fly(548, 316) + pill("Find the email Sam sent me…", 566, 300) + cap(`“${s.ask}”`)],
    ["Found, and fixed", mail(true) + fly(400, 150, "on") + cap("I fixed the date, a name and a missing word.")],
    ["It asks first", mail(true) + allow("Send this to Sam Rivera?", 560, 250, "Recommendation letter.docx") + ptr(640, 396)],
    ["Payoff", type(s.payoff.type)],
  ],
  quick: (s) => [
    ["Cut 1", type([`“${s.asks[0]}”`], "ask") + `<div class="mini alarm"><b>7:00</b><span>Alarm, tomorrow</span></div>`],
    ["Cut 2", type([`“${s.asks[1]}”`], "ask") + `<div class="mini pdfs"><i></i><i></i><i></i><u></u><b>Photos.pdf</b></div>`],
    ["Cut 3", type([`“${s.asks[2]}”`], "ask") + `<div class="mini quiz"><span>Question 2 of 5</span><b>What turns water into vapour?</b></div>`],
    ["Cut 4", type([`“${s.asks[3]}”`], "ask") + `<div class="mini map"><i style="left:30%;top:40%"></i><i style="left:62%;top:58%"></i><b>Juniper Coffee, 4.7, open until 9</b></div>`],
    ["Payoff", type(s.payoff.type)],
  ],
  end: (s) => [
    ["A beat of silence", ""],
    ["Into the light", `<div class="flash"></div>`],
    ["The name", `<div class="name"><i>${BUG}</i><b>Pyxi</b><em>Just ask.</em></div>` + cap(s.reply.split("? ")[1])],
    ["End card", `<div class="name small"><i>${BUG}</i><b>Pyxi</b><em>Just ask.</em></div><div class="fine">${s.payoff.small.map((l) => `<p>${l}</p>`).join("")}</div>`],
  ],
};

document.body.insertAdjacentHTML("afterbegin", `<svg width="0" height="0" style="position:absolute"><defs>
  <radialGradient id="fft" cx="0.45" cy="0.4" r="0.65"><stop offset="0" stop-color="#fffbea"/><stop offset="0.55" stop-color="#ffe08a"/><stop offset="1" stop-color="#ffb02e"/></radialGradient>
  <radialGradient id="ffh"><stop offset="0" stop-color="#ffe08a" stop-opacity="0.6"/><stop offset="0.45" stop-color="#ffc861" stop-opacity="0.22"/><stop offset="1" stop-color="#ffc861" stop-opacity="0"/></radialGradient></defs></svg>`);
const board = document.getElementById("board");
if (board) STORY.forEach((s, i) => {
  const beats = BEATS[s.id](s);
  const row = document.createElement("section");
  row.innerHTML = `<h2><b>${i}</b>${s.ask ? `“${s.ask}”` : s.id === "open" ? "Opening" : s.id === "quick" ? "Quick cuts" : "The name"}${s.reply ? `<span>Pyxi says: ${s.reply}</span>` : ""}</h2>
    <div class="beats">${beats.map(([label, pic], k) => `<figure><div class="frame"><div class="stage"><u class="g1"></u><u class="g2"></u>${pic}</div></div><figcaption><b>${k + 1}</b>${label}</figcaption></figure>`).join("")}</div>`;
  board.append(row);
});
if (new URLSearchParams(location.search).has("laptop")) document.body.classList.add("laptop");
document.fonts.ready.then(() => {
  // an ask pill that would run off the frame sits to the left of the firefly instead
  for (const el of document.querySelectorAll(".pill")) {
    const over = el.offsetLeft + el.offsetWidth - 930;
    if (over > 0) el.style.left = `${el.offsetLeft - over}px`;
  }
  document.body.dataset.ready = "1";
});
