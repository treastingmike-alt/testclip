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

from app import billing, queue
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
        # run_pipeline handles the failures it KNOWS about -- it writes the
        # message the user sees and returns their credits. Reaching here means
        # something it did not anticipate: an OOM the process survived, a
        # dropped database connection, a failure inside its own handler.
        #
        # Those are properties of the moment rather than of the job, so this
        # retries rather than giving up. It used to call queue.fail(), which is
        # permanent -- making every transient crash a final one, and leaving
        # the retry machinery to cover only the case where a process died too
        # hard to reach this code at all.
        print(f"[worker] job {job_id} crashed\n{traceback.format_exc()}")
        try:
            if queue.retry(job_id):
                update_job(job_id, status="queued", percent=0,
                           progress_message="Something interrupted this — retrying...")
            else:
                # Out of attempts. Give the money back BEFORE reporting the
                # failure: a user who reads "we gave up" and still sees the
                # credits gone has lost twice.
                given = billing.refund_job(job_id)
                if given:
                    print(f"[worker] refunded {given} credit(s) for {job_id}")
                update_job(
                    job_id, status="failed",
                    error=("This kept stopping partway through, so we've given "
                           "up and returned your credits. Try a different video, "
                           "or upload the file instead of using a link."))
        except Exception:
            # The reporting itself failed -- most likely the same database
            # problem that caused the crash. Leave the claim in place rather
            # than swallowing it: release_stale will pick the job up once the
            # database is reachable again, which is the only thing that can
            # help here anyway.
            print(f"[worker] could not record the failure of {job_id}\n"
                  f"{traceback.format_exc()}")
    return True
