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

## Implemented — June 2026, session 2

### Bug fix: job history timestamps
`models._iso_utc()`. SQLite has no timezone type, so a datetime written as aware
UTC read back naive, and `isoformat()` produced an offsetless string that the
browser parsed as *local* time — a job made a minute ago showed as "6 hours
ago" for anyone east of UTC, off by exactly their own offset. Applied to
`Job.to_dict`, the credit ledger and share pages.

### The studio is a 3-step wizard
`StudioWizard.jsx`. It used to be one screen where the source row, a collapsed
"Output settings" drawer and Generate coexisted, so the order was implied by
vertical position and nothing confirmed what was about to run. Now:
**1 Source → 2 Preferences → 3 Review**, with a progress rail (completed steps
are clickable, future ones are not), grouped preference sections, and a review
card listing every choice and its credit cost before anything is spent.

### Free vs premium, bifurcated
- `billing.LIMITS` — free: **2 clips/video, 30-min sources, 500 MB uploads**;
  creator: 10 / 240 min / 2 GB; pro: 10 / 240 min / 4 GB.
- `billing.ENTITLEMENTS` — paid: `gameplay`, `tighten_pauses`, `multilingual`,
  `priority`, `branding`, `no_watermark`, `share_pages` (+ `bulk_export` on pro).
- Enforced server-side by `main._gate()` (HTTP 402 with a descriptive reason) —
  the UI locks are presentation only.
- Locked controls stay **visible** with a lock and a tooltip, plus one
  "What's in Creator?" strip at the end of step 2. `UpgradeModal.jsx` names the
  feature the person just reached for and links to pricing. No per-control nags.
- Limits are stated in step 1, before they bite, instead of arriving as a
  rejection after Generate. Clip count is clamped rather than blocked.

### Watermark on free exports
`render.watermark_filter()` — a burned-in credit at the foot of the frame, below
the caption band and inside the safe area, so it survives re-encoding and
platform cropping. The decision is **frozen onto `job.options.watermark` at
creation**, so a re-export months later matches the file it replaces and a
lapsed subscriber cannot strip it by re-exporting. Text comes from
`CLIPPER_WATERMARK` because the product name is not final.

### Public share pages (premium)
`Share` model + `POST /jobs/{id}/clips/{i}/share`, `GET /share/{token}`,
`GET /share/{token}/video`. Opt-in per clip, ownership-checked, one idempotent
token per clip, no listing endpoint — the token is the only capability.
`SharePage.jsx` renders at `/s/<token>` (resolved from the path in `main.jsx`;
the app has no router and this is the only public path) with the clip, its score
breakdown, the hook and a "Made with Clipper" footer CTA.

### Notes
- `usePlan.js` derives paid state from the **entitlement list**, not the plan
  name: the `ADMIN_EMAILS` bypass grants entitlements while leaving `plan` at
  "free", and a plan-string check told admins their exports would be
  watermarked when the server had already decided otherwise.
- CORS origins now come from `CLIPPER_CORS_ORIGINS`.

## Backlog

### P0 — deferred by the user
- **Emoji in a burned-in title do not render.** libass needs a font with those
  glyphs; Noto Emoji would render monochrome. The user chose to leave it.

### P1
- The editor's ←/→ step in *source* time, so a step landing inside a tightened
  pause appears to do nothing. Step in output time instead.
- Loading skeletons for the dashboard job list and the clip grid.
- Payments are not wired: `/billing/checkout` returns `provider_not_configured`
  and grants nothing, so nobody can actually reach a paid plan yet — the only
  route to the paid experience is `ADMIN_EMAILS`. Top-up packs are display-only.
- `rerender` and `export` on the same clip take no lock; concurrent calls race.

### P2
- Audit the rest of `styles.css` for literal colours; a few rare panels have
  only been spot-checked in dark.
- Mobile pass on the editor and the wizard.
- Share pages have no revoke, and no view analytics beyond a counter.

