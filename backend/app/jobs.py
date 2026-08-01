"""Job persistence.

This was an in-memory dict, which meant a server restart lost every job and the
rendered clips on disk became orphans nobody could list. It is now backed by the
database while keeping the original create/get/update interface, so the pipeline
code calling it did not have to change.

Each call opens and closes its own short-lived session on purpose: the pipeline
runs on a FastAPI background thread and updates progress many times per job, and
holding one session open across a multi-minute render would pin a connection and
risk stale reads.
"""

from typing import Optional

from app.db import SessionLocal
from app.models import Clip, Job

# Fields that live on the Job row itself; anything else passed to update_job is
# handled specially (clips) or ignored.
JOB_FIELDS = {
    "status", "progress_message", "percent", "error",
    "transcript", "source_duration",
}


def create_job(url: str, options: dict, user_id: str = None) -> str:
    with SessionLocal() as session:
        job = Job(url=url, options=options, user_id=user_id, status="queued")
        session.add(job)
        session.commit()
        return job.id


def update_job(job_id: str, **fields):
    """Updates job columns, and replaces the clip set when `clips` is passed."""
    clips = fields.pop("clips", None)

    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if not job:
            return

        for key, value in fields.items():
            if key in JOB_FIELDS:
                setattr(job, key, value)

        if clips is not None:
            # Rendering reports the full list each time; replace wholesale so a
            # re-run cannot leave rows from a previous attempt behind.
            session.query(Clip).filter(Clip.job_id == job_id).delete()
            for n, c in enumerate(clips):
                session.add(Clip(
                    job_id=job_id,
                    index=n,
                    file=c.get("file", ""),
                    title=c.get("title", ""),
                    hook=c.get("hook", ""),
                    start=c.get("start", 0.0),
                    end=c.get("end", 0.0),
                    duration=c.get("duration", 0.0),
                    score=c.get("score"),
                    scores=c.get("scores"),
                    keywords=c.get("keywords"),
                    words=c.get("words"),
                ))

        session.commit()


def get_job(job_id: str) -> Optional[dict]:
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        return job.to_dict() if job else None


def list_jobs(user_id: str, limit: int = 50) -> list:
    """A signed-in user's history -- impossible before persistence existed."""
    with SessionLocal() as session:
        rows = (session.query(Job)
                .filter(Job.user_id == user_id)
                .order_by(Job.created_at.desc())
                .limit(limit)
                .all())
        return [j.to_dict() for j in rows]


def get_job_transcript(job_id: str) -> Optional[dict]:
    """Stored word-level transcript, for the clip editor."""
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        return job.transcript if job else None
