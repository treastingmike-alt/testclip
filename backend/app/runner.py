"""One step of work, shared by the standalone worker and the inline dev worker.

Two things need to claim a job and run it: `worker.py`, which is a process you
scale in production, and the thread the API starts in development so that
`make dev` still processes jobs without a second terminal. The claim/run/report
sequence is identical and easy to get subtly wrong -- forgetting to release a
claim on failure wedges a job forever -- so it lives once, here.

Takes the pipeline as a callable rather than importing it: `run_queued_job`
lives in app.main, which imports the queue, and importing it back would be a
cycle. It also keeps this file testable with a stub instead of ffmpeg.
"""

import traceback

from app import queue
from app.jobs import update_job


def work_once(run_one, worker: str = None) -> bool:
    """Claim one job and run it. Returns True if there was anything to do.

    The caller decides what to do with False -- sleep, or exit. This never
    sleeps, so a test can drain a queue as fast as it likes.
    """
    worker = worker or queue.worker_name()
    job_id = queue.claim(worker)
    if not job_id:
        return False

    try:
        run_one(job_id)
        queue.complete(job_id)
    except Exception:
        # run_pipeline handles the failures it knows about -- it records the
        # message the user sees and returns their credits. Reaching here means
        # something it did not anticipate, and the important part is that the
        # row does not stay claimed: a claimed row nobody is working on is a job
        # that sits at "rendering" until the stale-claim reaper notices.
        print(f"[worker] job {job_id} failed\n{traceback.format_exc()}")
        try:
            update_job(job_id, status="failed",
                       error="This job stopped unexpectedly. Please try again.")
        finally:
            queue.fail(job_id)
    return True
