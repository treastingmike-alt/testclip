# Test credentials

No seeded user accounts exist in this app — auth is optional and jobs can be
created signed out. Register a fresh account from the UI when a signed-in state
is needed (`POST /api/auth/register` with any email/password).

## Demo data for the editor

The real pipeline needs `DEEPGRAM_API_KEY` and `OPENAI_API_KEY`, which are not
set in this environment, so no job can be created end to end here. A seeded
finished job exists instead:

- Job id: `demo-job-0001`
- Open it in the UI at: `/?job=demo-job-0001`
- Re-seed with: `cd /app/backend && /root/.venv/bin/python seed_demo.py`

It has one clip with a full transcript, a rendered `clip_1.mp4` and an editor
proxy, so trimming, caption styling, colour overrides, the captions on/off
switch and export all work for real.
