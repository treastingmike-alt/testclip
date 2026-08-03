"""Face-aware reframing for podcast footage.

A podcast is shot in landscape for a landscape screen: two people sitting a
metre apart, framed wide, with a lot of table and wall between them. Every
generic vertical treatment does badly by it. Centre-cropping cuts one person
out. Letterboxing keeps both but shrinks them into a strip with two thirds of
the canvas left over. Neither is what a human editor would do -- an editor
crops to the PEOPLE, and if there are two of them, gives each their own frame.

This module works out where the people are, and hands `render` a set of crop
windows to composite. It does not itself touch ffmpeg.

The whole design is about STABILITY. Detection output is jittery -- on real
footage the same seated speaker's box moves 200px between samples as they lean
and gesture -- and a crop wired straight to raw coordinates produces the
hand-held-camera look that reads as broken rather than as energetic. So every
number that reaches the renderer has been through smoothing, a dead zone, a
minimum shot length and a speed limit, in that order.
"""

import os
import subprocess

# The detector's own asset. Small (230 KB) and loaded once per render.
MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "..", "assets", "models",
                     "face_detection_yunet_2023mar.onnx")

# --- detection -------------------------------------------------------------

SAMPLE_FPS = 2.0          # detections per second of footage. Faces do not move
                          # meaningfully faster than this, and the smoothing
                          # below would throw away the extra precision anyway.
                          # At 25fps this is ~8% of the work of every-frame.
DETECT_WIDTH = 640        # detect on a downscale; boxes are scaled back up.
MIN_SCORE = 0.55          # below this the box is usually a hand or a poster.

# --- tracking --------------------------------------------------------------

# Two detections belong to the same person if their centres are within this
# fraction of frame width. Podcast speakers are seated and far apart, so this
# can be generous without ever merging the two of them.
TRACK_RADIUS = 0.18
MIN_TRACK_COVERAGE = 0.35  # a track seen in fewer than this fraction of samples
                           # is a passer-by, a reflection, or a poster face.

# Fraction of samples that must show two faces AT THE SAME INSTANT before the
# stacked layout is allowed.
#
# Two tracks is not the same claim as two people. A podcast edit that cuts
# between two camera angles on ONE speaker puts their face at a different screen
# position after each cut, and position-based tracking reads that as a second
# person -- so the stacked layout showed the same man twice, in two angles, side
# by side. Genuinely two-shot footage has both faces in nearly every frame
# (co-occurrence near 1.0); cut-based footage has them in none (near 0.0). The
# gap between those cases is enormous, so this threshold is not delicate.
MIN_CO_OCCURRENCE = 0.45

# A jump larger than this, in one sample step, is a camera CUT rather than a
# person moving -- nobody crosses a fifth of the frame in half a second. Cuts
# must snap, not glide: smoothly sliding the crop across a hard cut is the one
# motion that looks unmistakably like a bug rather than like camerawork.
SNAP_DISTANCE = 0.20

# --- smoothing and shot discipline ----------------------------------------

SMOOTHING = 0.12          # EMA weight on each new sample. Low = heavy damping.
DEAD_ZONE = 0.035         # fraction of frame width the target must move before
                          # the crop follows at all. Kills the constant micro
                          # drift that makes a shot look unmoored.
MAX_SPEED = 0.10          # max crop travel per second, as a fraction of frame
                          # width. A hard ceiling on how fast a move can be,
                          # independent of how far the subject jumped.
MIN_SHOT = 1.4            # seconds. Once the crop settles it stays put at least
                          # this long, so a speaker leaning back and forward
                          # cannot start a rocking motion.

# --- composition -----------------------------------------------------------

# Headroom: a face box is the face only -- no hair above, no shoulders below.
# Framing on the box centre puts the eyeline dead centre, which looks like a
# passport photo. Real framing puts the eyes on the upper third.
HEAD_ROOM = 0.55          # of a face height, reserved above the box
EYE_LINE = 0.38           # target position of the face centre down the crop


def _detector(width, height):
    """YuNet, or None when the model is missing.

    Missing model is not an error: the caller falls back to a fixed frame, so a
    checkout without the asset still renders -- just without the smart crop.
    """
    try:
        import cv2
    except ImportError:
        return None
    if not os.path.exists(MODEL):
        return None
    try:
        det = cv2.FaceDetectorYN.create(MODEL, "", (width, height),
                                        score_threshold=MIN_SCORE)
        det.setInputSize((width, height))
        return det
    except Exception:
        return None


def detect_faces(video_path, start=0.0, end=None, sample_fps=SAMPLE_FPS):
    """Sample the clip and return [(t, [(cx, cy, w, h, score), ...]), ...].

    Coordinates are fractions of frame width/height, so everything downstream is
    resolution-independent and the same numbers work for a proxy or a master.
    """
    try:
        import cv2
    except ImportError:
        return [], (0, 0)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return [], (0, 0)
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not src_w or not src_h:
        cap.release()
        return [], (0, 0)

    # Detect on a downscale. YuNet is scale-sensitive in cost but not much in
    # accuracy at these face sizes, and this is ~9x less pixel work at 1080p.
    scale = min(1.0, DETECT_WIDTH / src_w)
    det_w, det_h = int(src_w * scale), int(src_h * scale)
    det = _detector(det_w, det_h)
    if det is None:
        cap.release()
        return [], (src_w, src_h)

    if end is None:
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        end = frames / fps if frames else start + 60.0

    out = []
    step = 1.0 / sample_fps
    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            break
        if scale < 1.0:
            frame = cv2.resize(frame, (det_w, det_h))
        try:
            _, faces = det.detect(frame)
        except Exception:
            faces = None
        found = []
        if faces is not None:
            for f in faces:
                # float() on every component, not just the score. OpenCV hands
                # back numpy scalars, which propagate all the way into the plan
                # and format fine into an ffmpeg expression -- but are not JSON
                # serialisable, so the editor's preview endpoint 500s on a plan
                # the renderer was perfectly happy with.
                x, y, w, h = (float(f[0]), float(f[1]), float(f[2]), float(f[3]))
                found.append((
                    (x + w / 2) / det_w, (y + h / 2) / det_h,
                    w / det_w, h / det_h, float(f[-1]),
                ))
        out.append((t, found))
        t += step
    cap.release()
    return out, (src_w, src_h)


def build_tracks(samples):
    """Group per-frame detections into per-person tracks.

    Greedy nearest-centre association. Sufficient because the subjects are
    seated and metres apart -- the hard cases this would fail on (crossing
    paths, occlusion, similar-looking people adjacent) do not happen at a
    podcast table.
    """
    tracks = []
    total = len(samples) or 1

    for t, faces in samples:
        # Biggest first: the near speaker should claim their track before a
        # smaller background face can steal it.
        for cx, cy, w, h, score in sorted(faces, key=lambda f: -f[2] * f[3]):
            best, best_d = None, TRACK_RADIUS
            for tr in tracks:
                if tr["last_t"] == t:
                    continue                    # already matched this frame
                lx, ly = tr["points"][-1][1], tr["points"][-1][2]
                d = ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = tr, d
            if best is None:
                tracks.append({"points": [(t, cx, cy, w, h, score)], "last_t": t})
            else:
                best["points"].append((t, cx, cy, w, h, score))
                best["last_t"] = t

    # Drop anything too intermittent to be a participant.
    keep = [tr for tr in tracks if len(tr["points"]) / total >= MIN_TRACK_COVERAGE]
    # Left to right, so "speaker 1" is stable between renders of the same clip.
    keep.sort(key=lambda tr: sum(p[1] for p in tr["points"]) / len(tr["points"]))
    return keep


def smooth(points, key=1):
    """EMA + dead zone + speed limit + minimum shot, applied in that order.

    Each stage exists for a failure seen in the raw signal:
      EMA          -- per-sample detector noise
      dead zone    -- slow drift when the subject is effectively still
      min shot     -- oscillation when they rock between two positions
      speed limit  -- a lurch when detection briefly lands on something else
    """
    if not points:
        return []
    out = []
    cur = points[0][key]
    committed = cur
    last_move_t = points[0][0]

    prev_raw = points[0][key]

    for i, p in enumerate(points):
        t, v = p[0], p[key]

        # A cut is not motion, and none of the damping below should apply to it.
        # Every stage of this function exists to stop the crop reacting to a
        # subject who moved; when the SHOT changed instead, the crop has to
        # arrive at the new framing immediately or it spends a second sliding
        # across a frame the viewer already sees as a new angle.
        if abs(v - prev_raw) > SNAP_DISTANCE:
            # Two points a hair apart in time: the interpolation in `expr` then
            # renders this as a step rather than a ramp, without that function
            # needing to know cuts exist.
            if out:
                out.append((max(t - 0.04, out[-1][0] + 1e-3), committed))
            cur = committed = v
            last_move_t = t
            out.append((t, committed))
            prev_raw = v
            continue
        prev_raw = v

        cur += (v - cur) * SMOOTHING                     # EMA

        want = cur
        moved = abs(want - committed)
        held = t - last_move_t
        if moved > DEAD_ZONE and held >= MIN_SHOT:       # dead zone + min shot
            dt = t - (out[-1][0] if out else t)
            limit = MAX_SPEED * max(dt, 1e-3)            # speed limit
            step = max(-limit, min(limit, want - committed))
            committed += step
            last_move_t = t
        out.append((t, committed))
    return out


def co_occurrence(samples):
    """Fraction of sampled frames showing two or more faces at once.

    The question this answers is "are there two people in this shot", which is
    not the same question as "did tracking produce two tracks" -- see
    MIN_CO_OCCURRENCE.
    """
    seen = [s for s in samples if s[1]]
    if not seen:
        return 0.0
    return sum(1 for _, faces in seen if len(faces) >= 2) / len(seen)


def primary_timeline(samples):
    """The dominant face in each sample: who this shot is of, cut by cut.

    Deliberately not a track. When an edit cuts between angles the subject is
    the same person at a new position, and following the largest face per frame
    keeps them framed across the cut -- where following one positional track
    would hold on empty chair for every second shot.
    """
    out = []
    for t, faces in samples:
        if not faces:
            continue
        cx, cy, w, h, score = max(faces, key=lambda f: f[2] * f[3])
        out.append((t, cx, cy, w, h, score))
    return out


def _clamp_window(centre, size, extent=1.0):
    """Keep a window of `size` fully inside [0, extent]."""
    half = size / 2
    return min(max(centre, half), extent - half) if size < extent else extent / 2


def portrait_window(track, src_w, src_h, out_w, out_h):
    """Crop keyframes framing ONE speaker for a vertical canvas.

    Returns (win_w, win_h, [(t, cx, cy), ...]) in source-pixel units.

    The window is as tall as the source allows, because the extra height is
    what turns a face into a person -- shoulders, hands, the microphone. A
    tighter crop reads as a webcam.
    """
    aspect = out_w / out_h
    win_h = src_h
    win_w = win_h * aspect
    if win_w > src_w:                       # source too narrow for a full-height
        win_w = src_w                       # portrait window; take what we have
        win_h = win_w / aspect

    xs = smooth(track["points"], key=1)
    ys = smooth(track["points"], key=2)
    face_h = sum(p[4] for p in track["points"]) / len(track["points"]) * src_h

    keys = []
    for (t, cx), (_, cy) in zip(xs, ys):
        px, py = cx * src_w, cy * src_h
        # Sit the face on the eye line rather than dead centre: for the face to
        # land EYE_LINE down the window, the window's centre has to be BELOW the
        # face by the remaining fraction. Then refuse to go so low that the top
        # of the head leaves the frame.
        py = py + (0.5 - EYE_LINE) * win_h
        py = max(py, face_h * (0.5 + HEAD_ROOM))
        keys.append((t,
                     _clamp_window(px, win_w, src_w),
                     _clamp_window(py, win_h, src_h)))
    return win_w, win_h, keys


def stacked_windows(tracks, src_w, src_h, out_w, out_h):
    """Two crop windows, one per speaker, for a vertically stacked layout.

    This is the shape podcast clips actually take on the platforms, and the
    reason is not aesthetic: two people a metre apart in a landscape frame
    cannot both be large in one vertical crop. Given each of them their own
    tile, both are large. The cost is that the two are no longer in a shared
    space -- which viewers accept completely, because every podcast clip they
    have ever seen is cut this way.
    """
    tile_h = out_h / 2
    aspect = out_w / tile_h                 # each tile is wide, not tall

    # Each speaker gets at most their half of the frame, or the crop windows
    # would overlap and show the same person twice.
    win_w = min(src_w / 2, src_h * aspect)
    win_h = win_w / aspect
    if win_h > src_h:
        win_h = src_h
        win_w = win_h * aspect

    out = []
    for tr in tracks[:2]:
        xs = smooth(tr["points"], key=1)
        ys = smooth(tr["points"], key=2)
        face_h = sum(p[4] for p in tr["points"]) / len(tr["points"]) * src_h
        keys = []
        for (t, cx), (_, cy) in zip(xs, ys):
            px, py = cx * src_w, cy * src_h
            py = max(py, face_h * (0.5 + HEAD_ROOM))
            keys.append((t,
                         _clamp_window(px, win_w, src_w),
                         _clamp_window(py, win_h, src_h)))
        out.append(keys)
    return win_w, win_h, out


def expr(keys, index, span):
    """A piecewise-linear ffmpeg expression for one crop coordinate over time.

    ffmpeg's `crop` takes an expression in `t`, so the whole move is described
    once at filter-build time rather than by re-invoking anything per frame.
    Keyframes that did not move are collapsed first -- after the dead zone most
    of them are identical, and emitting all of them would build a needlessly
    enormous nested expression for a shot that mostly sits still.
    """
    pts = [(t, round(v, 1)) for t, v in keys]
    trimmed = [pts[0]]
    for p in pts[1:]:
        if abs(p[1] - trimmed[-1][1]) > 0.5:
            trimmed.append(p)
    if len(trimmed) == 1:
        return f"{trimmed[0][1]:.1f}"

    # Built from the last segment backwards so each if() nests into the else.
    e = f"{trimmed[-1][1]:.1f}"
    for (t0, v0), (t1, v1) in zip(reversed(trimmed[:-1]), reversed(trimmed[1:])):
        dt = max(t1 - t0, 1e-3)
        ramp = f"{v0:.1f}+({v1:.1f}-{v0:.1f})*(t-{t0:.2f})/{dt:.2f}"
        e = f"if(lt(t\\,{t1:.2f})\\,{ramp}\\,{e})"
    return e


def plan(video_path, start, end, out_w, out_h):
    """Work out how this clip should be framed.

    Returns a dict the renderer can act on, or None to mean "no idea, use the
    fixed frame". Returning None is a normal outcome, not a failure: footage
    with no clear faces (a screen recording, a b-roll montage, a wide stage
    shot) genuinely has nothing to track, and a confident wrong crop there is
    far worse than the plain treatment.
    """
    samples, (src_w, src_h) = detect_faces(video_path, start, end)
    if not samples or not src_w:
        return None
    tracks = build_tracks(samples)
    if not tracks:
        return None

    # Stacked requires two people VISIBLE TOGETHER, not merely two tracks. The
    # earlier version asked only for two tracks, and an edit that cuts between
    # two angles on one speaker satisfies that -- which is how the same man
    # ended up in both tiles at once, facing two different directions.
    together = co_occurrence(samples)
    if len(tracks) >= 2 and together >= MIN_CO_OCCURRENCE:
        win_w, win_h, windows = stacked_windows(tracks, src_w, src_h, out_w, out_h)
        mode = "stacked"
    else:
        # One person, or an edit that shows them one at a time. Either way the
        # whole frame belongs to whoever is on screen -- following the dominant
        # face per sample keeps that true across the cuts.
        subject = {"points": primary_timeline(samples)}
        if not subject["points"]:
            return None
        win_w, win_h, keys = portrait_window(subject, src_w, src_h, out_w, out_h)
        windows, mode = [keys], "single"

    # Detection ran against source timestamps, but the renderer seeks to `start`
    # first, so inside the filter graph `t` is zero at the clip's first frame.
    # Rebasing here rather than at the call site keeps the expression builder
    # honest about what `t` means.
    windows = [[(t - start, x, y) for t, x, y in keys] for keys in windows]

    return {"mode": mode, "src": (src_w, src_h),
            "win": (int(win_w), int(win_h)), "windows": windows}
