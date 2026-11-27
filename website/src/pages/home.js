// The home page: the film in the hero, the demo clips, and the question typed in the headline.
//
// A clip costs nothing until it is needed: every <video data-loop> has preload="none" and a
// poster, starts only when it scrolls into view, and stops when it leaves. With "reduce motion"
// on, nothing plays by itself: the poster stays, with a play button on it.
import "./home.css";

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches && !document.documentElement.classList.contains("force-motion");

// ------------------------------------------------------------------ clips that loop while on screen
const loops = [...document.querySelectorAll("video[data-loop]")];
const state = new WeakMap(); // video -> { seen, stopped }: on screen, and paused by hand

function play(video) {
  video.play().catch(() => {}); // a browser that refuses autoplay keeps the poster and the button
}

for (const video of loops) {
  const button = video.parentElement.querySelector(".clip-toggle");
  const s = { seen: false, stopped: reduced };
  state.set(video, s);
  if (!button) continue;
  const word = video.classList.contains("film-loop") ? "the preview" : "this demo";
  const label = () => {
    const paused = video.paused;
    button.setAttribute("aria-pressed", String(paused));
    button.querySelector(".sr-only").textContent = `${paused ? "Play" : "Pause"} ${word}`;
  };
  button.hidden = false;
  button.addEventListener("click", () => {
    s.stopped = !video.paused;
    if (video.paused) play(video);
    else video.pause();
  });
  video.addEventListener("play", label);
  video.addEventListener("pause", label);
  label();
}

if ("IntersectionObserver" in window) {
  const watch = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const video = e.target;
        const s = state.get(video);
        s.seen = e.isIntersecting;
        if (video.hidden) continue;
        if (s.seen && !s.stopped && !document.hidden) play(video);
        else if (!s.seen) video.pause();
      }
    },
    { threshold: 0.35 },
  );
  loops.forEach((v) => watch.observe(v));
  document.addEventListener("visibilitychange", () => {
    for (const v of loops) {
      const s = state.get(v);
      if (document.hidden) v.pause();
      else if (s.seen && !s.stopped && !v.hidden) play(v);
    }
  });
}

// ------------------------------------------------------------------ the film, with sound
const film = document.getElementById("film");
if (film) {
  const loop = film.querySelector(".film-loop");
  const full = film.querySelector(".film-full");
  const start = film.querySelector(".film-play");
  const back = () => {
    // the film is over (or was closed): the silent preview comes back
    full.pause();
    full.hidden = true;
    loop.hidden = false;
    film.classList.remove("playing");
    const s = state.get(loop);
    if (s.seen && !s.stopped) play(loop);
  };
  start.addEventListener("click", () => {
    loop.pause();
    loop.hidden = true;
    full.hidden = false;
    film.classList.add("playing");
    full.currentTime = 0;
    full.muted = false;
    full.play().catch(() => {}); // if it will not start, its own controls are there
    full.focus({ preventScroll: true });
  });
  full.addEventListener("ended", () => {
    back();
    start.focus({ preventScroll: true });
  });
  full.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      back();
      start.focus({ preventScroll: true });
    }
  });
}

// ------------------------------------------------------------------ the headline asks, typed one after another
const ask = document.querySelector(".ask-text[data-asks]");
if (ask && !reduced) {
  const asks = ask.dataset.asks.split("|");
  const hero = ask.closest(".hero");
  let visible = true;
  new IntersectionObserver(([e]) => (visible = e.isIntersecting), { threshold: 0.3 }).observe(hero);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  (async () => {
    for (let i = 1; ; i++) {
      await sleep(4200);
      while (!visible || document.hidden) await sleep(400);
      const text = asks[i % asks.length];
      while (ask.textContent.length) {
        ask.textContent = ask.textContent.slice(0, -1);
        await sleep(16);
      }
      for (let k = 1; k <= text.length; k++) {
        ask.textContent = text.slice(0, k);
        await sleep(30 + Math.random() * 30);
      }
    }
  })();
}
