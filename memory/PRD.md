# Clipper — PRD / working notes

## Original problem statement (June 2026)

> make the ui a little better ux things and a lot of things better this is a
> clipper project where people will clip their long form videos. there is
> already a frontend folder (not `frontend-premium`, that is something else) —
> refine the ui a little better, currently there are some inconsistencies and a
> bad ux in some cases. some buttons should be toggles, the hover state is bad,
> and the colour while uploading looks bad. Or you can add a few things —
> understand the project then take steps.

### User's stated choices
- Polish **all** of: landing/upload, clip editor, dashboard & navigation.
- Keep the current cream/light theme **and** add a dark theme; fix inconsistencies.
- The multicolour glow behind the upload bar should be subtle / replaced.
- Premium-feeling UX additions welcome (toasts, skeletons, shortcuts, progress).
- Editor specifically: **more caption styles**, **caption colour is stuck on
  white — make it changeable**, and a **captions on/off toggle while editing**.

## Architecture

- `frontend/` — React 18 + Vite (the real frontend). `frontend-premium/` is
  unrelated and untouched.
- `backend/` — FastAPI + SQLAlchemy (SQLite). `server.py` is the preview
  entrypoint: it mounts `app.main` under `/api` because the platform ingress
  routes `/api/*` straight to port 8001.
- Pipeline: yt-dlp → Deepgram → LLM clip selection → ffmpeg render with a
  generated ASS subtitle track. Requires `DEEPGRAM_API_KEY` + `OPENAI_API_KEY`
  (not set in this environment — see the demo seed below).
- Editing is non-destructive: `Clip.edit` stores a *recipe*, and
  `POST /clips/{i}/export` re-renders from it.

## Implemented — June 2026 (this session)

### Made the app runnable in this environment
- `backend/server.py` mounts the app under `/api`; added `sqlalchemy` to
  requirements; installed ffmpeg; `yarn start` script + Vite host/HMR config.
- `backend/seed_demo.py` seeds job `demo-job-0001` with a hand-written
  transcript and ffmpeg-generated video, so the editor and export can be
  exercised without API keys.

### Editor (the user's specific asks)
- **Caption colour**: `caption_color` / `caption_active_color` on the recipe,
  applied by `subtitles._recolour()` via a new `caption_presets.css_to_ass()`.
  Two swatch rows in the editor (`cap-text-colour`, `cap-active-colour`) with a
  curated palette, a custom colour input and a reset.
- **Captions on/off**: `captions_on` on the recipe; a switch
  (`captions-toggle`) that hides the styling controls, drops the overlay from
  the live preview, and makes the export skip the ASS file entirely.
- **11 new presets** in two new families whose *base* text is coloured, not
  white: `vibrant` (Sunset, Mint, Bubblegum, Ice, Gold, Crimson, Royal) and
  `clean` (White Plate, Dark Plate, Soft, Editorial).
- Keyboard shortcuts: Space play/pause, ←/→ scrub (⇧ = 2s), `c` captions,
  Esc close, plus a visible legend in the footer.
- Export confirmation no longer closes the editor on a stray backdrop click,
  and also raises a toast.

### Landing / upload
- Replaced the spinning violet-lime-indigo conic halo behind the URL bar with a
  still, single-family violet/indigo aura that brightens on focus.
- The source switcher is a real segmented control with a sliding thumb; hover on
  the unselected side no longer looks like a selection.
- Upload mode is a drag-and-drop zone with file name and size, not a file input
  disguised as a text field.
- Disabled buttons render as a neutral "empty slot" instead of a muddy grey
  pill, and carry a tooltip saying what is missing.
- Boolean option chips are toggle switches; dropdown chips keep the caret. The
  locked "Mixed language" chip is readable and sells the upgrade.

### Global
- Dark theme (`polish.css`, `html[data-theme]`), toggled from the nav and
  persisted in `localStorage`. Light stays the default. Introduced
  `--ink-solid` for surfaces that are dark *by design* in both themes.
- Toast system (`Toast.jsx`) for job start/finish/failure and export.
- The marketing nav is stowed while the studio or dashboard is open.
- Focus-visible rings, `::selection` colour, reduced-motion support.

## Backlog

### P1
- The editor's ←/→ step in *source* time, so a step that lands inside a
  tightened pause appears to do nothing. Step in output time instead.
- Loading skeletons for the dashboard job list and the clip grid.
- Top-up credit packs are display-only — no checkout is wired.

### P2
- Audit the rest of `styles.css` for literal colours; a few rare panels
  (billing edge cases, some picker menus) have only been spot-checked in dark.
- `main.py` CORS `allow_origins` is localhost-only; fine behind this ingress,
  not for a direct-origin deployment.
- Mobile pass on the editor (the inspector column stacks but has not been
  reviewed at phone widths).
