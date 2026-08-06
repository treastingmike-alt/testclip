"""Where rendered media lives, and how it reaches a browser.

Until now this was implicit: everything wrote to `backend/storage/<job_id>/` and
every download was a `FileResponse` off local disk. That is exactly right for
one machine and fails in three ways the moment there is more than one:

  * A container filesystem is ephemeral. On a platform that replaces the
    container each deploy, every clip a user paid for disappears on the next
    push while the database still lists the job -- so the row looks clickable
    and 404s, which reads as data loss because it is.
  * Two instances do not share a disk. A clip rendered on instance A simply
    does not exist on instance B, so downloads fail at random as soon as the
    service scales past one. This is the hard ceiling.
  * Every byte of video streams through the API process, competing with real
    requests and billed as egress.

So media moves behind a driver. Local development keeps the filesystem and
behaves exactly as it does today; production points at S3-compatible object
storage (Cloudflare R2) and hands out short-lived signed URLs so the bytes go
straight from the object store to the browser.

Keys are `<job_id>/<filename>`, matching the directory layout that came before,
so nothing has to be migrated or renamed.

ffmpeg and yt-dlp only understand local paths. The job directory therefore
remains real scratch space; this module is the boundary that work crosses on
its way out, not a filesystem replacement.
"""

from app import env  # noqa: F401  -- .env must be read BEFORE _build() runs

import os
import shutil

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "storage")


class LocalDriver:
    """The filesystem, i.e. what this app has always done.

    Kept as a first-class driver rather than a fallback so local development
    exercises the same call sites production does. A dev path that skips the
    abstraction is a dev path that cannot catch bugs in it.
    """

    name = "local"
    serves_redirects = False

    def __init__(self, root: str = STORAGE_DIR):
        self.root = root

    def path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def put(self, key: str, local_path: str) -> None:
        """No-op when the file is already at its key. Renders write in place."""
        target = self.path(key)
        if os.path.abspath(local_path) == os.path.abspath(target):
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(local_path, target)

    def download(self, key: str, local_path: str) -> str:
        source = self.path(key)
        if os.path.abspath(source) != os.path.abspath(local_path):
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            shutil.copy2(source, local_path)
        return local_path

    def exists(self, key: str) -> bool:
        return os.path.exists(self.path(key))

    def size(self, key: str) -> int:
        return os.path.getsize(self.path(key))

    def url(self, key: str, expires: int = 3600, filename: str = "") -> str | None:
        """None means "no direct URL" -- the caller serves the bytes itself."""
        return None

    def delete_prefix(self, prefix: str) -> int:
        target = self.path(prefix)
        if not os.path.isdir(target):
            return 0
        shutil.rmtree(target, ignore_errors=True)
        return 1


class R2Driver:
    """Cloudflare R2 over the S3 API.

    R2 rather than S3 for one specific reason: zero egress fees. This service
    serves video, so egress is the dominant cost and the difference is not
    marginal.

    Downloads are signed URLs the browser fetches directly. That keeps video
    bytes off the API entirely, and hands HTTP Range support -- which the editor
    depends on for scrubbing -- to infrastructure that already implements it
    correctly, instead of a hand-rolled version here.
    """

    name = "r2"
    serves_redirects = True

    def __init__(self, bucket: str, endpoint: str, key_id: str, secret: str,
                 public_base: str = ""):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.public_base = (public_base or "").rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            # R2 speaks the v4-signing S3 API but is not in any AWS region.
            # "auto" is what Cloudflare documents; a real region name signs
            # requests R2 then rejects.
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def put(self, key: str, local_path: str) -> None:
        extra = {}
        if key.endswith(".mp4"):
            # Set at upload time because a signed GET cannot add it later, and
            # without it browsers download the file instead of playing it.
            extra["ContentType"] = "video/mp4"
        elif key.endswith(".png"):
            extra["ContentType"] = "image/png"
        elif key.endswith(".json"):
            extra["ContentType"] = "application/json"
        self.client.upload_file(local_path, self.bucket, key, ExtraArgs=extra or None)

    def download(self, key: str, local_path: str) -> str:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self.client.download_file(self.bucket, key, local_path)
        return local_path

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def size(self, key: str) -> int:
        return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]

    def url(self, key: str, expires: int = 3600, filename: str = "") -> str:
        """A URL the browser can fetch directly.

        `filename` forces a download under that name. It exists because the
        HTML `download` attribute is SILENTLY IGNORED once a request redirects
        cross-origin -- so with the download button pointing at this API and
        this API redirecting to R2, "Save as My Clip.mp4" became "open
        clip_1.mp4 in a tab". Signing Content-Disposition into the URL is the
        only way to name the file once the bytes no longer come from us.
        """
        if self.public_base:
            # A bucket published on a custom domain needs no signature, and
            # skipping it means the URL is cacheable by the CDN.
            return f"{self.public_base}/{key}"

        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            safe = filename.replace('"', "").replace("\\", "")
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        return self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires,
        )

    def delete_prefix(self, prefix: str) -> int:
        prefix = prefix.rstrip("/") + "/"
        removed = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            batch = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if not batch:
                continue
            # delete_objects caps at 1000 keys per call; a paginator page is
            # already bounded by that, so one call per page is safe.
            self.client.delete_objects(Bucket=self.bucket,
                                       Delete={"Objects": batch})
            removed += len(batch)
        return removed


def _build():
    """Choose a driver from the environment.

    Defaults to local so a fresh checkout, and every test, runs with no
    configuration at all. Misconfigured R2 raises here at import rather than at
    the first upload -- a job that transcribes, analyses and renders before
    discovering it cannot store the result has wasted real money.

    This runs at import, which is why the module loads .env at the top. Without
    that, importing storage before app.env -- which any script or worker
    entrypoint may do -- read an unset STORAGE_BACKEND and silently chose the
    local disk. The failure is invisible until a second container looks for a
    file the first one "stored" and finds nothing.
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    if backend in ("", "local", "filesystem"):
        return LocalDriver()
    if backend in ("r2", "s3"):
        missing = [name for name in
                   ("R2_BUCKET", "R2_ENDPOINT", "R2_ACCESS_KEY_ID",
                    "R2_SECRET_ACCESS_KEY")
                   if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                f"STORAGE_BACKEND={backend} needs {', '.join(missing)}. "
                f"Set them, or unset STORAGE_BACKEND to use the local disk."
            )
        return R2Driver(
            bucket=os.environ["R2_BUCKET"],
            endpoint=os.environ["R2_ENDPOINT"],
            key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret=os.environ["R2_SECRET_ACCESS_KEY"],
            public_base=os.environ.get("R2_PUBLIC_BASE", ""),
        )
    raise RuntimeError(f"Unknown STORAGE_BACKEND {backend!r}. Use 'local' or 'r2'.")


driver = _build()


def job_key(job_id: str, filename: str) -> str:
    return f"{job_id}/{filename}"


def publish(job_id: str, job_dir: str, filenames) -> int:
    """Copy finished artefacts from the scratch directory into storage.

    Failures are raised, not swallowed: an unpublished clip is a clip the user
    cannot download, and pretending the job succeeded turns that into a silent
    loss discovered days later.
    """
    if driver.name == "local":
        return 0                      # already at its key
    count = 0
    for name in filenames:
        local_path = os.path.join(job_dir, name)
        if os.path.exists(local_path):
            driver.put(job_key(job_id, name), local_path)
            count += 1
    return count


def ensure_local(job_id: str, job_dir: str, filename: str) -> str | None:
    """A local path for `filename`, fetching it back from storage if needed.

    Editing re-cuts from the source, and on a worker that never rendered this
    job -- or one whose container was replaced since -- that source is not on
    disk. Without this, exporting an edit would re-download from YouTube, which
    now frequently refuses.
    """
    local_path = os.path.join(job_dir, filename)
    if os.path.exists(local_path):
        return local_path
    key = job_key(job_id, filename)
    if not driver.exists(key):
        return None
    os.makedirs(job_dir, exist_ok=True)
    return driver.download(key, local_path)
