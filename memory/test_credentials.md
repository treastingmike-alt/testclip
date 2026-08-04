# Test credentials

## Admin / paid-experience account

`ADMIN_EMAILS` in `/app/backend/.env` grants every entitlement regardless of
plan, which is how the paid experience is tested without a payment provider.

- Email: `admin@clipper.test`
- Password: `clipperadmin123`
- Effect: all entitlements (share pages, gameplay, pause tightening, mixed
  language, no watermark), Pro limits (10 clips, 240 min, 4096 MB)

Any other account you register from the UI is a **free** plan: 2 clips per
video, 30-minute sources, 500 MB uploads, watermarked exports, share pages
locked. Register from the UI or `POST /api/auth/register`.

## Demo data

The real pipeline needs `DEEPGRAM_API_KEY` and `OPENAI_API_KEY`, which are NOT
set here, so a new job cannot be created end to end. A seeded finished job
exists instead:

- Job id: `demo-job-0001` (owned by `admin@clipper.test`)
- Open it in the UI at: `/?job=demo-job-0001`
- Re-seed with: `cd /app/backend && /root/.venv/bin/python seed_demo.py`

It has one clip with a full transcript, a rendered `clip_1.mp4` and an editor
proxy, so trimming, caption styling, colour overrides, the captions on/off
switch, export and share pages all work for real.

Note: re-seeding recreates the job, which invalidates any share token minted
against the old row. Mint a new one via
`POST /api/jobs/demo-job-0001/clips/0/share`.
