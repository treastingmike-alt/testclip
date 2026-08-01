# Clipper

Turns a long YouTube video into short, vertical clips — with accurate,
speech-boundary-aligned cuts and burned-in captions, keeping the real
original audio by default.

## Why this exists / what it fixes vs a single-script version

1. **Accurate clip boundaries.** An LLM asked to output raw timestamps in
   seconds tends to guess, and guesses land mid-sentence. This project never
   lets the model invent a timestamp: Deepgram transcribes with exact
   utterance-level timing, we number those utterances, and the LLM only picks
   *which utterances* to include. The real start/end times are looked up
   afterward — so a clip always starts and ends on a real speech boundary.
2. **Burned-in captions.** Deepgram's word-level timestamps are reused to
   build short-form-style captions (a few words at a time), synced exactly to
   the real audio, and burned into the video with ffmpeg.
3. **No lossy cropping.** Vertical reframing uses a blurred, scaled copy of
   the frame as a background fill, with the untouched full frame centered on
   top — nothing gets cut off, which matters for screen recordings or
   anything not centered on a single face.
4. **Original audio by default.** The AI voiceover/narration path is opt-in
   only (`mode="voiceover"`), for cases like dubbing into another language.

## Architecture

```
React frontend  --HTTP-->  FastAPI backend  --calls-->  pipeline modules
                                 |
                                 v
                        storage/ (per-job folder:
                        source video, audio, srt, clips)
```

- `app/pipeline/downloader.py` — yt-dlp + ffmpeg audio extraction
- `app/pipeline/transcriber.py` — Deepgram transcription, word-level timestamps
- `app/pipeline/analyzer.py` — LLM picks utterance index ranges (not raw seconds)
- `app/pipeline/subtitles.py` — builds .srt captions from word timestamps
- `app/pipeline/render.py` — reframes to 9:16, cuts, burns captions
- `app/pipeline/voiceover.py` — optional AI narration (off by default)
- `app/jobs.py` — in-memory job tracking (swap for SQLite/Postgres later)
- `app/main.py` — FastAPI endpoints + pipeline orchestration

## Backend setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export OPENAI_API_KEY="sk-..."
export DEEPGRAM_API_KEY="..."

uvicorn app.main:app --reload --port 8000
```

Also requires `yt-dlp` and `ffmpeg` on PATH (e.g. `brew install yt-dlp ffmpeg`).

## Using it (no frontend yet — test with curl)

```bash
# Submit a job
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=XXXXXXXXXXX", "n_clips": 3}'

# -> {"job_id": "..."}

# Poll status
curl http://localhost:8000/jobs/<job_id>

# Once status is "done", download a clip:
curl -o clip_1.mp4 http://localhost:8000/clips/<job_id>/clip_1.mp4
```

## Roadmap (good next steps for the resume project)

- **Frontend**: a React page with a URL input, a progress view (poll
  `/jobs/{id}` every couple seconds), and a video grid to preview/download
  results. This is the natural next piece to build.
- **Persistence**: replace the in-memory `JOBS` dict in `app/jobs.py` with
  SQLite (via SQLModel/SQLAlchemy) so jobs survive a backend restart.
- **Real job queue**: swap `BackgroundTasks` for Celery/RQ + Redis if you want
  multiple jobs processing concurrently without blocking the API process.
- **Cloud storage**: swap local `storage/` for S3-compatible storage (e.g.
  Cloudflare R2) if you want this deployable rather than local-only.
- **Auth + rate limiting**: needed before this could be a public-facing product.

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Opens at http://localhost:5173 and proxies `/api/*` to the FastAPI backend at
http://localhost:8000 (start the backend first). Paste a YouTube URL, pick how
many clips, toggle captions, and hit Generate — the page polls job status and
shows a vertical video grid with download buttons when rendering finishes.
