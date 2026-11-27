// The booking clips' shared machinery (movie.html, doctor.html), on top of kit.js.
//
// A booking in the app goes: Pyxi's OK card (which details Scout may use), then Scout's own
// browser window opens where you can watch it, Scout clicks and types its way through the
// site, and it stops at the two moments that are yours: the final booking click (an Allow
// card) and payment (a "Your turn" card: Scout never types a card number). A page gives:
//
//   base     the screen underneath, as html
//   site     { tab, url, color } of the made-up site Scout opens
//   pages    the site's pages, as html, in order
//   cards    [{ at, click, x, y, title, action, reason, yes, no }]: `at` and `click` name times in the plan
//   plan     (d, B) => the clip's times (kit.js), with `open` (Scout's window) among them
//   script   (T) => what Scout does: [{ at, page }], [{ at, click: sel }], [{ at, on: sel, cls }], [{ at, type: sel, text, d }]
//   cam      (T, box) => camera keys after the opening; box(sel) measures an element
//   notch    the notch's rows (kit.js)
const Booking = {};
Booking.make = function (cfg) {
  Film.scene({
    id: cfg.id,
    screen: cfg.base,
    overlay: `
      <div id="scout">
        <div class="tabs"><div class="tab"><u style="background:${cfg.site.color}"></u>${cfg.site.tab}</div><div class="by"><i></i>Scout's browser</div><div class="dots"><s></s><s></s><s></s></div></div>
        <div class="addr"><b></b><b></b><div>${cfg.site.url}</div></div>
        <div id="pages">${cfg.pages.map((p, i) => `<div class="pg" id="pg${i}">${p}</div>`).join("")}<div id="sring"></div><div id="sdot"></div></div>
      </div>
      ${cfg.cards.map((c, i) => `<div class="request" id="rq${i}" style="left:${c.x}px; top:${c.y}px"><div class="rt">${c.title}</div><div class="ra">${c.action}</div><div class="rr">${c.reason}</div>
        <div class="rb"><u class="yes">${c.yes}</u><u>${c.no}</u></div></div>`).join("")}`,
    plan: cfg.plan,
    notch: cfg.notch,

    init(T) {
      const scout = $("#scout"), pages = boxOf("#pages");
      scout.style.transform = "none";
      this.ev = cfg.script(T);
      // where Scout clicks, inside its own page
      this.clicks = this.ev.filter((e) => e.click).map((e) => { const b = boxOf(e.click); return { at: e.at, x: b.cx - pages.x, y: b.cy - pages.y }; });
      const first = this.clicks[0] || { x: 600, y: 300 };
      this.sstops = [{ at: 0, d: 0, x: first.x + 260, y: first.y + 170 }, ...this.clicks.map((c) => ({ at: c.at - 0.4, d: 0.36, x: c.x, y: c.y }))];
      // your own pointer: it only ever presses the cards' buttons
      this.cards = cfg.cards.map((c, i) => { const el = $("#rq" + i); el.style.transform = "none"; return { ...c, el, yes: boxOf($(".yes", el)), box: boxOf(el) }; });
      this.stops = [{ at: 0, d: 0, x: 1180, y: 700 }];
      for (const c of this.cards) if (c.click) this.stops.push({ at: T[c.click] - 0.7, d: 0.6, x: c.yes.cx + 4, y: c.yes.cy + 4 }, { at: T[c.click] + 0.35, d: 0.8, x: 1640, y: 860 });
      this.cam = [
        { at: 0, v: [960, 539, 0.97, -8, 4] },
        { at: T.listen - 0.2, d: 0.6, v: [960, 300, 1.55, -4, 2] },
        { at: T.look - 0.2, d: 0.7, v: [960, 539, 0.97, -3, 1.4] },
        ...cfg.cam(T, (sel) => boxOf(sel), this.cards.map((c) => c.box)),
      ];
    },

    draw(t, T) {
      // Scout's window opens over the screen
      const scout = $("#scout"), o = back(seg(t, T.open, T.open + 0.45));
      scout.style.opacity = seg(t, T.open, T.open + 0.12);
      scout.style.transform = `translateY(${46 * (1 - o)}px) scale(${mix(0.9, 1, o)})`;
      let page = 0, pageAt = 0;
      for (const e of this.ev) {
        if (e.page !== undefined && t >= e.at) { page = e.page; pageAt = e.at; }
        if (e.on) $(e.on).classList.toggle(e.cls, t >= e.at);
        if (e.type) {
          const el = $(e.type), n = Math.round(e.text.length * seg(t, e.at, e.at + e.d));
          const text = e.text.slice(0, n) + (t >= e.at && t < e.at + e.d + 0.25 && Math.floor(t * 2.6) % 2 === 0 ? "|" : "");
          if (el.textContent !== text) el.textContent = text;
        }
      }
      cfg.pages.forEach((_, i) => { $("#pg" + i).style.opacity = i === page ? seg(t, pageAt, pageAt + 0.16) : i < page ? 0 : 0; });
      if (page > 0 && t < pageAt + 0.16) $("#pg" + (page - 1)).style.opacity = 1 - seg(t, pageAt, pageAt + 0.16);
      // the spot where Scout clicks
      const [sx, sy] = pointer(t, this.sstops), last = this.clicks[this.clicks.length - 1];
      const shown = seg(t, T.open + 0.35, T.open + 0.55) * (last ? 1 - seg(t, last.at + 0.5, last.at + 0.8) : 0);
      let press = 0;
      for (const c of this.clicks) if (t >= c.at && t < c.at + 0.5) press = (t - c.at) / 0.5;
      $("#sdot").style.opacity = shown; $("#sdot").style.transform = `translate(${sx}px, ${sy}px) scale(${press > 0 && press < 0.3 ? 0.78 : 1})`;
      $("#sring").style.opacity = press > 0 ? (1 - press) * shown : 0; $("#sring").style.transform = `translate(${sx}px, ${sy}px) scale(${0.5 + 1.3 * press})`;

      // the app's cards: each waits for your click, if it has one
      for (const c of this.cards) {
        const at = T[c.at], click = c.click ? T[c.click] : Infinity;
        const u = back(seg(t, at, at + 0.4)), gone = c.click ? seg(t, click + 0.08, click + 0.3) : 0;
        c.el.style.opacity = seg(t, at, at + 0.1) * (1 - gone);
        c.el.style.transform = `translateY(${-16 * (1 - u) - 10 * gone}px) scale(${mix(0.94, 1, u)})`;
        $(".yes", c.el).classList.toggle("on", t > click - 0.02);
      }
      if (cfg.draw) cfg.draw(t, T);
      const k = keyed(t, this.cam);
      return { ptr: pointer(t, this.stops), cam: { x: k[0], y: k[1], s: k[2], ry: k[3], rx: k[4] } };
    },
  });
};
