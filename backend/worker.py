"""The render worker.

Run one or more of these alongside the API:

    python worker.py

They share nothing but the database and the object store, so adding capacity
is starting another one. Each worker runs a single job at a time -- ffmpeg
already saturates the cores it is given, so a second concurrent render on the
same box buys no throughput and only competes for the memory ceiling.

Sizing follows from that: give a worker 2 vCPU and 2 GB, and add workers rather
than making one bigger.

Shutdown
--------
SIGTERM (what a platform sends before replacing a container) stops the loop
from claiming anything new and lets the job in flight finish. If the platform's
grace period runs out first the process is killed mid-render, which is exactly
the case the queue exists for: the claim goes stale and another worker picks
the job up. Nothing is lost either way.
"""

from app import env  # noqa: F401  -- loads .env before anything reads keys

import signal
import sys
import time
import traceback

from app import billing, queue, runner
from app.db import init_db
from app.main import run_queued_job


def _run_and_log(job_id: str) -> None:
    print(f"[worker] running {job_id}")
    run_queued_job(job_id)

# How long to wait after finding nothing. Short enough that a submitted job
# starts promptly, long enough that an idle worker is not hammering the
# database all day. The API does not notify workers -- polling a table this
# cheaply is simpler than a listen/notify path that has to survive reconnects.
IDLE_SLEEP_SECONDS = float(__import__("os").environ.get("CLIPPER_WORKER_IDLE", "2"))

# How often to look for claims left behind by workers that died.
REAP_EVERY_SECONDS = 60.0

_shutting_down = False


def _request_shutdown(signum, _frame):
    global _shutting_down
    if _shutting_down:
        # A second signal means someone is impatient, or the platform has
        # escalated. Stop immediately and let the claim go stale.
        print("[worker] second signal -- exiting now")
        sys.exit(1)
    _shutting_down = True
    print(f"[worker] signal {signum} -- finishing the current job, then stopping")


def _reap() -> None:
    """Release claims from dead workers, refunding anything given up on."""
    # Read the give-ups BEFORE releasing, because release_stale deletes those
    # rows -- afterwards there is nothing left to identify them by.
    abandoned = queue.abandoned_job_ids()
    released = queue.release_stale()
    if released:
        print(f"[worker] released {released} stale claim(s) back to the queue")
    for job_id in abandoned:
        # These were charged when their clip count settled and never delivered.
        # The job row is already marked failed by release_stale; this returns
        # the credits, which is the part the user actually cares about.
        given = billing.refund_job(job_id)
        if given:
            print(f"[worker] refunded {given} credit(s) for abandoned {job_id}")


def main() -> int:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    init_db()
    name = queue.worker_name()
    print(f"[worker] {name} ready -- polling every {IDLE_SLEEP_SECONDS}s")

    last_reap = 0.0
    while not _shutting_down:
        now = time.monotonic()
        if now - last_reap > REAP_EVERY_SECONDS:
            try:
                _reap()
            except Exception:
                # Reaping is maintenance. It must never take the worker down,
                # or one bad row stops all rendering.
                print(f"[worker] reap failed\n{traceback.format_exc()}")
            last_reap = now

        started = time.monotonic()
        try:
            did_work = runner.work_once(_run_and_log, name)
        except Exception:
            # work_once already contains the per-job handler, so this is the
            # queue itself failing -- a dropped connection, most likely. Back
            # off and keep going; the alternative is a crash loop.
            print(f"[worker] queue unreachable\n{traceback.format_exc()}")
            time.sleep(IDLE_SLEEP_SECONDS)
            continue

        if not did_work:
            time.sleep(IDLE_SLEEP_SECONDS)
        else:
            print(f"[worker] job finished in {time.monotonic() - started:.0f}s")

    print("[worker] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
