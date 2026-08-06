# Where media lives

Rendered media goes through a driver (`app/storage.py`) instead of straight to
local disk. Two implementations, chosen by `STORAGE_BACKEND`:

| | `local` (default) | `r2` |
|---|---|---|
| Where | `backend/storage/<job_id>/` | Cloudflare R2 bucket |
| Downloads | streamed by the API | 302 to a signed URL |
| Survives a redeploy | no | yes |
| Works on >1 instance | no | yes |
| Egress cost | your host's | none (R2 charges no egress) |

Local is the default so a fresh checkout and every test run with no
configuration. **Nothing about local development changes.**

## Why this exists

A container filesystem is ephemeral. Without object storage:

- every deploy deletes clips users paid for, while the database still lists the
  job — so the row looks clickable and 404s, which reads as data loss;
- two instances do not share a disk, so a clip rendered on one is missing on the
  other. This is the hard ceiling on scaling past a single container;
- every byte of video streams through the API, competing with real requests.

## Enabling R2

Create a bucket and an R2 API token, then set:

```
STORAGE_BACKEND=r2
R2_BUCKET=klipcut
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
```

Optional, if the bucket is published on a custom domain — URLs are then
unsigned and CDN-cacheable, but **anyone with a link keeps access forever**, so
only use it if clips are meant to be public:

```
R2_PUBLIC_BASE=https://cdn.yourdomain.com
```

Missing variables raise at startup rather than at the first upload. A job that
transcribes, analyses and renders before discovering it has nowhere to put the
result has already spent real money.

## What gets stored

```
<job_id>/clip_1.mp4        the deliverable
<job_id>/preview_1.mp4     editor scrubbing proxy, one per clip
<job_id>/source.mp4        kept so an edit can re-cut without re-downloading
```

Keys match the old directory layout, so nothing needs migrating.

Two ordering rules that matter:

- Clips publish **before** the job is marked `done`. Status is what makes a clip
  downloadable, so flipping it first opens a window where a user can click a
  clip that is not in storage yet.
- Proxies publish **individually as each finishes**, because they build on a
  thread that outlives the pipeline. A batch upload at job-done would race
  whichever encode was still running.

`source.mp4` is what lets an edit re-cut on a container that never rendered the
job. Without it, editing falls back to re-downloading from YouTube, which now
frequently refuses — gambling the user's saved work on a download.

Retention deletes the bucket prefix as well as the local directory. Object
storage does not expire on its own and nothing else revisits these keys.

## Verifying

`moto` provides a real S3 server locally, so the R2 path can be exercised
without credentials:

```bash
pip install "moto[s3]" flask
```

Covered by the checks written during this change: upload, content-type,
presigned signing, a signed URL actually serving bytes, download round-trip,
prefix deletion, endpoints returning 302 under `r2` and 200 under `local`,
range requests still returning 206 locally, and path traversal returning 404
rather than signing a URL outside the job's namespace.
