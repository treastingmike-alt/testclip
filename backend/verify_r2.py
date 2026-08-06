"""Proves the app can really use your Cloudflare R2 bucket.

Run it AFTER putting real credentials in backend/.env:

    cd backend && venv/bin/python verify_r2.py

Unlike try_storage_locally.py -- which talks to a fake server on this machine --
this one uses your actual bucket, so a pass here means the production path
works. It uploads a small test object, signs a URL, fetches that URL over the
public internet exactly as a browser would, checks the download filename
override, then deletes what it made.

It never prints your keys.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import env  # noqa: F401,E402  -- loads backend/.env


REQUIRED = ("R2_BUCKET", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


def main() -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print("Missing from backend/.env:")
        for name in missing:
            print(f"   {name}")
        print("\nCreate them at Cloudflare > R2 > Manage API tokens.")
        return 1

    # Forced on so this exercises the real driver even if .env still says local.
    os.environ["STORAGE_BACKEND"] = "r2"

    bucket = os.environ["R2_BUCKET"]
    endpoint = os.environ["R2_ENDPOINT"]
    print(f"Bucket:   {bucket}")
    print(f"Endpoint: {endpoint}")
    print(f"Key ID:   ...{os.environ['R2_ACCESS_KEY_ID'][-4:]}  (not shown in full)\n")

    import importlib
    from app import storage
    importlib.reload(storage)
    driver = storage.driver
    if driver.name != "r2":
        print(f"Driver is {driver.name!r}, expected 'r2'.")
        return 1

    key = "_klipcut_selftest/hello.mp4"
    tmp = os.path.join(tempfile.mkdtemp(), "hello.mp4")
    with open(tmp, "wb") as f:
        f.write(b"klipcut r2 self test" * 64)
    expected = os.path.getsize(tmp)

    try:
        print("1. Uploading a test object...")
        driver.put(key, tmp)
        print("   uploaded.")

        print("2. Checking it is there...")
        if not driver.exists(key):
            print("   NOT FOUND after upload -- the write silently failed.")
            return 1
        print(f"   present, {driver.size(key)} bytes.")

        print("3. Signing a URL and fetching it over the internet...")
        import requests
        url = driver.url(key, expires=300)
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"   HTTP {resp.status_code} -- a browser could not play this.")
            print(f"   {resp.text[:300]}")
            return 1
        if len(resp.content) != expected:
            print(f"   got {len(resp.content)} bytes, expected {expected}.")
            return 1
        print(f"   HTTP 200, {len(resp.content)} bytes, "
              f"content-type={resp.headers.get('content-type')}")

        print("4. Checking Range requests (the editor scrubs with these)...")
        part = requests.get(url, headers={"Range": "bytes=0-99"}, timeout=30)
        if part.status_code != 206:
            print(f"   HTTP {part.status_code}, expected 206. Seeking will not work.")
            return 1
        print(f"   HTTP 206, content-range={part.headers.get('content-range')}")

        print("5. Checking the download filename override...")
        named = driver.url(key, expires=300, filename="My Clip.mp4")
        head = requests.get(named, timeout=30)
        disposition = head.headers.get("content-disposition", "")
        if "My Clip.mp4" not in disposition:
            print(f"   filename not applied. content-disposition={disposition!r}")
            print("   Downloads would save under the wrong name.")
            return 1
        print(f"   content-disposition={disposition}")

    finally:
        print("\nCleaning up the test object...")
        try:
            removed = driver.delete_prefix("_klipcut_selftest")
            print(f"   removed {removed} object(s).")
        except Exception as exc:
            print(f"   could not clean up: {exc}")
            print("   Delete _klipcut_selftest/ from the bucket by hand.")

    print("\nR2 is working. Start the server with STORAGE_BACKEND=r2 and every "
          "clip it renders will land in this bucket.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
