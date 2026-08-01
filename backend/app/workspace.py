"""Disk hygiene for job working directories.

A run downloads a source video that can be several hundred MB, then renders
clips that are a few MB each. Only the clips matter once rendering finishes, so
holding onto sources is what fills a disk: a handful of runs on long videos is
several gigabytes, and the failure mode is an ENOSPC crash partway through a
download rather than anything actionable.

So: check there is room before committing to a download, drop the source once
the clips exist, and sweep away old jobs.
"""

import os
import shutil
import time

# Rendering needs room for the source, the muxed output, and ffmpeg's scratch
# space all at once, so require meaningfully more headroom than the raw download.
HEADROOM_FACTOR = 2.2
MIN_FREE_BYTES = 512 * 1024 * 1024        # never fill the disk to the last byte

# Clips are the thing the user paid for and expects to find later. 48h was a
# development default and is far too aggressive for that -- someone who clips on
# Friday should still have it on Monday. Override with CLIPPER_RETENTION_HOURS.
DEFAULT_MAX_AGE_HOURS = int(os.environ.get("CLIPPER_RETENTION_HOURS", 24 * 30))

# Everything a job writes that is disposable once the clips are rendered.
INTERMEDIATE_SUFFIXES = (".m4a", ".webm", ".part", ".ytdl", ".src", ".ass", ".srt")
INTERMEDIATE_PREFIXES = ("_ovtext_",)
INTERMEDIATE_NAMES = ("audio.mp3",)


def free_bytes(path: str) -> int:
    """Free space on the volume holding path (walks up to an existing parent)."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(probe).free


def human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit != "GB" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def ensure_space(path: str, needed_bytes: int) -> None:
    """Raises with a readable message rather than letting yt-dlp die on ENOSPC."""
    if not needed_bytes:
        return

    required = int(needed_bytes * HEADROOM_FACTOR) + MIN_FREE_BYTES
    available = free_bytes(path)
    if available < required:
        raise RuntimeError(
            f"Not enough disk space. This video needs about {human(required)} free "
            f"to download and render, but only {human(available)} is available. "
            f"Free up some space and try again."
        )


def clear_intermediates(job_dir: str) -> int:
    """Deletes working files, keeping the source, proxy and rendered clips.

    The source video is KEPT deliberately. Exporting an edit re-cuts from it, so
    deleting it turned every export into a fresh YouTube download -- which fails
    outright whenever YouTube demands a sign-in. An editor must not depend on
    the internet to save your work. Disk is reclaimed by the retention sweep
    instead, which is the right place for that trade.

    Returns bytes reclaimed. Never raises -- losing the cleanup is not a reason
    to fail a job whose clips already rendered successfully.
    """
    reclaimed = 0
    try:
        for name in os.listdir(job_dir):
            path = os.path.join(job_dir, name)
            if not os.path.isfile(path):
                continue

            disposable = (
                name.startswith(INTERMEDIATE_PREFIXES)
                or name in INTERMEDIATE_NAMES
                or name.endswith(INTERMEDIATE_SUFFIXES)
            )
            # Guard: the rendered clips are the deliverable.
            if name.startswith("clip_") and name.endswith(".mp4"):
                disposable = False

            # Guard: the proxy is what makes editing instant. It is small
            # (a few MB) and regenerating it means re-downloading the source,
            # which YouTube increasingly refuses. Always worth keeping.
            if name == "preview.mp4":
                disposable = False

            if disposable:
                try:
                    size = os.path.getsize(path)
                    os.remove(path)
                    reclaimed += size
                except OSError:
                    pass
    except OSError:
        pass
    return reclaimed


def purge_old_jobs(storage_dir: str, max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> int:
    """Removes job folders untouched for longer than max_age_hours.

    Each purged job is marked "expired" in the database, so the dashboard can
    say "clips expired" instead of listing a job whose files silently no longer
    exist -- a row that looks clickable and then 404s reads as data loss.
    """
    if not os.path.isdir(storage_dir):
        return 0

    cutoff = time.time() - max_age_hours * 3600
    removed_ids = []
    for name in os.listdir(storage_dir):
        job_dir = os.path.join(storage_dir, name)
        if not os.path.isdir(job_dir):
            continue
        try:
            if os.path.getmtime(job_dir) < cutoff:
                shutil.rmtree(job_dir, ignore_errors=True)
                removed_ids.append(name)
        except OSError:
            pass

    if removed_ids:
        # Imported here to keep this module usable without a database.
        try:
            from app.db import SessionLocal
            from app.models import Job
            with SessionLocal() as session:
                (session.query(Job)
                 .filter(Job.id.in_(removed_ids), Job.status == "done")
                 .update({Job.status: "expired"}, synchronize_session=False))
                session.commit()
        except Exception:
            pass
    return len(removed_ids)
