"""The job queue: what is waiting, who is running it, and what to do when a
worker dies holding one.

Why this exists
---------------
Work used to be dispatched with FastAPI's BackgroundTasks, which starts the
function in the same process that served the request. That has three
consequences that only show up in production:

  * A redeploy or an OOM kill loses every job in flight, with no record that
    they were ever running. The user has been charged and gets nothing.
  * Renders compete with API requests for the same CPU and the same memory
    limit, so submitting a job makes the whole site slow for everyone.
  * Capacity is whatever one container happens to be, and cannot be increased
    without also multiplying the API.

Moving the work behind a table fixes all three: a claim can be released and
retried, workers are separate processes that can be sized and scaled on their
own, and "how much can we process" becomes a number of workers rather than a
property of the web server.

Claiming
--------
The hard part of any database queue is two workers taking the same row.
Postgres has SELECT ... FOR UPDATE SKIP LOCKED for exactly this, which is used
where available. SQLite has no such thing, so the fallback is an optimistic
claim: read a candidate, then UPDATE it only if it is still unclaimed, and
believe the claim only if that UPDATE actually touched a row. Both paths are
exclusive; the Postgres one is simply cheaper under contention.

Nothing here imports the pipeline. The queue knows about rows, not rendering,
so it can be tested -- and reasoned about -- without ffmpeg anywhere near it.
"""

import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal, DATABASE_URL
from app.models import Job, QueuedJob

IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# A claim older than this is assumed to belong to a worker that died. Generous
# on purpose: it is the ceiling on how long a genuinely slow job may run before
# a second worker decides it is abandoned and starts it again. Long renders are
# normal here, and a duplicate render costs real money.
STALE_CLAIM_MINUTES = int(os.environ.get("CLIPPER_STALE_CLAIM_MINUTES", "45"))

# How many times a job may be picked up before it is given up on. This counts
# CRASHES, not rejections -- a job that fails cleanly is finished by fail(),
# which does not requeue. So this only bounds the "worker keeps dying on this
# input" case, where retrying forever would occupy the queue indefinitely.
MAX_ATTEMPTS = int(os.environ.get("CLIPPER_MAX_ATTEMPTS", "3"))

PRIORITY_PRO = 10


def worker_name() -> str:
    """Identifies a claim in the logs. Host plus pid, which is enough to tell
    two workers apart on one machine and two machines apart in one cluster."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; Postgres does not. Compare in UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def enqueue(job_id: str, priority: int = 0) -> None:
    """Make a job eligible to run.

    Idempotent: re-enqueuing something already waiting is a no-op rather than a
    second row, so a retried submission cannot cause two renders.
    """
    with SessionLocal() as session:
        existing = (session.query(QueuedJob)
                    .filter(QueuedJob.job_id == job_id).first())
        if existing:
            return
        session.add(QueuedJob(job_id=job_id, priority=priority))
        session.commit()


def dequeue(job_id: str) -> None:
    """Withdraw a job that was enqueued but cannot run after all.

    Distinct from fail() only in intent: this is for the submitting request
    giving up between enqueue and commit -- an upload whose bytes never reached
    storage, say -- where leaving the row would hand a worker a job whose input
    does not exist.
    """
    with SessionLocal() as session:
        session.query(QueuedJob).filter(QueuedJob.job_id == job_id).delete()
        session.commit()


def claim(worker: str = None) -> str | None:
    """Take the next eligible job, or None. Returns the job_id.

    Ordering is priority first, then age -- so a Pro job goes ahead of a Free
    one, but two jobs at the same priority are strictly first-come. Without the
    age tiebreak a busy queue could starve someone indefinitely.
    """
    worker = worker or worker_name()
    now = _now()

    with SessionLocal() as session:
        if IS_POSTGRES:
            # SKIP LOCKED lets N workers claim N different rows concurrently
            # without any of them blocking on the others.
            row = session.execute(text("""
                SELECT id FROM job_queue
                 WHERE claimed_at IS NULL AND available_at <= :now
                 ORDER BY priority DESC, created_at ASC
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
            """), {"now": now}).first()
            if not row:
                return None
            entry = session.get(QueuedJob, row[0])
            entry.claimed_at = now
            entry.claimed_by = worker
            entry.attempts += 1
            session.commit()
            return entry.job_id

        # SQLite and anything else: optimistic claim. The UPDATE's WHERE clause
        # is the lock -- if another worker got there first, it matches nothing
        # and rowcount is 0, so we simply look again.
        for _ in range(5):
            candidate = (session.query(QueuedJob)
                         .filter(QueuedJob.claimed_at.is_(None),
                                 QueuedJob.available_at <= now)
                         .order_by(QueuedJob.priority.desc(),
                                   QueuedJob.created_at.asc())
                         .first())
            if not candidate:
                return None
            taken = session.execute(text("""
                UPDATE job_queue
                   SET claimed_at = :now, claimed_by = :worker,
                       attempts = attempts + 1
                 WHERE id = :id AND claimed_at IS NULL
            """), {"now": now, "worker": worker, "id": candidate.id}).rowcount
            session.commit()
            if taken:
                return candidate.job_id
            session.expire_all()      # someone else won; re-read and retry
        return None


def complete(job_id: str) -> None:
    """The work is done. The row has no further purpose, so it goes."""
    with SessionLocal() as session:
        session.query(QueuedJob).filter(QueuedJob.job_id == job_id).delete()
        session.commit()


def fail(job_id: str) -> None:
    """The work failed in a way that will fail again. Stop scheduling it.

    Deliberately identical to complete(): a job that was refused -- a private
    video, a plan limit, an unreadable file -- gets the same answer every time,
    and retrying it would burn a worker slot on a foregone conclusion. The Job
    row already carries the error for the user. Only CRASHES are retried, via
    release_stale.
    """
    with SessionLocal() as session:
        session.query(QueuedJob).filter(QueuedJob.job_id == job_id).delete()
        session.commit()


def release_stale(older_than_minutes: int = None) -> int:
    """Return abandoned claims to the queue. Returns how many.

    This is the whole reliability argument for the queue. A worker killed
    mid-render -- redeploy, OOM, machine loss -- leaves a claim nobody will
    ever finish. Releasing it means the job resumes on another worker instead
    of sitting at 40% forever while the user waits for something that stopped
    existing.

    Jobs that have burned through MAX_ATTEMPTS are failed instead of released,
    so an input that reliably kills its worker cannot cycle forever.
    """
    cutoff = _now() - timedelta(minutes=older_than_minutes
                                if older_than_minutes is not None
                                else STALE_CLAIM_MINUTES)
    released = 0
    with SessionLocal() as session:
        stale = (session.query(QueuedJob)
                 .filter(QueuedJob.claimed_at.isnot(None),
                         QueuedJob.claimed_at < cutoff)
                 .all())
        for entry in stale:
            if entry.attempts >= MAX_ATTEMPTS:
                job = session.get(Job, entry.job_id)
                if job and job.status not in ("done", "failed"):
                    job.status = "failed"
                    job.error = ("This job stopped unexpectedly several times "
                                 "and we have given up on it. Your credits have "
                                 "been returned -- please try again.")
                session.delete(entry)
                continue
            entry.claimed_at = None
            entry.claimed_by = None
            # Back off a little so a job that kills workers does not immediately
            # take down the next one that picks it up.
            entry.available_at = _now() + timedelta(minutes=entry.attempts)
            released += 1
        session.commit()
    return released


def abandoned_job_ids(older_than_minutes: int = None) -> list:
    """Jobs whose claims are about to be released, for refunding.

    Separate from release_stale because giving credits back needs the billing
    module, and this one deliberately knows nothing about billing.
    """
    cutoff = _now() - timedelta(minutes=older_than_minutes
                                if older_than_minutes is not None
                                else STALE_CLAIM_MINUTES)
    with SessionLocal() as session:
        return [e.job_id for e in session.query(QueuedJob)
                .filter(QueuedJob.claimed_at.isnot(None),
                        QueuedJob.claimed_at < cutoff,
                        QueuedJob.attempts >= MAX_ATTEMPTS).all()]


def position(job_id: str) -> int | None:
    """How many jobs are ahead of this one. 0 means it is next; None means it
    is not waiting (already running, or finished).

    Shown to the user, because a wait you can see the end of is a queue and a
    wait you cannot is a bug. This is the difference between "it's stuck" and
    "there are two ahead of me".
    """
    with SessionLocal() as session:
        entry = (session.query(QueuedJob)
                 .filter(QueuedJob.job_id == job_id).first())
        if not entry or entry.claimed_at is not None:
            return None
        return (session.query(QueuedJob)
                .filter(QueuedJob.claimed_at.is_(None),
                        # Strictly ahead: higher priority, or equal priority and
                        # older. Mirrors claim()'s ORDER BY exactly, or the
                        # number shown would not match the order served.
                        ((QueuedJob.priority > entry.priority)
                         | ((QueuedJob.priority == entry.priority)
                            & (QueuedJob.created_at < entry.created_at))))
                .count())


def stats() -> dict:
    """Queue depth, for health checks and for deciding when to add a worker."""
    with SessionLocal() as session:
        waiting = (session.query(QueuedJob)
                   .filter(QueuedJob.claimed_at.is_(None)).count())
        running = (session.query(QueuedJob)
                   .filter(QueuedJob.claimed_at.isnot(None)).count())
        return {"waiting": waiting, "running": running,
                "total": waiting + running}
