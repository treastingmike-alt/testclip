"""Fetches source media with yt-dlp.

Two important performance decisions live here:

1. Audio is downloaded on its own, first. Transcription only needs audio, and
   the audio stream is a few MB against a video's hundreds. If the transcript
   yields nothing usable we never pay for the video download at all.

2. The video is capped at 1080p. render.py composes a 1080x1920 frame, so
   anything above that is downscaled and thrown away -- and YouTube's top
   formats are now 4K AV1, which is both enormous (>1GB) and slow to decode.
   Preferring avc1 keeps ffmpeg on its fast, hardware-friendly path.
"""

import json
import os
import re
import subprocess
import threading

# "[download]  46.3% of ..." -- yt-dlp emits one of these per line with --newline
_PERCENT_RE = re.compile(r"\[download\]\s+([\d.]+)%")

# <=1080p, h264 preferred, falling back progressively so odd sources still work
VIDEO_FORMAT = (
    "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/"
    "bestvideo[height<=1080]+bestaudio/"
    "best[height<=1080]/best"
)

AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"

# YouTube increasingly refuses anonymous downloads ("Sign in to confirm you're
# not a bot"), so a signed-in session is needed for reliability.
#
# TWO ways to supply one, and the difference matters for deployment:
#
#   CLIPPER_COOKIES_FILE    a Netscape cookies.txt. Portable, no OS keychain,
#                           works on a headless server. THIS is the deployable
#                           option and is preferred when both are set.
#   CLIPPER_COOKIES_BROWSER read the local browser profile directly. Convenient
#                           on a dev laptop, but it prompts for the macOS
#                           keychain password and cannot work on a server.
#
# Default to a cookies.txt sitting next to the app if present, so the common
# case needs no configuration at all.
_DEFAULT_COOKIE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "cookies.txt"))

_configured = os.environ.get("CLIPPER_COOKIES_FILE", "").strip()
if _configured:
    # Resolve relative to the backend directory, not the process working
    # directory -- uvicorn may be started from anywhere.
    COOKIES_FILE = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", _configured))
elif os.path.exists(_DEFAULT_COOKIE_FILE):
    COOKIES_FILE = _DEFAULT_COOKIE_FILE
else:
    COOKIES_FILE = ""

if COOKIES_FILE and not os.path.exists(COOKIES_FILE):
    print(f"[clipper] WARNING: cookies file not found at {COOKIES_FILE} -- "
          f"YouTube will block many downloads. Regenerate it with: make cookies")
    COOKIES_FILE = ""
COOKIES_BROWSER = os.environ.get("CLIPPER_COOKIES_BROWSER", "").strip()


# A residential proxy, if you have one. This is the only thing that reliably
# fixes "Sign in to confirm you're not a bot" at scale: the block is on the IP,
# not the request. Datacenter ranges -- Railway, Render, AWS, GCP -- are
# pre-flagged, and no combination of headers, clients or cookies changes which
# network the packets came from. Cookies help, but YouTube invalidates them
# faster when it sees them used from a datacenter, so they decay in exactly the
# environment that needs them most.
PROXY = os.environ.get("CLIPPER_PROXY", "").strip()

# YouTube applies the bot challenge per client. When the default web client is
# challenged, another often is not -- so a failure is worth retrying as a
# different client before giving up. Ordered by how much detail they return:
# web carries the richest metadata, the mobile clients are the ones that tend to
# still work from a blocked IP.
#
# This is mitigation, not a fix. Which clients work changes every few weeks, so
# treat it as buying time rather than solving the problem.
PLAYER_CLIENTS = ("default", "android", "ios", "tv")


def _cookie_args() -> list:
    if COOKIES_FILE:
        return ["--cookies", COOKIES_FILE]
    if COOKIES_BROWSER:
        return ["--cookies-from-browser", COOKIES_BROWSER]
    return []


def _network_args() -> list:
    return ["--proxy", PROXY] if PROXY else []


def _client_args(client: str) -> list:
    """Extractor args for one player client, or nothing for yt-dlp's default."""
    if client == "default":
        return []
    return ["--extractor-args", f"youtube:player_client={client}"]


def _is_bot_block(log_text: str) -> bool:
    """YouTube refusing the IP rather than the video being unavailable.

    Worth separating because the two want opposite responses: a blocked IP is
    retryable with a different client and is a configuration problem to report,
    while a private or deleted video will fail identically no matter what.
    """
    markers = ("Sign in to confirm", "confirm you're not a bot",
               "confirm you’re not a bot", "not a bot")
    return any(m in log_text for m in markers)



def cookie_source() -> str:
    """Human description of where the YouTube session comes from."""
    if COOKIES_FILE:
        return f"cookies file ({os.path.basename(COOKIES_FILE)})"
    if COOKIES_BROWSER:
        return f"{COOKIES_BROWSER} browser profile (not deployable)"
    return "none -- anonymous, YouTube will block many videos"


# yt-dlp failure signatures -> what the user should actually be told. The raw
# log is for the server console; a person who pasted a link deserves a sentence.
def _cookie_hint() -> str:
    """Advice that matches how this install is actually configured."""
    if COOKIES_FILE:
        return (" The saved YouTube session has gone stale -- YouTube rotates these "
                "regularly. Refresh it with: ./refresh-cookies.sh")
    if COOKIES_BROWSER:
        return (f" The {COOKIES_BROWSER} session did not satisfy YouTube. Make sure "
                f"you are signed in to YouTube in {COOKIES_BROWSER}.")
    return (" No YouTube session is configured. On this machine run "
            "./refresh-cookies.sh, which lets Clipper use your browser's sign-in.")

# Message templates; the session hint is appended at raise time so it always
# reflects the CURRENT configuration rather than whatever it was at import.
_FAILURES = [
    ("Sign in to confirm your age",
     "This video is age-restricted, and YouTube only serves it to signed-in viewers.@HINT"),
    ("Sign in to confirm you",   # "...you're not a bot" (curly apostrophe varies)
     "YouTube would not serve this video without a signed-in session.@HINT"),
    ("Private video",
     "This video is private, so it can't be downloaded."),
    ("members-only",
     "This video is for channel members only, so it can't be downloaded."),
    ("Video unavailable",
     "YouTube says this video is unavailable -- it may have been removed or the "
     "link may be wrong."),
    ("not available in your country",
     "This video is blocked in your region."),
    ("This live event",
     "This is a live stream that hasn't finished -- clip it after it ends."),
    ("is not a valid URL",
     "That doesn't look like a valid video link -- check the URL and try again."),
]


def _friendly_error(log_text: str) -> str:
    for signature, message in _FAILURES:
        if signature in log_text:
            return message.replace("@HINT", _cookie_hint())
    return None


def _public_failure(log_text: str) -> str:
    """A useful next step for creators; detailed provider output stays in logs."""
    friendly = _friendly_error(log_text)
    if friendly:
        return "We couldn't access this video. Check that it is public, or upload the video file instead."
    return "This link is busy or unavailable right now. Try again shortly, or upload the video file instead."


def _is_auth_failure(log_text: str) -> bool:
    return "Sign in to confirm" in log_text


def refresh_cookies_from_browser() -> bool:
    """Re-exports the YouTube session from the local browser.

    YouTube rotates session tokens, so an exported cookies.txt goes stale --
    sometimes within minutes. When a browser is available (a dev machine) we can
    silently re-export and retry instead of failing the user's job.
    """
    if not COOKIES_BROWSER or not COOKIES_FILE:
        return False
    try:
        out = subprocess.run(
            ["yt-dlp", "--cookies-from-browser", COOKIES_BROWSER,
             "--cookies", COOKIES_FILE, "--skip-download", "--no-playlist",
             "--quiet", "--no-warnings",
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode == 0 and os.path.exists(COOKIES_FILE):
            os.chmod(COOKIES_FILE, 0o600)
            print("[clipper] refreshed the YouTube session from "
                  f"{COOKIES_BROWSER}")
            return True
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[clipper] could not refresh cookies: {e}")
    return False


# Nothing here may block forever. A keychain prompt on a headless box, or a
# stalled CDN, would otherwise leave a job sitting at 0% with no explanation.
STALL_TIMEOUT = 180


def _run_with_progress(cmd: list, on_progress=None, _retrying: bool = False) -> None:
    """Runs yt-dlp, forwarding download percentage to on_progress as it streams.

    Aborts if yt-dlp produces no output at all for STALL_TIMEOUT seconds.
    """
    
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    tail = []
    stalled = []

    def watchdog():
        # Reading a line at a time gives no timeout hook, so a separate timer
        # kills the process if nothing has been printed in a long while.
        if proc.poll() is None:
            stalled.append(True)
            proc.kill()

    timer = threading.Timer(STALL_TIMEOUT, watchdog)
    timer.start()
    try:
        for line in proc.stdout:
            timer.cancel()
            timer = threading.Timer(STALL_TIMEOUT, watchdog)
            timer.start()
            tail.append(line)
            del tail[:-40]                  # keep only the last lines for error context
            if on_progress:
                match = _PERCENT_RE.search(line)
                if match:
                    on_progress(float(match.group(1)))
    finally:
        timer.cancel()

    if stalled:
        raise RuntimeError(
            f"The download stalled with no progress for {STALL_TIMEOUT // 60} minutes "
            f"and was stopped. If this machine was waiting on a keychain or "
            f"password prompt, switch to a cookies file "
            f"(CLIPPER_COOKIES_FILE) instead of reading the browser profile."
        )

    if proc.wait() != 0:
        log = "".join(tail)

        # A stale session is the single most common failure here, and it is
        # recoverable without bothering anyone -- refresh and try once more.
        if _is_auth_failure(log) and not _retrying and refresh_cookies_from_browser():
            return _run_with_progress(cmd, on_progress, _retrying=True)

        friendly = _friendly_error(log)
        if friendly:
            # Full log to the server console for debugging; only the sentence
            # travels to the user.
            print("[clipper] yt-dlp failure:\n" + log)
            raise RuntimeError(_public_failure(log))
        print("[clipper] downloader failure:\n" + log)
        raise RuntimeError(_public_failure(log))


def estimate_video_bytes(url: str) -> int:
    """Approximate download size for the video formats we actually select.

    Used to fail fast with a readable message when the disk is too full, rather
    than grinding through a multi-minute download that dies on ENOSPC.
    Returns 0 if the size cannot be determined -- absence of a number is not a
    reason to block the run.
    """
    try:
        out = subprocess.run(
            ["yt-dlp", "-f", VIDEO_FORMAT, "--no-playlist", "-J", "--no-warnings",
             *_cookie_args(), url],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            return 0
        info = json.loads(out.stdout)
        formats = info.get("requested_formats") or [info]
        return sum(
            (f.get("filesize") or f.get("filesize_approx") or 0) for f in formats
        )
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError, OSError):
        return 0


def inspect_url(url: str) -> dict:
    """Read public video metadata without downloading its media streams."""
    log = ""
    proc = None
    # Try each client in turn: a bot challenge is per-client, so the one that
    # fails is often not the one that would have worked.
    for client in PLAYER_CLIENTS:
        try:
            proc = subprocess.run(
                ["yt-dlp", "--dump-single-json", "--skip-download", "--no-playlist",
                 "--no-warnings", *_cookie_args(), *_network_args(),
                 *_client_args(client), url],
                capture_output=True, text=True, timeout=45,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(
                "Could not check that video link. Try again or upload the file."
            ) from exc

        if proc.returncode == 0:
            if client != "default":
                print(f"[clipper] metadata needed the {client} client "
                      f"(the default was blocked)")
            break

        log = proc.stderr + proc.stdout
        if not _is_bot_block(log):
            break        # private, deleted or unsupported -- another client won't help

    if proc is None or proc.returncode != 0:
        print("[clipper] source preview failure:\n" + log)
        if _is_bot_block(log):
            # Say what is actually wrong. "Try a public video link" sent users
            # hunting for a problem with their video when the video was fine and
            # the server's IP was the thing being refused.
            print("[clipper] YouTube refused this server's IP. Set CLIPPER_PROXY "
                  "to a residential proxy, or CLIPPER_COOKIES_FILE to a fresh "
                  "cookies.txt. See DOWNLOADS.md.")
        raise RuntimeError(_public_failure(log))
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The video site did not return usable preview data.") from exc

    thumbs = info.get("thumbnails") or []
    thumb = info.get("thumbnail") or next(
        (item.get("url") for item in reversed(thumbs) if item.get("url")), None)
    return {
        "title": info.get("title") or "Untitled video",
        "creator": info.get("uploader") or info.get("channel") or info.get("extractor_key"),
        "duration": info.get("duration"),
        "thumbnail": thumb,
        "platform": info.get("extractor_key"),
    }


def download_audio(url: str, out_path: str, on_progress=None) -> str:
    """Grabs the audio stream only and transcodes it to 16kHz mono mp3 for Deepgram."""
    raw_path = out_path + ".src"
    _run_with_progress(
        [
            "yt-dlp",
            "-f", AUDIO_FORMAT,
            "--newline",
            "--no-playlist",
            "--force-overwrites",
            *_cookie_args(),
            "-o", raw_path,
            url,
        ],
        on_progress,
    )
  
    

    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_path, "-vn", "-ac", "1", "-ar", "16000",
         "-acodec", "libmp3lame", out_path],
        check=True,
        capture_output=True,
    )
    return out_path


def download_video(url: str, out_path: str, on_progress=None) -> str:
    _run_with_progress(
        [
            "yt-dlp",
            "-f", VIDEO_FORMAT,
            "--merge-output-format", "mp4",
            "--newline",
            "--no-playlist",
            "--force-overwrites",
            *_cookie_args(),
            "-o", out_path,
            url,
        ],
        on_progress,
    )
    return out_path


def extract_audio(video_path: str, out_path: str) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         "-acodec", "libmp3lame", out_path],
        check=True,
        capture_output=True,
    )
    return out_path
