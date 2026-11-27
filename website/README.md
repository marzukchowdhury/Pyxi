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
| `macDownloadUrl` | Optional. The Apple silicon `.dmg` on a GitHub release (its name has the version in it, so update this with each Mac release). When set, "Download for Mac" buttons and the Mac install steps (`/download#mac`) appear, in place of the waitlist. Leave it empty until a Mac build has been tried on a real Mac. |
| `macIntelDownloadUrl`, `macDownloadSize` | Optional. The Intel `.dmg`, and the size shown next to the Mac button. |
| `macWaitlistUrl` | Optional. When set (and there's no Mac download), "Join the Mac waitlist" links appear. With neither, the site says "Mac is next on the roadmap". |
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

## The demo clips and the film

The clips on the home page are designed demos, built the way `docs/FILM-MOCKUP.md` describes: nothing is recorded from a screen. Never call them screen recordings.

- `film3/story.js` holds every clip's words: the problem line, the ask, what Pyxi says, the payoff. Change them there.
- `film3/<scene>.html` (`setting`, `post`, `pivot`, `desktop`) draws one scene as a function of time; `film3/kit.js` and `kit.css` hold the shared stage and Pyxi's interface, copied from the app's overlay.
- `..\.venv\Scripts\python film3\render.py look <scene>` makes a quick contact sheet in `.shots/film3/look`. `... render.py all` renders everything (about ten minutes): masters with Pyxi's voice in `public/clips/master`, the silent loops and posters in `public/clips` (`demo-*.mp4`, `.webm`, `.webp`), the film (`pyxi-film.mp4`) and its preview loop. Full-quality masters and the contact sheets stay in `.shots/film3`. Needs `PYTHONPATH` set to the repo's `src`, Pyxi's voice pack, and ffmpeg on PATH.
- `..\.venv\Scripts\python film3\listen.py` prints a transcript and the loudness of each master.
- `npm run build`, then `..\.venv\Scripts\python scripts\home_check.py` checks the home page at a desktop and a phone size: weight before any video plays, layout shift, console errors, that clips play only on screen, and that the film plays with sound.
- The music in the masters is a stand-in made in `render.py`. Swap in a licensed track before posting them.

## Deploy

```bash
npm run build                      # refreshes content/PRIVACY.md from docs/PRIVACY.md
npx vercel deploy --prod --scope marzuks-projects
```

Run from `website/` (the project is `pyxi-site`, live at https://pyxi-site.vercel.app). Vercel
only uploads this folder, so the build reads the committed copy in `content/PRIVACY.md`: build
locally after editing the policy, then deploy. `vercel.json` sets clean URLs (`/download`, `/privacy`), long caching for
hashed assets and a few security headers.
