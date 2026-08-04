"""Seeds a fake finished job so the editor can be exercised without API keys.

The real path needs Deepgram and an LLM to produce a transcript, and yt-dlp to
fetch a video. Neither is available in a dev/CI environment, which meant the
editor -- the most complex screen in the app -- could not be opened at all.

This writes one job with a hand-made transcript and ffmpeg-generated video, so
trimming, caption styling, colour overrides and export all run for real.

    python seed_demo.py            # prints the job id
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import SessionLocal, init_db          # noqa: E402
from app.models import Clip, Job, User            # noqa: E402

JOB_ID = "demo-job-0001"
# Owned by the admin test account when it exists, so the paid flows (share
# pages) have something they are allowed to act on. Ownership is checked on
# every share, so an unowned job could not be shared at all.
OWNER_EMAIL = "admin@clipper.test"
STORAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage")

SCRIPT = [
    "Most people never finish what they start.",
    "And the reason is almost never talent.",
    "It is that the first version always looks bad.",
    "So they quit at the exact moment it starts working.",
    "Ship it ugly, then make it good.",
    "That is the whole secret, honestly.",
]


def build_transcript():
    """Utterances with plausible word timings, in Deepgram's shape."""
    utterances, t = [], 1.0
    for line in SCRIPT:
        tokens = line.split()
        words, cur = [], t
        for tok in tokens:
            dur = 0.16 + 0.055 * len(tok)
            words.append({"word": tok.strip(".,"), "punctuated_word": tok,
                          "start": round(cur, 3), "end": round(cur + dur, 3)})
            cur += dur + 0.05
        utterances.append({"start": round(t, 3), "end": round(cur, 3),
                           "transcript": line, "words": words})
        t = cur + 0.45
    return {"results": {"utterances": utterances}}, round(t, 2)


def make_video(path, seconds, size="1280x720"):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path],
        check=True)


def main():
    init_db()
    transcript, duration = build_transcript()
    job_dir = os.path.join(STORAGE, JOB_ID)
    os.makedirs(job_dir, exist_ok=True)

    make_video(os.path.join(job_dir, "source.mp4"), duration + 2)
    # The clip itself is vertical, as the renderer would have left it.
    make_video(os.path.join(job_dir, "clip_1.mp4"), duration, size="1080x1920")
    # The editor's scrubbing proxy. proxy_window() decides its offset from the
    # clip bounds, and with a clip starting at 0 that offset is 0 -- so a proxy
    # of the whole source is exactly right here.
    make_video(os.path.join(job_dir, "preview_1.mp4"), duration + 2, size="640x360")

    with SessionLocal() as s:
        s.query(Clip).filter(Clip.job_id == JOB_ID).delete()
        old = s.get(Job, JOB_ID)
        if old:
            s.delete(old)
        s.commit()

        owner = s.query(User).filter(User.email == OWNER_EMAIL).first()
        s.add(Job(
            id=JOB_ID, url="https://example.com/demo", status="done", percent=100,
            user_id=owner.id if owner else None,
            progress_message="Done", transcript=transcript, source_duration=duration + 2,
            options={"n_clips": 1, "template": "classic", "ratio": "9:16",
                     "burn_subtitles": True, "auto_censor": True,
                     "frame": "blur", "caption_style": "classic",
                     "tighten_pauses": True, "watermark": "",
                     "max_source_minutes": 240},
        ))
        s.add(Clip(
            job_id=JOB_ID, index=0, file="clip_1.mp4",
            title="Ship it ugly, then make it good",
            hook="Why most people quit right before it works",
            start=0.0, end=round(duration, 2), duration=round(duration, 2),
            score=8.4,
            scores={"hook": 9, "standalone": 8, "emotion": 7, "ending": 8,
                    "payoff": 9, "share": 8},
            keywords=["secret", "ugly"],
            words=[w for u in transcript["results"]["utterances"] for w in u["words"]],
        ))
        s.commit()

    print(JOB_ID)


if __name__ == "__main__":
    main()
