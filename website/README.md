# Pyxi website

The marketing and download site: home, `/download` (install and join the beta) and `/privacy`
(rendered from `docs/PRIVACY.md` at build time). Plain HTML, CSS and a little JS, built with Vite.
It deploys to Vercel as its own project, separate from the beta server in `cloud/`.

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # dist/
```

## One place to change things

`site.config.json` holds every value that changes between releases:

| Key | What it is |
|---|---|
| `downloadUrl` | Where every "Download for Windows" button points. The installer (about 304 MB) is too big for a Vercel static file, so host it on GitHub Releases or Vercel Blob and paste the URL here. |
| `downloadFile`, `downloadSize`, `version` | Shown next to the buttons. |
| `siteUrl` | The deployed address, used in canonical links and social previews. |
| `macWaitlistUrl` | Optional. When set, "Join the Mac waitlist" links appear. Empty means "Mac is next on the roadmap". |
| `contactEmail` | Optional. Fills the contact placeholder in the privacy policy and the "No code yet?" line. |

## The firefly

`src/firefly.js` is a port of the app's `Presence.tsx`: it lives on the dot of the "i" in the
headline, rides beside your cursor once you move it, and threads light to the exact control in
each real screenshot as you scroll (`<span class="pt" style="--x;--y" data-label>` marks the
target). The notch at the top is the nav, and it streams a line for each section.

With "reduce motion" on (Windows: Settings, Accessibility, Visual effects, Animation effects off)
the page stays calm: no typing, trail or tilt. Add `?motion=1` to the URL to show the full motion
anyway, for example in a demo on a PC with animations turned off.

## Images

- `python scripts/images.py` makes the WebP screenshots in `public/img` from `docs/images` and the icons from `desktop/resources/icon.png`.
- `scripts/capture_chip.py` renders the "how is an AI chip built?" model with Pyxi's own 3D renderer (needs the overlay preview: `cd desktop && npx vite --config vite.web.config.ts`).
- `..\.venv\Scripts\python scripts\shots.py og` renders `public/og.png` (the social preview) from `scripts/og.html`.
- `..\.venv\Scripts\python scripts\shots.py pages http://localhost:5173 <dir>` saves desktop and phone screenshots of every page.

## Deploy

```bash
npm run build                      # refreshes content/PRIVACY.md from docs/PRIVACY.md
npx vercel deploy --prod --scope marzuks-projects
```

Run from `website/` (the project is `pyxi-site`, live at https://pyxi-site.vercel.app). Vercel
only uploads this folder, so the build reads the committed copy in `content/PRIVACY.md`: build
locally after editing the policy, then deploy. `vercel.json` sets clean URLs (`/download`, `/privacy`), long caching for
hashed assets and a few security headers.
