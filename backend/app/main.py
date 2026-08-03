"""FastAPI backend for the clipping tool.

Run with:
    uvicorn app.main:app --reload --port 8000

Endpoints:
    POST /jobs            submit a public video URL, returns a job id
    POST /jobs/upload     upload a local video and return a job id
    GET  /jobs/{job_id}    poll status + get clip results once done
    GET  /clips/{job_id}/{filename}   download a rendered clip
"""

from app import env  # noqa: F401  -- loads .env BEFORE anything reads keys
import json
import os
import uuid
import subprocess
import random
import threading
import traceback
import shutil
from urllib.parse import urlparse

from fastapi import (BackgroundTasks, Depends, FastAPI, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import auth, billing, editing, workspace
from app.db import SessionLocal, get_session, init_db
from app.jobs import create_job, get_job, get_job_transcript, list_jobs, update_job
from app.models import Clip, Job, User
from app.pipeline import (analyzer, caption_presets, censor, energy, downloader,
                          pacing, reframe, render, scoring, subtitles,
                          transcriber, voiceover)

app = FastAPI(title="Clipper API")

# Allow a local React dev server to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STORAGE_DIR = os.path.join(os.path.dirname(__file__), "..", "storage")
# Drop royalty-safe gameplay loops (mp4/mov) in here to enable the split layout.
GAMEPLAY_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "gameplay")

# Uploads are streamed to disk, never read into RAM. Keep this configurable for
# a production host with object storage or a stricter reverse-proxy limit.
MAX_UPLOAD_BYTES = int(os.environ.get("CLIPPER_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024))
VIDEO_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


# A template is a complete look: how the frame is filled AND how captions are
# drawn. Keeping them paired here means the UI sends one id and no invalid
# combination (e.g. a middle-of-frame caption over a split screen) can be built.
TEMPLATES = {
    "classic":  {"frame": "blur",     "caption_style": "classic",
                 "label": "Classic"},
    "fitvideo": {"frame": "fit",      "caption_style": "fitbox",
                 "label": "Fit video"},
    "tech":     {"frame": "fill",     "caption_style": "tech",
                 "label": "Tech"},
    "business": {"frame": "fill",     "caption_style": "business",
                 "label": "Business"},
    "gameplay": {"frame": "gameplay", "caption_style": "gameplay",
                 "label": "Gameplay"},
    # Podcasts are a two-shot problem: the zoom every other template uses to
    # fill the frame is exactly what crops the second person out of it.
    "podcast":  {"frame": "podcast",  "caption_style": "podcast_clean",
                 "label": "Podcast"},
}


@app.get("/platform-logos/{platform}.png")
def serve_platform_logo(platform: str):
    """The same brand mark the renderer composites, so the editor preview and
    the exported frame cannot drift apart."""
    from app.pipeline.platform_logos import brand_logo
    path = brand_logo(platform)
    if not path:
        raise HTTPException(status_code=404, detail="Unknown platform")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/fonts/{font_id}.ttf")
def serve_font(font_id: str):
    """Serves a caption font so the browser previews it in the SAME file the
    renderer burns in. Loading a lookalike from a CDN instead is how a preview
    drifts from the export."""
    entry = subtitles.font_entry(font_id)
    if not entry or not entry.get("file"):
        raise HTTPException(status_code=404, detail="Unknown font")
    path = os.path.join(subtitles.FONTS_DIR, entry["file"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Font file missing")
    return FileResponse(path, media_type="font/ttf",
                        headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/caption-options")
def caption_options():
    """Fonts, animations and speed range -- read from the renderer itself.

    The font list is whatever is installed in assets/fonts right now, so adding
    a .ttf shows up in the editor without a code change on either side.
    """
    return {
        "fonts": [
            {"id": k, "label": v["label"], "family": v["family"],
             "devanagari": v["devanagari"], "emoji": v.get("emoji", False)}
            for k, v in subtitles.FONTS.items()
        ],
        # Whether an emoji typed into a caption or title will actually burn in.
        # It reaches the subtitle file either way; libass silently draws nothing
        # unless a face on the fallback path has the glyph. The editor showed
        # emoji in the preview regardless, which is how "it was there while
        # editing and gone after export" happened.
        "emoji_supported": subtitles.emoji_font() is not None,
        "animations": [{"id": k, **v} for k, v in subtitles.ANIMATIONS.items()],
        "speed": {"min": render.MIN_SPEED, "max": render.MAX_SPEED},
        # Title looks are served rather than duplicated in the editor, for the
        # same reason as fonts: a look added here must not need a matching edit
        # on the frontend to become selectable.
        "title_styles": [
            # "Off" is a first-class choice, not an absence. Burning a title in
            # is irreversible, and plenty of people want the cut and the captions
            # while writing their own hook in the platform's composer.
            {"id": subtitles.TITLE_LOOK_OFF, "text": None, "edge": None,
             "boxed": False},
        ] + [
            {"id": k,
             # Enough for the editor to draw a faithful swatch without knowing
             # anything about ASS colour notation.
             "text": _ass_colour_to_css(v["colour"]),
             "edge": _ass_colour_to_css(v["edge"]),
             "boxed": v["border_style"] == 3}
            for k, v in subtitles.TITLE_LOOKS.items()
        ],
        "default_title_style": subtitles.DEFAULT_TITLE_LOOK,
        # The caption presets, in browser terms. Served rather than mirrored so
        # the picker's live previews are generated from the same definitions the
        # renderer burns in -- the editor used to keep its own copy, and the two
        # drifted on both colours and sizes.
        **caption_presets.web_presets(),
    }


def _ass_colour_to_css(value: str) -> str:
    """&HAABBGGRR (ASS, alpha first and BGR order) -> #RRGGBB (CSS)."""
    digits = value.lstrip("&H").lstrip("&h")
    if len(digits) < 6:
        return "#000000"
    bb, gg, rr = digits[-6:-4], digits[-4:-2], digits[-2:]
    return f"#{rr}{gg}{bb}"


@app.get("/templates")
def list_templates():
    """Lets the UI render the picker from one source of truth."""
    return [{"id": k, **v} for k, v in TEMPLATES.items()]


def _gameplay_loops() -> list:
    """All installed gameplay assets, shuffled. Raises with setup instructions.

    Returns the whole list rather than one file so a multi-clip job can give each
    clip different footage -- shipping five clips over identical gameplay reads
    as a template, not an edit.
    """
    try:
        files = [f for f in os.listdir(GAMEPLAY_DIR)
                 if f.lower().endswith((".mp4", ".mov", ".webm"))]
    except FileNotFoundError:
        files = []
    if not files:
        raise HTTPException(
            status_code=400,
            detail="No gameplay footage installed. Put an .mp4 loop (e.g. parkour or "
                   f"driving gameplay you have rights to) in {os.path.abspath(GAMEPLAY_DIR)} "
                   "and try again.",
        )
    random.shuffle(files)
    return [os.path.join(GAMEPLAY_DIR, f) for f in files]


class JobRequest(BaseModel):
    url: str
    n_clips: int = 3
    mode: str = "original"          # "original" keeps real audio, "voiceover" replaces it
    voice: str = "onyx"
    language: str = "English"
    burn_subtitles: bool = True
    template: str = "classic"       # key into TEMPLATES
    ratio: str = "9:16"             # key into render.RATIOS
    length_pref: str = "any"        # key into analyzer.LENGTH_PREFS
    intent: str = ""                # free-text steer, e.g. "when he talks about pricing"
    tighten_pauses: bool = True     # cut dead air so the clip does not feel merely trimmed
    auto_censor: bool = True        # mute profanity and star it in captions
    # Transcribe speech that switches language mid-sentence with the model that
    # can follow it. Off by default and gated on the plan -- see billing.allows.
    multilingual: bool = False


class SourcePreviewRequest(BaseModel):
    url: str


class Credentials(BaseModel):
    email: str
    password: str


# States that only exist while a worker thread is alive. If the process died
# (a crash, a Ctrl-C, or uvicorn --reload restarting on a file change) the
# thread went with it, and nothing will ever move these jobs again -- they sit
# at "0%" forever, which is exactly what a stuck spinner looks like.
RUNNING_STATES = ("queued", "downloading", "transcribing", "analyzing", "rendering")


def _reload_active() -> bool:
    """True when uvicorn was started with --reload (best effort)."""
    try:
        import psutil  # optional
        return "--reload" in " ".join(psutil.Process(os.getppid()).cmdline())
    except Exception:
        try:
            out = subprocess.run(["ps", "-o", "command=", "-p", str(os.getppid())],
                                 capture_output=True, text=True, timeout=5)
            return "--reload" in out.stdout
        except Exception:
            return False


def _recover_orphaned_jobs():
    """Fails jobs left mid-flight by a previous process. Returns how many."""
    from app.models import Job
    with SessionLocal() as session:
        stale = session.query(Job).filter(Job.status.in_(RUNNING_STATES)).all()
        for job in stale:
            job.status = "failed"
            job.error = ("This run was interrupted when the server restarted, so it "
                         "could not finish. Nothing was charged -- run the video again.")
        if stale:
            session.commit()
        return len(stale)


@app.on_event("startup")
def on_startup():
    init_db()

    orphaned = _recover_orphaned_jobs()
    if orphaned:
        print(f"[clipper] recovered {orphaned} job(s) interrupted by a restart")

    # Clipping runs in a background thread, and --reload kills the process on
    # every file save. Editing any backend file mid-job therefore destroys that
    # job silently -- it just stops at whatever percentage it had reached.
    if os.environ.get("CLIPPER_RELOAD_WARNED") != "1" and _reload_active():
        os.environ["CLIPPER_RELOAD_WARNED"] = "1"
        print("[clipper] NOTE: running with --reload. Saving any backend file "
              "restarts the server and kills jobs that are still running. "
              "Use plain `uvicorn app.main:app` while processing videos.")
    for key, what in (("DEEPGRAM_API_KEY", "transcription"),
                      ("OPENAI_API_KEY", "clip selection")):
        if not os.environ.get(key):
            print(f"[clipper] WARNING: {key} is not set -- {what} will fail. "
                  f"Add it to backend/.env")

    from app.pipeline import downloader as _dl
    print(f"[clipper] model: {os.environ.get('CLIPPER_MODEL', 'gpt-4o')} | "
          f"youtube session: {_dl.cookie_source()}")
    removed = workspace.purge_old_jobs(STORAGE_DIR)
    if removed:
        # The window is configurable (CLIPPER_RETENTION_HOURS) and this line used
        # to say a hardcoded "48h" regardless -- so the log confidently reported
        # the wrong number for the one event that destroys a user's clips.
        print(f"[clipper] removed {removed} job folder(s) older than "
              f"{workspace.DEFAULT_MAX_AGE_HOURS}h")


@app.post("/auth/register")
def register(body: Credentials, session: Session = Depends(get_session)):
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < auth.MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Enter a valid email and a password of at least "
                   f"{auth.MIN_PASSWORD_LENGTH} characters.",
        )
    if session.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="That email is already registered.")

    user = User(email=email, password_hash=auth.hash_password(body.password))
    session.add(user)
    session.commit()
    return {"token": auth.create_token(user.id),
            "user": {"id": user.id, "email": user.email, "credits": user.credits}}


@app.post("/auth/login")
def login(body: Credentials, session: Session = Depends(get_session)):
    user = session.query(User).filter(User.email == body.email.strip().lower()).first()
    # Same message either way -- distinguishing them tells an attacker which
    # emails are registered.
    if not user or not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    return {"token": auth.create_token(user.id),
            "user": {"id": user.id, "email": user.email, "credits": user.credits}}


@app.get("/billing/plans")
def plans():
    """Public pricing, so the page renders from one source of truth."""
    return {
        "plans": billing.PLANS,
        "topups": billing.TOPUPS,
        "credits_per_clip": billing.CREDITS_PER_CLIP,
        "max_source_minutes": billing.MAX_SOURCE_MINUTES,
    }


@app.get("/billing/me")
def billing_me(user: User = Depends(auth.current_user_required)):
    return {
        "credits": billing.balance(user.id),
        "plan": user.plan,
        # What this plan unlocks, so the UI can show a locked control with the
        # reason rather than hiding it -- a feature nobody can see is a feature
        # nobody upgrades for. Sent as a list so adding one needs no client
        # change beyond using it.
        "entitlements": sorted(billing.entitlements_for(user.plan, user.email)),
        "is_admin": billing.is_admin(user.email),
        "history": billing.history(user.id),
    }


@app.post("/billing/estimate")
def estimate(body: dict):
    """Credits a run would cost, for showing a price before committing.

    Takes a clip count now rather than a duration. `duration_seconds` is still
    accepted and ignored so an older client asking the old question gets a
    correct answer for the default clip count instead of an error.
    """
    n_clips = int(body.get("n_clips") or 1)
    return {"credits": billing.credits_for_clips(n_clips),
            "credits_per_clip": billing.CREDITS_PER_CLIP}


class CheckoutRequest(BaseModel):
    plan_id: Optional[str] = None
    credits: Optional[int] = None
    interval: str = "monthly"        # "monthly" | "yearly"


@app.post("/billing/checkout")
def checkout(body: CheckoutRequest, user: User = Depends(auth.current_user_required)):
    """Records purchase intent and returns what a provider needs.

    No provider is connected yet, so this deliberately does NOT grant credits --
    doing so would be giving away paid product. When Polar/Stripe is added, its
    webhook calls billing.grant_credits() on a confirmed payment and this returns
    a real redirect URL.
    """
    if body.plan_id:
        plan = billing.get_plan(body.plan_id)
        if not plan:
            raise HTTPException(status_code=400, detail="Unknown plan.")

        # Price comes from the tier, never from the request -- otherwise a client
        # could post any amount it liked and name its own price.
        tier = billing.get_tier(body.plan_id, body.credits)
        if not tier:
            raise HTTPException(
                status_code=400,
                detail=f"{plan['name']} is not offered at {body.credits} credits.",
            )
        price = tier["yearly_usd"] if body.interval == "yearly" else tier["monthly_usd"]
        return {
            "status": "provider_not_configured",
            "detail": "Connect a payment provider to complete checkout.",
            "line_item": {"kind": "plan", "plan_id": plan["id"], "name": plan["name"],
                          "interval": body.interval, "usd": price,
                          "credits": tier["credits"]},
        }

    topup = next((t for t in billing.TOPUPS if t["credits"] == body.credits), None)
    if not topup:
        raise HTTPException(status_code=400, detail="Unknown credit pack.")

    return {
        "status": "provider_not_configured",
        "detail": "Connect a payment provider to complete checkout.",
        "line_item": {"kind": "topup", "credits": topup["credits"], "usd": topup["usd"]},
    }


@app.get("/auth/me")
def me(user: User = Depends(auth.current_user_required)):
    # `entitlements` rides along here rather than only on /billing/me because
    # this is what the app loads on boot, and the clip-creation form needs to
    # know what the plan unlocks before it can render its controls correctly.
    return {"id": user.id, "email": user.email, "credits": user.credits,
            "plan": user.plan,
            "entitlements": sorted(billing.entitlements_for(user.plan, user.email)),
            # So the UI can say WHY a paid control is unlocked on an account
            # whose plan is free -- otherwise testing the paid experience looks
            # identical to a billing bug.
            "is_admin": billing.is_admin(user.email)}


@app.get("/jobs")
def my_jobs(user: User = Depends(auth.current_user_required)):
    return list_jobs(user.id)


class CaptionLine(BaseModel):
    start: float
    end: float
    text: str
    # Whether the user actually rewrote this line. Only rewritten text loses its
    # per-word timings; untouched lines keep the real ones. Defaults True so
    # recipes saved before this field existed behave exactly as they did.
    edited: bool = True


class ClipEdit(BaseModel):
    # Optional[...] rather than `= None` alone: under Pydantic v2 a plain
    # `str = None` field REJECTS an explicit JSON null, and clients send nulls.
    start: float
    end: float
    caption_style: Optional[str] = None       # override the template's caption look
    caption_lines: Optional[list[CaptionLine]] = None   # rewritten subtitle text
    caption_font: Optional[str] = None        # id from subtitles.FONTS
    translate_to: Optional[str] = None        # language code from translation.LANGUAGES


@app.get("/jobs/{job_id}/reframe/{index}")
def clip_reframe(job_id: str, index: int, start: float = None, end: float = None):
    """Where the faces are, so the editor can PREVIEW the podcast composition.

    The editor draws its preview from the source proxy and composes the frame
    itself. It had no way to know about face-aware cropping, so it drew the old
    letterbox -- showing the user a layout their clip was not in and had never
    been in.

    Cached on disk per clip: detection takes several seconds, and reopening an
    editor should not pay that again. Trimming passes explicit bounds and gets a
    fresh plan for the new range, since that is a genuinely different question.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job.")
    tpl = TEMPLATES.get((job.get("options") or {}).get("template", "classic"),
                        TEMPLATES["classic"])
    if tpl["frame"] != "podcast":
        return {"plan": None}          # every other frame is fixed geometry

    clips = job.get("clips") or []
    if index < 0 or index >= len(clips):
        raise HTTPException(status_code=404, detail="No such clip.")
    clip = clips[index]
    s = clip["start"] if start is None else start
    e = clip["end"] if end is None else end

    job_dir = os.path.join(STORAGE_DIR, job_id)
    cache = os.path.join(job_dir, f"reframe_{index}_{s:.1f}_{e:.1f}.json")
    if os.path.exists(cache):
        with open(cache) as f:
            return {"plan": json.load(f)}

    source = os.path.join(job_dir, "source.mp4")
    if not os.path.exists(source):
        return {"plan": None}
    ratio = (job.get("options") or {}).get("ratio", "9:16")
    out_w, out_h = render.RATIOS.get(ratio, render.RATIOS["9:16"])
    try:
        plan = reframe.plan(source, s, e, out_w, out_h)
    except Exception as exc:
        print(f"[clipper] editor reframe preview failed: {exc}")
        return {"plan": None}
    if plan:
        with open(cache, "w") as f:
            json.dump(plan, f)
    return {"plan": plan}


@app.get("/jobs/{job_id}/transcript")
def job_transcript(job_id: str):
    """Utterances with word timings, shaped for the editor timeline."""
    transcript = get_job_transcript(job_id)
    if transcript is None:
        raise HTTPException(
            status_code=404,
            detail="No transcript stored for this job. Jobs created before editing "
                   "existed cannot be edited -- re-run the video to enable it.",
        )
    utterances = transcriber.get_utterances(transcript)
    return {
        "duration": utterances[-1]["end"] if utterances else 0,
        # Word timings travel too: the editor reproduces the renderer's cue
        # chunking from them, so the preview breaks captions in exactly the same
        # places the exported video will. Showing whole sentences there was a
        # preview that lied.
        "cue_rules": {
            "max_words": subtitles.MAX_WORDS_PER_CUE,
            "max_seconds": subtitles.MAX_CUE_SECONDS,
        },
        "utterances": [
            {
                "start": u["start"], "end": u["end"], "text": u["transcript"],
                "words": [
                    {"t": w["start"], "e": w["end"],
                     "w": w.get("punctuated_word") or w.get("word", "")}
                    for w in u.get("words", [])
                ],
            }
            for u in utterances
        ],
    }


@app.get("/jobs/{job_id}/gameplay")
def job_gameplay(job_id: str):
    """The gameplay loop this job renders with, so the editor can show the
    split-screen composition instead of the bare source."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    tpl = TEMPLATES.get((job.get("options") or {}).get("template", "classic"))
    if not tpl or tpl["frame"] != "gameplay":
        raise HTTPException(status_code=404, detail="This job is not a split-screen job")

    loops = _gameplay_loops()
    if not loops:
        raise HTTPException(status_code=404, detail="No gameplay assets available")
    # Same round-robin the renderer uses, so the preview shows THIS clip's loop.
    return FileResponse(loops[0], media_type="video/mp4")


@app.api_route("/jobs/{job_id}/preview", methods=["GET", "HEAD"])
def job_preview(job_id: str, request: Request, clip: int = None):
    """Streams the editor proxy with HTTP range support.

    Range support is not optional here -- without it a browser cannot seek, and
    the whole point of the proxy is scrubbing.

    `clip` selects that clip's own windowed proxy, which is what current jobs
    build. Jobs made before windowing fall back to the whole-source preview.mp4,
    so opening an old job still works; its offset is simply zero.

    HEAD is declared explicitly. FastAPI, unlike bare Starlette, does NOT add it
    alongside GET, so the editor's "is the proxy ready yet?" poll was getting 405
    forever and the loading veil never lifted -- over a video that was in fact
    playing underneath it.
    """
    job_dir = os.path.join(STORAGE_DIR, job_id)
    path = os.path.join(job_dir, "preview.mp4")
    if clip is not None:
        windowed = os.path.join(job_dir, f"preview_{clip}.mp4")
        if os.path.exists(windowed):
            path = windowed
    if not os.path.exists(path):
        raise HTTPException(
            status_code=404,
            detail="No editor preview for this job. Jobs made before live editing "
                   "existed do not have one -- re-run the video.",
        )

    size = os.path.getsize(path)

    if request.method == "HEAD":
        return Response(status_code=200, media_type="video/mp4",
                        headers={"Accept-Ranges": "bytes",
                                 "Content-Length": str(size)})
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type="video/mp4",
                            headers={"Accept-Ranges": "bytes"})

    try:
        units, _, span = range_header.partition("=")
        start_s, _, end_s = span.partition("-")
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        raise HTTPException(status_code=416, detail="Malformed Range header.")
    start = max(0, min(start, size - 1))
    end = max(start, min(end, size - 1))

    def chunks():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(65536, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        chunks(), status_code=206, media_type="video/mp4",
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
        },
    )


class Overlay(BaseModel):
    platform: Optional[str] = None          # instagram/x/tiktok/... draws the mark
    t_start: Optional[float] = None         # seconds from clip start; None = from 0
    t_end: Optional[float] = None           # None = until the clip ends
    """A logo or piece of text burned in at export.

    x/y/size are fractions of the frame (see pipeline/overlays.py) so one recipe
    survives a ratio change. `file` names an uploaded image inside the job dir --
    never a path, so a recipe cannot be used to read arbitrary files.
    """
    type: str = "text"
    text: Optional[str] = None
    file: Optional[str] = None
    x: float = 0.5
    y: float = 0.9
    size: float = 0.045
    color: Optional[str] = "#ffffff"
    font: Optional[str] = None
    plate: Optional[str] = None
    opacity: float = 1.0


class ClipRecipe(BaseModel):
    """A clip described rather than rendered."""
    start: float
    end: float
    ratio: Optional[str] = None
    caption_style: Optional[str] = None
    caption_font: Optional[str] = None
    speed: Optional[float] = None
    translate_to: Optional[str] = None
    lines: Optional[list[CaptionLine]] = None
    overlays: Optional[list[Overlay]] = None
    title: Optional[str] = None             # burned into the video and used as filename
    caption_size: Optional[int] = None      # px on the 1080x1920 reference canvas
    caption_pos: Optional[float] = None     # 0..1 down the frame, centre of the block
    caption_anim: Optional[str] = None      # entrance animation preset
    speed: Optional[float] = None           # 0.5 - 3.0 playback speed
    speed_pitched: Optional[bool] = None    # True = pitch rides with speed (meme)
    background: Optional[str] = None        # bar colour for the fit/pad frame
    # The title is its own design decision, not a second caption. Look and
    # typeface move independently: the same white plate reads very differently
    # in Anton than in Merriweather.
    title_style: Optional[str] = None       # key into subtitles.TITLE_LOOKS
    title_font: Optional[str] = None        # font id, independent of captions


@app.put("/jobs/{job_id}/clips/{index}/edit")
def save_clip_edit(job_id: str, index: int, body: ClipRecipe):
    """Stores the recipe. Costs nothing -- no render, no API calls.

    The rendered file is now stale, which `rendered: false` records so the UI
    can offer an export.
    """
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        clip = (session.query(Clip)
                .filter(Clip.job_id == job_id, Clip.index == index).first())
        if not clip:
            raise HTTPException(status_code=404, detail="Clip not found")

        recipe = body.dict(exclude_none=True)
        if body.lines is not None:
            recipe["lines"] = [l.dict() for l in body.lines]
        if body.overlays is not None:
            recipe["overlays"] = [o.dict() for o in body.overlays]

        clip.edit = recipe
        if body.title is not None and body.title.strip():
            clip.title = body.title.strip()[:120]
        # Do not mutate the baked clip bounds until export. The per-clip proxy
        # was encoded around those baked bounds, and changing them here makes a
        # reopened editor calculate the wrong proxy offset for the same file.
        # The recipe carries the draft trim; export commits it.
        clip.duration = editing.tightened_duration(
            job.transcript if job else None,
            body.start,
            body.end,
            recipe.get("lines"),
            ((job.options or {}) if job else {}).get("tighten_pauses", True),
            body.speed,
        )
        clip.rendered = False
        session.commit()
        return clip.to_dict()


# Only formats ffmpeg can actually composite, and small enough that a logo
# cannot be used to fill the disk.
LOGO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_LOGO_BYTES = 4 * 1024 * 1024


@app.post("/jobs/{job_id}/overlays/upload")
async def upload_overlay_image(job_id: str, file: UploadFile = File(...)):
    """Stores a logo for this job and returns the name to put in a recipe."""
    if file.content_type not in LOGO_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Logos must be PNG, JPEG or WebP. PNG keeps transparency.",
        )
    data = await file.read()
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail="Logo must be under 4 MB.")
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")

    out_dir = os.path.join(STORAGE_DIR, job_id, "overlays")
    os.makedirs(out_dir, exist_ok=True)
    name = f"logo_{uuid.uuid4().hex[:10]}{LOGO_TYPES[file.content_type]}"
    with open(os.path.join(out_dir, name), "wb") as f:
        f.write(data)
    return {"file": name, "url": f"/jobs/{job_id}/overlays/{name}"}


@app.get("/jobs/{job_id}/overlays/{name}")
def get_overlay_image(job_id: str, name: str):
    """Serves an uploaded logo so the editor can preview it."""
    path = os.path.join(STORAGE_DIR, job_id, "overlays", os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Overlay image not found")
    return FileResponse(path)


@app.post("/jobs/{job_id}/clips/{index}/export")
def export_clip(job_id: str, index: int):
    """Renders the stored recipe to a real MP4. The only expensive operation."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    with SessionLocal() as session:
        clip = (session.query(Clip)
                .filter(Clip.job_id == job_id, Clip.index == index).first())
        if not clip:
            raise HTTPException(status_code=404, detail="Clip not found")
        recipe = dict(clip.edit or {})
        start = recipe.get("start", clip.start)
        end = recipe.get("end", clip.end)

    # Resolve uploaded image names to paths, refusing anything that tries to
    # escape the job directory.
    job_dir = os.path.join(STORAGE_DIR, job_id)
    resolved = []
    for ov in (recipe.get("overlays") or []):
        ov = dict(ov)
        if ov.get("type") == "image" and ov.get("file"):
            name = os.path.basename(ov["file"])
            path = os.path.join(job_dir, "overlays", name)
            if not os.path.exists(path):
                continue
            ov["path"] = path
        resolved.append(ov)

    tpl = TEMPLATES.get((job.get("options") or {}).get("template", "classic"))
    loops = _gameplay_loops() if tpl and tpl["frame"] == "gameplay" else []
    try:
        result = editing.rerender_clip(
            job_id, index, start, end, STORAGE_DIR, TEMPLATES, loops,
            caption_style=recipe.get("caption_style"),
            caption_lines=recipe.get("lines"),
            caption_font=recipe.get("caption_font"),
            translate_to=recipe.get("translate_to"),
            ratio=recipe.get("ratio"),
            overlay_list=resolved,
            caption_size=recipe.get("caption_size"),
            caption_pos=recipe.get("caption_pos"),
            caption_anim=recipe.get("caption_anim"),
            speed=recipe.get("speed"),
            speed_pitched=bool(recipe.get("speed_pitched")),
            background=recipe.get("background"),
            title_style=recipe.get("title_style"),
            title_font=recipe.get("title_font"),
        )
        with SessionLocal() as session:
            clip = (session.query(Clip)
                    .filter(Clip.job_id == job_id, Clip.index == index).first())
            clip.rendered = True
            session.commit()
            # Re-read inside this session: `result` was serialised before the
            # flag was flipped and would tell the UI the export is still pending.
            return clip.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")


@app.post("/jobs/{job_id}/clips/{index}/rerender")
def rerender_clip(job_id: str, index: int, body: ClipEdit):
    """Re-cuts one clip to new in/out points, reusing the stored transcript."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    tpl = TEMPLATES.get((job.get("options") or {}).get("template", "classic"))
    loops = _gameplay_loops() if tpl and tpl["frame"] == "gameplay" else []
    try:
        return editing.rerender_clip(
            job_id, index, body.start, body.end, STORAGE_DIR, TEMPLATES, loops,
            caption_style=body.caption_style,
            caption_lines=[l.dict() for l in body.caption_lines] if body.caption_lines else None,
            caption_font=body.caption_font,
            translate_to=body.translate_to,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Re-render failed: {e}")


def _prepare_job(req: JobRequest, user: User = None, uploaded: bool = False) -> list:
    """Validate shared job options and return any pre-resolved gameplay loops."""
    if not uploaded:
        parsed = urlparse(req.url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Paste a public video link starting with http:// or https://.")
    tpl = TEMPLATES.get(req.template)
    if not tpl:
        raise HTTPException(status_code=400,
                            detail=f"Unknown template. Choose from: {', '.join(TEMPLATES)}")
    # Resolve the gameplay asset NOW so a missing file fails the request with a
    # clear message instead of dying mid-pipeline after download+transcription.
    gameplay_loops = _gameplay_loops() if tpl["frame"] == "gameplay" else []

    # Per-clip pricing means the price of what was ASKED for is known up front,
    # so the gate can be exact instead of "has any credit at all". Charged for
    # real once the analysis settles the actual clip count, which may be lower.
    if user:
        want = billing.credits_for_clips(req.n_clips)
        have = billing.balance(user.id)
        if have < want:
            raise HTTPException(
                status_code=402,
                detail=(f"{req.n_clips} clips cost {want} credits and you have "
                        f"{have}. Ask for fewer clips, top up, or upgrade."),
            )

    # Paid capability, enforced here rather than trusted from the client. The UI
    # hides the control for free accounts, but hiding a control is presentation,
    # not a gate -- the request can still arrive with the flag set.
    if req.multilingual and not billing.allows(user.plan if user else None,
                                               "multilingual",
                                               user.email if user else None):
        raise HTTPException(
            status_code=402,
            detail="Multilingual captions are on the Creator and Pro plans. "
                   "They transcribe speech that switches between languages "
                   "mid-sentence, which the standard model transcribes as noise.",
        )

    # The template's frame mode is resolved once, here, and stored with the job.
    # The editor has to preview the same composition the renderer builds, and it
    # was inferring that from the template name -- so every template added since
    # was silently previewed as `blur`, whatever it actually renders as.
    return gameplay_loops


def _start_job(req: JobRequest, background_tasks: BackgroundTasks,
               user: User = None, uploaded: bool = False, gameplay_loops: list = None) -> str:
    options = req.dict()
    options["frame"] = TEMPLATES.get(req.template, TEMPLATES["classic"])["frame"]
    options["input_type"] = "upload" if uploaded else "link"
    job_id = create_job(req.url, options, user_id=user.id if user else None)
    background_tasks.add_task(run_pipeline, job_id, req, gameplay_loops,
                              user.id if user else None, uploaded)
    return job_id


@app.post("/sources/preview")
def source_preview(body: SourcePreviewRequest):
    """Confirm a public URL's identity before a user starts a job."""
    parsed = urlparse(body.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Paste a public video link starting with http:// or https://.")
    try:
        return downloader.inspect_url(body.url.strip())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/jobs")
def submit_job(req: JobRequest, background_tasks: BackgroundTasks,
               user: User = Depends(auth.current_user_optional)):
    gameplay_loops = _prepare_job(req, user)
    job_id = _start_job(req, background_tasks, user, gameplay_loops=gameplay_loops)
    return {"job_id": job_id}


def _remove_failed_upload(job_id: str) -> None:
    """Remove a just-created job if its source file fails validation."""
    shutil.rmtree(os.path.join(STORAGE_DIR, job_id), ignore_errors=True)
    with SessionLocal() as session:
        row = session.get(Job, job_id)
        if row:
            session.delete(row)
            session.commit()


def _assert_video_file(path: str) -> None:
    """Reject a renamed text/archive file before it reaches ffmpeg workers."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    if probe.returncode != 0 or "video" not in probe.stdout:
        raise HTTPException(status_code=400, detail="That file is not a readable video.")


@app.post("/jobs/upload")
async def submit_uploaded_job(background_tasks: BackgroundTasks,
                              file: UploadFile = File(...), options: str = Form("{}"),
                              user: User = Depends(auth.current_user_optional)):
    """Create a job from a local video without involving a platform downloader."""
    try:
        req = JobRequest(**json.loads(options))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Upload settings were invalid.") from exc

    name = os.path.basename(file.filename or "video.mp4")
    ext = os.path.splitext(name)[1].lower()
    if ext not in VIDEO_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Upload an MP4, MOV, M4V, WebM or MKV video.")
    if (file.content_type and file.content_type != "application/octet-stream"
            and not file.content_type.startswith("video/")):
        raise HTTPException(status_code=400, detail="Choose a video file to upload.")

    gameplay_loops = _prepare_job(req, user, uploaded=True)
    req.url = f"upload://{name}"
    job_id = _start_job(req, background_tasks, user, uploaded=True,
                        gameplay_loops=gameplay_loops)
    job_dir = os.path.join(STORAGE_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    part_path = os.path.join(job_dir, "source.mp4.part")
    source_path = os.path.join(job_dir, "source.mp4")
    written = 0

    try:
        with open(part_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Uploads must be under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                out.write(chunk)
        if not written:
            raise HTTPException(status_code=400, detail="That upload was empty.")
        os.replace(part_path, source_path)
        _assert_video_file(source_path)
    except Exception:
        _remove_failed_upload(job_id)
        raise
    finally:
        await file.close()

    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/clips/{job_id}/{filename}")
def get_clip_file(job_id: str, filename: str):
    path = os.path.join(STORAGE_DIR, job_id, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(path, media_type="video/mp4")


def _build_proxies_quietly(job_id: str, video_path: str, job_dir: str,
                           clips: list, duration: float = None) -> None:
    """Builds one scrubbing proxy per clip, on a background thread.

    One proxy per clip, not one for the job. The editor only ever exposes a
    ~105s window around a clip (its trim handles are clamped to it), so a
    whole-source proxy spent minutes encoding footage nobody could reach --
    which is why the editor sat on "Preparing the editor preview" long after
    the clips were done.

    Clips are done in order, so clip 1 -- the one people open first -- is
    playable within seconds of its own render finishing. A failed proxy costs
    live editing for that clip and nothing else, so failure is swallowed per
    clip: this runs unsupervised while the real render is in flight and must
    never take the deliverable down with it.
    """
    for i, clip in enumerate(clips, start=1):
        lo, hi = render.proxy_window(clip["start"], clip["end"], duration)
        out_path = os.path.join(job_dir, f"preview_{i}.mp4")
        try:
            render.make_proxy(video_path, out_path, start=lo, end=hi)
            print(f"[clipper] editor preview ready for {job_id} clip {i} "
                  f"({lo:.0f}s-{hi:.0f}s)")
        except (RuntimeError, OSError) as e:
            print(f"[clipper] proxy for {job_id} clip {i} failed (live editing "
                  f"unavailable for it): {e}")


def run_pipeline(job_id: str, req: JobRequest, gameplay_loops: list = None,
                 job_user_id: str = None, uploaded_source: bool = False):
    gameplay_loops = gameplay_loops or []
    tpl = TEMPLATES.get(req.template, TEMPLATES["classic"])
    job_dir = os.path.join(STORAGE_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Link jobs fetch an audio-only stream first. Uploads already live in
        # this job directory, so extracting audio locally avoids yt-dlp entirely.
        audio_path = os.path.join(job_dir, "audio.mp3")
        if uploaded_source:
            video_path = os.path.join(job_dir, "source.mp4")
            if not os.path.exists(video_path):
                raise RuntimeError("The uploaded source video is missing. Upload it again.")
            update_job(job_id, status="downloading", progress_message="Preparing uploaded video...", percent=0)
            workspace.ensure_space(job_dir, os.path.getsize(video_path))
            downloader.extract_audio(video_path, audio_path)
        else:
            update_job(job_id, status="downloading", progress_message="Fetching audio track...", percent=0)
            audio_path = downloader.download_audio(
                req.url,
                audio_path,
                on_progress=lambda p: update_job(
                    job_id, percent=round(p), progress_message=f"Fetching audio track... {p:.0f}%"
                ),
            )

        update_job(job_id, status="transcribing",
                   progress_message="Transcribing speech with Deepgram...", percent=0)
        # The pricier model is asked for, not inferred. Tying it to the podcast
        # template was the wrong proxy: code-switching is a property of how
        # people SPEAK, not of the layout picked for them -- a bilingual
        # commentary video needs it and is not a podcast.
        # Diarization stays tied to the frame, because only the podcast
        # reframing reads speaker labels.
        transcript_json = transcriber.transcribe(
            audio_path,
            multilingual=bool(req.multilingual),
            diarize=tpl["frame"] == "podcast",
        )
        utterances = transcriber.get_utterances(transcript_json)
        if not utterances:
            raise RuntimeError(
                "No speech was found in this video. Clipper needs talking to work with -- "
                "try a podcast, interview, or commentary video."
            )
        numbered = transcriber.build_numbered_transcript(utterances)
        # Stored now so a later edit can reopen this job without paying Deepgram again.
        source_seconds = utterances[-1]["end"] if utterances else 0
        update_job(job_id, transcript=transcript_json, source_duration=source_seconds)

        # Source length no longer sets the price, but it still sets OUR cost, so
        # it is the one thing per-clip pricing has to bound. Rejected here,
        # after transcription has revealed the true duration.
        if source_seconds > billing.MAX_SOURCE_MINUTES * 60:
            update_job(
                job_id, status="failed",
                error=(f"This video is {source_seconds/3600:.1f} hours long, over "
                       f"the {billing.MAX_SOURCE_MINUTES // 60}-hour limit. "
                       f"Clip a section of it instead -- you were not charged."),
            )
            workspace.clear_intermediates(job_dir)
            return

        # Prosody: how lines were delivered, which the transcript cannot show.
        update_job(job_id, status="analyzing",
                   progress_message="Listening for energy, laughter and emphasis...", percent=0)
        utterances = energy.analyze(audio_path, utterances)

        update_job(job_id,
                   progress_message=f"Reading {len(utterances)} moments to find the best ones...",
                   percent=0)
        raw_clips = analyzer.pick_clips(
            utterances,
            req.n_clips,
            intent=req.intent,
            length_pref=req.length_pref,
            on_progress=lambda n, total: update_job(
                job_id,
                percent=100 if n < 0 else round(n / total * 100),
                progress_message=(
                    f"Comparing the {total} strongest moments..."
                    if n < 0 else f"Scoring section {n} of {total}..."
                ),
            ),
        )
        if not raw_clips:
            raise RuntimeError(
                "Nothing in this video scored well enough to clip. Videos with a clear "
                "spoken point -- podcasts, interviews, commentary -- work best."
            )
        resolved_clips = [analyzer.resolve_clip_times(c, utterances) for c in raw_clips]

        # Charged here, not earlier: the price is per clip, and only now is the
        # real number known. Asking for 5 and being handed 3 costs 3 -- billing
        # for clips the analysis could not find would make the per-clip promise
        # a lie. Still ahead of the render, which is where the compute goes.
        # Anonymous jobs are free while metering is unenforced.
        if job_user_id:
            cost = billing.credits_for_clips(len(resolved_clips))
            if not billing.charge(job_user_id, cost, job_id=job_id,
                                  note=f"{len(resolved_clips)} clip(s)"):
                have = billing.balance(job_user_id)
                update_job(
                    job_id, status="failed",
                    error=(f"These {len(resolved_clips)} clips need {cost} credits "
                           f"and you have {have}. Top up and run it again -- "
                           f"you were not charged."),
                )
                workspace.clear_intermediates(job_dir)
                return

        update_job(job_id, status="rendering",
                   progress_message="Checking disk space...", percent=0)
        if uploaded_source:
            workspace.ensure_space(job_dir, os.path.getsize(video_path))
            update_job(job_id, progress_message="Preparing your uploaded video...", percent=0)
        else:
            workspace.ensure_space(job_dir, downloader.estimate_video_bytes(req.url))
            update_job(job_id, progress_message="Downloading video...", percent=0)
            video_path = downloader.download_video(
                req.url,
                os.path.join(job_dir, "source.mp4"),
                on_progress=lambda p: update_job(
                    job_id, percent=round(p * 0.5), progress_message=f"Downloading video... {p:.0f}%"
                ),
            )

        # Start the editor proxies NOW, alongside clip rendering, instead of
        # after it. The trim/caption editor is useless without them, and building
        # them last meant the editor was dead at exactly the moment people open
        # it -- when their clips have just appeared. These are 640px veryfast
        # encodes of ~105s each, so they cost a background core, not the clips'
        # latency. Nothing waits on this thread: if it loses the race the editor
        # shows its "preparing" state for that clip, which is the truth.
        proxy_thread = threading.Thread(
            target=_build_proxies_quietly,
            args=(job_id, video_path, job_dir,
                  [{"start": c["start"], "end": c["end"]} for c in resolved_clips],
                  source_seconds),
            daemon=True,
        )
        proxy_thread.start()

        out_w, out_h = render.RATIOS.get(req.ratio, render.RATIOS["9:16"])
        if gameplay_loops:
            margin_v = render.split_caption_margin_v(out_h)
        else:
            src_w, src_h = render.probe_dimensions(video_path)
            # The podcast and fit frames sit their video high, so the generic
            # margin -- which assumes equal bands -- would strand captions in
            # the corner of the taller band underneath.
            place = {"podcast": render.podcast_caption_margin_v,
                     "fit": render.fit_caption_margin_v}.get(
                         tpl["frame"], render.caption_margin_v)
            margin_v = place(src_w, src_h, out_w, out_h)

        results = []
        total = len(resolved_clips)
        for i, clip in enumerate(resolved_clips, start=1):
            update_job(
                job_id,
                progress_message=f"Rendering clip {i} of {total}...",
                percent=round(50 + (i - 1) / total * 50),
            )
            subtitle_path = None
            audio_override = None

            # Where the people actually are in THIS clip. Per clip rather than
            # per job: a podcast cuts between a two-shot and a single, so one
            # plan for the whole source would frame most of the clips wrongly.
            # None is a normal answer -- see reframe.plan.
            clip_plan = None
            clip_margin_v = margin_v
            if tpl["frame"] == "podcast":
                # Broad except on purpose. Reframing is an ENHANCEMENT -- the
                # template renders without it -- so no failure in detection is
                # worth losing a job the user has already paid to transcribe.
                try:
                    clip_plan = reframe.plan(video_path, clip["start"],
                                             clip["end"], out_w, out_h)
                except Exception as exc:
                    print(f"[clipper] clip {i}: reframing failed ({exc}); "
                          "falling back to the fixed podcast frame")
                if clip_plan:
                    print(f"[clipper] clip {i}: reframing as {clip_plan['mode']}")
                    clip_margin_v = render.smart_caption_margin_v(clip_plan, out_h)

            # Dead air is what makes a clip feel trimmed rather than edited, so
            # this is on by default. Captions must use the remapped timings or
            # they desync by exactly the amount removed.
            segments = None
            clip_words = clip["words"]
            render_duration = clip["end"] - clip["start"]
            if req.tighten_pauses and clip_words:
                segments, clip_words, removed = pacing.tighten(
                    clip_words, clip["start"], clip["end"])
                if removed < 0.4:
                    segments, clip_words = None, clip["words"]   # not worth a re-cut
                else:
                    render_duration = sum(e - s for s, e in segments)
                    print(f"[clipper] clip {i}: removed {removed:.1f}s of dead air")

            mute_spans = None
            caption_words = clip_words
            if req.auto_censor and clip_words:
                offset = 0.0 if segments else clip["start"]
                caption_words, mute_spans = censor.apply(clip_words, offset)
                if mute_spans:
                    print(f"[clipper] clip {i}: muted {len(mute_spans)} word(s)")

            if req.mode == "original":
                if req.burn_subtitles and clip_words:
                    subtitle_path = os.path.join(job_dir, f"clip_{i}.ass")
                    subtitles.build_ass(caption_words,
                                        0.0 if segments else clip["start"], subtitle_path,
                                        clip_margin_v, style=tpl["caption_style"],
                                        play_res=(out_w, out_h),
                                        title=clip.get("title", ""),
                                        keywords=clip.get("keywords"))
                end_time = clip["end"]
            else:
                script_text = voiceover.write_narration_script(
                    numbered, clip["start_index"], clip["end_index"], req.language
                )
                audio_override = os.path.join(job_dir, f"voiceover_{i}.mp3")
                voiceover.generate_voiceover_audio(script_text, audio_override, req.voice)
                clip["voiceover_script"] = script_text
                end_time = clip["start"] + 9999  # duration governed by -shortest against the voiceover

            out_name = f"clip_{i}.mp4"
            render.render_clip(
                video_path,
                clip["start"],
                end_time,
                os.path.join(job_dir, out_name),
                subtitle_path=subtitle_path,
                audio_override_path=audio_override,
                # Round-robin so consecutive clips never reuse the same loop.
                gameplay_path=(gameplay_loops[(i - 1) % len(gameplay_loops)]
                               if gameplay_loops else None),
                ratio=req.ratio,
                frame=tpl["frame"],
                segments=segments,
                mute_spans=mute_spans,
                reframe_plan=clip_plan,
            )

            # Raw scores rank the clips; these are what gets shown. The mapping
            # is monotonic, so display never disagrees with the chosen order.
            shown_score, shown_scores = scoring.presentation_score(
                clip.get("score") or 0, clip.get("scores") or {})

            results.append({
                "file": out_name,
                "title": clip["title"],
                "hook": clip["hook"],
                "start": clip["start"],
                "end": clip["end"],
                "duration": round(render_duration, 1),
                "score": shown_score,
                "scores": shown_scores,
                "voiceover_script": clip.get("voiceover_script"),
                "words": clip_words,
            })

        # The clips are the deliverable; the source video is often several hundred
        # MB and is worthless once they exist.
        reclaimed = workspace.clear_intermediates(job_dir)

        update_job(job_id, status="done", progress_message="Done", percent=100, clips=results)
        print(f"[clipper] job {job_id} done, reclaimed {workspace.human(reclaimed)}")

    except Exception as e:
        # The user sees one sentence; the traceback goes to the server console.
        # RuntimeErrors are raised by us with messages written for humans; for
        # anything else, still show only the message, not the stack.
        traceback.print_exc()
        update_job(job_id, status="failed", error=str(e) or e.__class__.__name__)
