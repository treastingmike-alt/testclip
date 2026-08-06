"""Exercises the R2 storage path with a FAKE S3 server -- no real account,
no real credentials, no cost. Run it any time you want to sanity-check the
storage code before touching Railway.

    cd backend && venv/bin/python try_storage_locally.py

It starts a local server that speaks the S3 API, points app/storage.py at it
exactly the way STORAGE_BACKEND=r2 would in production, uploads a fake clip,
downloads it back, and prints what happened at each step.
"""

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # quiet the fake server's own logs

from moto.server import ThreadedMotoServer  # noqa: E402


def main():
    print("Starting a fake S3 server on your machine (port 5199)...")
    server = ThreadedMotoServer(port=5199, verbose=False)
    server.start()
    endpoint = "http://127.0.0.1:5199"

    import boto3
    boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id="test",
        aws_secret_access_key="test", region_name="us-east-1",
    ).create_bucket(Bucket="klipcut-test")

    # This is exactly what you will set on Railway, pointed at the fake server
    # instead of the real one.
    os.environ.update(
        STORAGE_BACKEND="r2",
        R2_BUCKET="klipcut-test",
        R2_ENDPOINT=endpoint,
        R2_ACCESS_KEY_ID="test",
        R2_SECRET_ACCESS_KEY="test",
    )

    from app import storage
    print(f"\nDriver selected: {storage.driver.name}  (should say 'r2')\n")

    # Pretend a job just rendered a clip.
    job_dir = tempfile.mkdtemp()
    clip_path = os.path.join(job_dir, "clip_1.mp4")
    with open(clip_path, "wb") as f:
        f.write(b"pretend this is video bytes" * 1000)
    print(f"Made a fake clip: {clip_path} ({os.path.getsize(clip_path)} bytes)")

    storage.driver.put("test-job-1/clip_1.mp4", clip_path)
    print("Uploaded it to the fake bucket.")

    print("Exists check:", storage.driver.exists("test-job-1/clip_1.mp4"))

    url = storage.driver.url("test-job-1/clip_1.mp4", expires=300)
    print(f"\nSigned URL (this is what a browser would be redirected to):")
    print(f"  {url[:90]}...")

    import requests
    resp = requests.get(url)
    print(f"\nFetching that URL directly: {resp.status_code}, "
          f"{len(resp.content)} bytes back, "
          f"content-type={resp.headers.get('content-type')}")

    print("\nDeleting the job (what retention does):")
    removed = storage.driver.delete_prefix("test-job-1")
    print(f"  removed {removed} object(s). Still exists?",
          storage.driver.exists("test-job-1/clip_1.mp4"))

    server.stop()
    print("\nDone. If everything above looks right, the storage code itself "
          "is fine -- what's left is pointing it at your REAL R2 bucket.")


if __name__ == "__main__":
    main()
