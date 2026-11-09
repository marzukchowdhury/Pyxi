/**
 * Ask anything: a live filter over the sixty real asks (by word and by kind), with the box typing
 * suggestions by itself until you type. The team: the page's firefly flies to whichever teammate
 * you hover. A day with Pyxi: the track scrolls sideways with the wheel, like a timeline.
 */
export function initCatalog({ reduced = false } = {}) {
  const input = document.getElementById("pal-input");
  const grid = document.getElementById("asks-grid");
  if (input && grid) {
    const items = [...grid.querySelectorAll("li")];
    const chips = [...document.querySelectorAll(".pal-chips button")];
    const count = document.getElementById("pal-count");
    const empty = document.getElementById("pal-empty");
    let cat = "";
    const norm = (s) => s.toLowerCase().normalize("NFKD");
    const apply = () => {
      const q = norm(input.value.trim());
      let shown = 0;
      for (const li of items) {
        const text = norm(li.textContent + " " + li.dataset.who);
        const ok = (!cat || li.dataset.cat === cat) && (!q || text.includes(q));
        li.classList.toggle("hide", !ok);
        if (ok) shown++;
      }
      count.textContent = String(shown);
      empty.hidden = shown > 0;
    };
    input.addEventListener("input", () => {
      stopDemo();
      apply();
    });
    chips.forEach((b) =>
      b.addEventListener("click", () => {
        cat = b.dataset.cat;
        chips.forEach((c) => c.classList.toggle("on", c === b));
        apply();
      }),
    );
    // Until you type, the box types real asks by itself, and the grid filters along.
    const demos = ["PDF", "flight", "spend", "meeting", "cheaper", "quiz", "remind me", "where did I"];
    let demoOn = !reduced;
    let timer = 0;
    const stopDemo = () => {
      demoOn = false;
      clearTimeout(timer);
      input.placeholder = "Hey Pyxi, ";
    };
    input.addEventListener("focus", () => {
      if (!demoOn) return;
      demoOn = false;
      clearTimeout(timer);
      input.value = "";
      input.placeholder = "Hey Pyxi, ";
      apply();
    });
    let visible = false;
    new IntersectionObserver(([e]) => (visible = e.isIntersecting), { threshold: 0.2 }).observe(grid.parentElement);
    const cycle = async (n) => {
      if (!demoOn) return;
      if (!visible) {
        timer = setTimeout(() => cycle(n), 800);
        return;
      }
      const word = demos[n % demos.length];
      for (let i = 1; i <= word.length && demoOn; i++) {
        input.value = word.slice(0, i);
        apply();
        await new Promise((r) => (timer = setTimeout(r, 70)));
      }
      await new Promise((r) => (timer = setTimeout(r, 2600)));
      if (!demoOn) return;
      input.value = "";
      apply();
      await new Promise((r) => (timer = setTimeout(r, 900)));
      cycle(n + 1);
    };
    timer = setTimeout(() => cycle(0), 1800);
  }

  // The team: the firefly flies over to whoever you're looking at.
  const fly = window.pyxi?.fly;
  document.querySelectorAll(".mate").forEach((card) => {
    card.addEventListener("pointerenter", () => {
      if (!fly) return;
      const mark = card.querySelector(".mark");
      fly.point(mark, card.dataset.mate, { hold: 2400 });
    });
  });

  // The day: a mouse wheel over the track scrolls it sideways.
  const track = document.querySelector(".day-track");
  if (track) {
    track.addEventListener(
      "wheel",
      (e) => {
        if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
        const atStart = track.scrollLeft <= 0 && e.deltaY < 0;
        const atEnd = track.scrollLeft + track.clientWidth >= track.scrollWidth - 1 && e.deltaY > 0;
        if (atStart || atEnd) return; // let the page carry on scrolling at the ends
        e.preventDefault();
        track.scrollLeft += e.deltaY;
      },
      { passive: false },
    );
  }
}
