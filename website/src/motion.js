/**
 * Motion on the page:
 *  - clips: muted loops recorded from Pyxi's real overlay (scripts/record_clips.py). Each video
 *    sits over its still and only loads and plays while it's on screen. data-start skips the
 *    recording's first moments, so a loop starts on the scene, not on a blank page.
 *  - layers: "how it works" lifts the screen, what Pyxi reads and what Pyxi draws apart in 3D as
 *    you scroll through the section, one layer at a time, then settles them back into one.
 */
export function initMotion({ reduced = false } = {}) {
  initClips(reduced);
  initLayers(reduced);
}

function initClips(reduced) {
  const clips = [...document.querySelectorAll("video[data-clip]")];
  if (!clips.length || reduced) return; // reduced motion: the stills stay
  const start = (v) => Number(v.dataset.start || 0);
  for (const v of clips) {
    v.addEventListener("timeupdate", () => {
      if (v.currentTime < start(v) - 0.05) v.currentTime = start(v);
    });
    v.addEventListener("playing", () => v.classList.add("playing"));
  }
  const watch = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const v = e.target;
        if (e.isIntersecting) {
          if (!v.src) {
            v.src = `/clips/${v.dataset.clip}.webm`;
            v.addEventListener("loadedmetadata", () => (v.currentTime = start(v)), { once: true });
          }
          void v.play().catch(() => undefined);
        } else v.pause();
      }
    },
    { threshold: 0.35 },
  );
  clips.forEach((v) => watch.observe(v));
  // In the hero, a clip starts from its beginning each time its slide comes round.
  const stage = document.getElementById("stage");
  if (stage)
    new MutationObserver((records) => {
      for (const r of records) {
        const slide = r.target;
        // Only the moment a slide becomes the visible one (not every class change inside it).
        if (!slide.classList?.contains("slide") || !slide.classList.contains("is-on") || (r.oldValue || "").includes("is-on")) continue;
        const v = slide.querySelector("video[data-clip]");
        if (v && v.readyState > 0) v.currentTime = start(v);
      }
    }).observe(stage, { subtree: true, attributes: true, attributeFilter: ["class"], attributeOldValue: true });
}

function initLayers(reduced) {
  const track = document.querySelector(".layers-track");
  if (!track) return;
  const steps = [...track.querySelectorAll(".layer-steps li")];
  const setStep = (n) => {
    if (track.dataset.step === String(n)) return;
    track.dataset.step = String(n);
    steps.forEach((li, i) => li.classList.toggle("on", i + 1 === n));
  };
  if (reduced) {
    setStep(0);
    steps.forEach((li) => li.classList.add("on"));
    return;
  }
  const smooth = (t) => t * t * (3 - 2 * t);
  let raf = 0;
  const update = () => {
    raf = 0;
    const r = track.getBoundingClientRect();
    const span = r.height - innerHeight;
    const p = Math.min(1, Math.max(0, -r.top / span));
    // Lift apart over the first fifth, hold while each layer has its turn, settle at the end.
    const open = p < 0.18 ? smooth(p / 0.18) : p > 0.86 ? smooth((1 - p) / 0.14) : 1;
    track.style.setProperty("--open", open.toFixed(3));
    setStep(p < 0.18 || p > 0.86 ? 0 : p < 0.41 ? 1 : p < 0.64 ? 2 : 3);
    if (p >= 0.86) steps.forEach((li) => li.classList.add("on"));
  };
  const queue = () => (raf ||= requestAnimationFrame(update));
  addEventListener("scroll", queue, { passive: true });
  addEventListener("resize", queue);
  update();
}
