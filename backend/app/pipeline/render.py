"""Cuts a clip from the source video, reframes it to 9:16 without cropping
content (blurred background fill + centered full frame), burns in captions,
and keeps the original audio track by default.
"""

import json
import os
import subprocess

OUT_W, OUT_H = 1080, 1920

# The background is blurred at quarter size and then scaled back up. Blurring is
# per-pixel work, so doing it at 540x960 instead of 1080x1920 is ~4x cheaper --
# and since the result is heavily blurred anyway, the upscale is invisible.
REFRAME_FILTER = (
    "[0:v]scale=540:960:force_original_aspect_ratio=increase,"
    "crop=540:960,gblur=sigma=12,scale=1080:1920[bg];"
    "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2[framed]"
)


def probe_dimensions(video_path: str) -> tuple:
    """Returns (width, height) of the first video stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", video_path],
        check=True, capture_output=True, text=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def caption_margin_from_position(out_h: int, position: float) -> int:
    """Margin for a caption block whose CENTRE sits at `position` (0..1).

    ASS margins are measured from the bottom, so a position of 0.9 (near the
    bottom of frame) becomes a small margin. Clamped so captions cannot be
    dragged off the canvas entirely.
    """
    position = max(0.05, min(0.95, float(position)))
    return int(round(out_h * (1.0 - position)))


# Reels/TikTok/Shorts paint their own UI over the bottom of the video -- the
# caption, the audio strip, the like/comment/share rail. Anything below this is
# read by nobody.
PLATFORM_UI_FRACTION = 0.17


def caption_margin_v(src_w: int, src_h: int, out_w: int = OUT_W, out_h: int = OUT_H) -> int:
    """Distance from the bottom of the output frame to the caption baseline.

    Two constraints, in order: captions must clear the platform's own UI, and
    they should sit under the video rather than across a face. Clearing the UI
    wins -- a perfectly placed caption hidden behind an Instagram like button is
    worth nothing.
    """
    floor = int(out_h * PLATFORM_UI_FRACTION)

    fg_h = content_height(src_w, src_h, out_w, out_h)
    band_height = (out_h - fg_h) / 2

    if band_height >= 200:
        # Sit in the lower blur band, nudged toward the video, but never below
        # the platform UI line.
        return max(floor, int(band_height * 0.40))

    # Little or no letterbox: ride just above the platform chrome.
    return floor


# Output canvas per aspect ratio. 9:16 for Shorts/Reels/TikTok, 1:1 for feed
# posts, 16:9 for YouTube proper.
RATIOS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


# A 16:9 source letterboxed into 9:16 fills only 32% of the frame -- two thirds
# of a phone screen is blurred wallpaper. Zooming in slightly (which crops the
# sides) buys a lot of content area cheaply, because the subject in talking-head
# footage is centred and the edges are usually room.
#
# The cap matters: past ~1.45x you start guillotining people at the shoulders.
MIN_CONTENT_FRACTION = 0.44     # aim for the video covering >= 44% of frame height
MAX_ZOOM = 1.35                 # ~26% off the sides; past this, wide shots lose
                                # their scenery and people lose their shoulders


def content_height(src_w: int, src_h: int, out_w: int, out_h: int) -> int:
    """Height the source occupies after the zoom policy, in output pixels."""
    natural = out_w * src_h / src_w              # height at full output width
    if natural >= out_h:
        return out_h
    wanted = out_h * MIN_CONTENT_FRACTION
    zoom = min(max(wanted / natural, 1.0), MAX_ZOOM)
    return int(round(min(natural * zoom, out_h)))


def blur_filter(width: int, height: int, src_w: int = None, src_h: int = None) -> str:
    """Subject centred on a blurred fill of itself, for any canvas size.

    Keeps the quarter-size blur trick: gblur is per-pixel work, so blurring at
    half dimensions and upscaling is ~4x cheaper and invisible once blurred.
    """
    half_w, half_h = width // 2, height // 2
    bg = (f"[0:v]scale={half_w}:{half_h}:force_original_aspect_ratio=increase,"
          f"crop={half_w}:{half_h},gblur=sigma=12,scale={width}:{height}[bg];")

    if src_w and src_h:
        fg_h = content_height(src_w, src_h, width, height)
        # scale to that height, then crop the overflowing sides back to width
        fg = (f"[0:v]scale=-2:{fg_h}:force_original_aspect_ratio=increase,"
              f"crop={width}:{fg_h}[fg];")
    else:
        fg = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"

    return bg + fg + "[bg][fg]overlay=(W-w)/2:(H-h)/2[framed]"


def fit_filter(width: int, height: int, background: str = "black") -> str:
    """Whole frame visible on flat bars -- nothing cropped, nothing blurred.

    `background` is any ffmpeg colour ('black', '#1e1e2e', 'white'). The bars are
    a design surface, not dead space: a brand colour there reads as intentional
    where black reads as a mistake.
    """
    colour = (background or "black").replace("#", "0x")
    return (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{colour}[framed]"
    )


def fill_filter(width: int, height: int) -> str:
    """Edge-to-edge: scale up and crop. No bars, but the sides are lost."""
    return (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}[framed]"
    )


def split_filter(width: int, height: int, gameplay_index: int) -> str:
    """Speaker on top, looping gameplay below -- the retention-bait format."""
    half = height // 2
    return (
        f"[0:v]scale={width}:{half}:force_original_aspect_ratio=increase,"
        f"crop={width}:{half}[top];"
        f"[{gameplay_index}:v]scale={width}:{half}:force_original_aspect_ratio=increase,"
        f"crop={width}:{half}[bot];"
        f"[top][bot]vstack=inputs=2[framed]"
    )


# Speed change, two honestly different effects:
#
#   "natural"  atempo    -- resamples in the time domain, pitch preserved. This
#                           is what you want for "make it punchier".
#   "pitched"  asetrate  -- replays the samples at a different rate, so pitch
#                           moves with speed. Chipmunk up, demon down. Deliberate
#                           meme effect, not a bug.
#
# atempo only accepts 0.5-2.0 per instance, so faster/slower gets chained.
MIN_SPEED = 0.5
MAX_SPEED = 3.0


def _atempo_chain(speed: float) -> str:
    """atempo clauses whose product is `speed`, each within ffmpeg's 0.5-2.0."""
    parts = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 0.001:
        parts.append(f"atempo={remaining:.4f}")
    return ",".join(parts) or "anull"


def speed_filters(speed: float, pitched: bool, sample_rate: int = 44100) -> tuple:
    """Returns (video_filter, audio_filter) for a playback speed change."""
    speed = max(MIN_SPEED, min(MAX_SPEED, float(speed)))
    video = f"setpts={1.0 / speed:.6f}*PTS"
    if pitched:
        # Pitch rides along with speed, then resample back to a normal rate so
        # downstream filters and the encoder see the sample rate they expect.
        audio = (f"asetrate={int(sample_rate * speed)},"
                 f"aresample={sample_rate},atempo=1.0")
    else:
        audio = _atempo_chain(speed)
    return video, audio


def split_caption_margin_v(height: int) -> int:
    """Puts captions just below the speaker/gameplay seam at height/2."""
    return height // 2 - 80


def _segments_filter(segments: list, want_audio: bool) -> tuple:
    """Cuts the source to `segments` and concatenates them, dropping the gaps.

    Returns (filter_string, video_label, audio_label). Timestamps are reset per
    piece with setpts/asetpts, otherwise concat keeps the original PTS and the
    output stalls for exactly as long as the gap we just removed.

    Note the input here must NOT be pre-seeked with -ss: these are absolute
    source times.
    """
    parts = []
    for n, (seg_start, seg_end) in enumerate(segments):
        parts.append(
            f"[0:v]trim=start={seg_start:.3f}:end={seg_end:.3f},"
            f"setpts=PTS-STARTPTS[sv{n}]"
        )
        if want_audio:
            parts.append(
                f"[0:a]atrim=start={seg_start:.3f}:end={seg_end:.3f},"
                f"asetpts=PTS-STARTPTS[sa{n}]"
            )

    if want_audio:
        joins = "".join(f"[sv{n}][sa{n}]" for n in range(len(segments)))
        parts.append(f"{joins}concat=n={len(segments)}:v=1:a=1[cv][ca]")
        return ";".join(parts), "[cv]", "[ca]"

    joins = "".join(f"[sv{n}]" for n in range(len(segments)))
    parts.append(f"{joins}concat=n={len(segments)}:v=1[cv]")
    return ";".join(parts), "[cv]", None


def render_clip(
    video_path: str,
    start: float,
    end: float,
    out_path: str,
    subtitle_path: str = None,
    audio_override_path: str = None,
    gameplay_path: str = None,
    ratio: str = "9:16",
    frame: str = "blur",
    segments: list = None,
    mute_spans: list = None,
    overlay_list: list = None,
    speed: float = None,
    speed_pitched: bool = False,
    background: str = None,
) -> str:
    duration = max(end - start, 1)
    width, height = RATIOS.get(ratio, RATIOS["9:16"])

    # Pause-tightening needs absolute source timestamps, so the input cannot be
    # pre-seeked; trim= inside the filter graph does the seeking instead.
    if segments:
        cmd = ["ffmpeg", "-y", "-i", video_path]
    else:
        cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", video_path]
    cmd_inputs = [video_path]        # index 0

    if audio_override_path:
        cmd += ["-i", audio_override_path]
        cmd_inputs.append(audio_override_path)
        audio_map = ["-map", "1:a"]
        extra = ["-shortest"]
    else:
        audio_map = ["-map", "0:a"]
        extra = []

    # Pause-tightening runs first, so the framing filters operate on the already
    # shortened stream. Its output is relabelled to [0:v]/[0:a] equivalents by
    # feeding the concat labels into the framing filter below.
    prefix = ""
    src_v, src_a = "[0:v]", "[0:a]"
    if segments:
        want_audio = not audio_override_path
        prefix, src_v, src_a = _segments_filter(segments, want_audio)
        prefix += ";"
        duration = sum(e - s for s, e in segments)
        if not want_audio:
            src_a = None

    if gameplay_path:
        # -stream_loop -1 loops short gameplay files; the output -t caps length.
        gameplay_input_index = 2 if audio_override_path else 1
        cmd += ["-stream_loop", "-1", "-i", gameplay_path]
        cmd_inputs.append(gameplay_path)
        filter_complex = split_filter(width, height, gameplay_input_index)
    elif frame == "fit":
        filter_complex = fit_filter(width, height, background)
    elif frame == "fill":
        filter_complex = fill_filter(width, height)
    else:
        # Pass the real source dimensions so the zoom policy can apply -- without
        # them blur_filter falls back to plain letterboxing.
        try:
            sw, sh = probe_dimensions(video_path)
        except (subprocess.SubprocessError, ValueError, KeyError, IndexError):
            sw = sh = None
        filter_complex = blur_filter(width, height, sw, sh)

    if segments:
        # Point the framing filter at the concatenated stream. An ffmpeg output
        # label may only be consumed once, and blur_filter reads the source
        # twice (background + foreground), so fan it out with split first.
        uses = filter_complex.count("[0:v]")
        if uses > 1:
            labels = [f"[cvs{i}]" for i in range(uses)]
            prefix += f"{src_v}split={uses}{''.join(labels)};"
            for label in labels:
                filter_complex = filter_complex.replace("[0:v]", label, 1)
        else:
            filter_complex = filter_complex.replace("[0:v]", src_v)
        filter_complex = prefix + filter_complex
        if src_a:
            audio_map = ["-map", src_a]

    # Auto-censor: silence exactly the profane words. Times are already on the
    # output timeline. Skipped under a voiceover override -- that audio is
    # generated text and never profane.
    if mute_spans and not audio_override_path:
        gates = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in mute_spans)
        src = audio_map[1]
        filter_complex += f";{src if src.startswith('[') else '[' + src + ']'}"                           f"volume=enable='{gates}':volume=0[aout]"
        audio_map = ["-map", "[aout]"]

    video_label = "[framed]"

    if subtitle_path:
        # subtitles filter must run after the video is in its final [framed] state.
        # fontsdir makes the shipped caption fonts available to libass on any
        # machine -- without it, font choice would silently depend on the host.
        from app.pipeline.subtitles import FONTS_DIR
        escaped = subtitle_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        fonts_escaped = FONTS_DIR.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        filter_complex += f";[framed]subtitles='{escaped}':fontsdir='{fonts_escaped}'[v]"
        video_label = "[v]"

    # Overlays go on last so a logo or handle sits above the captions, and so
    # their coordinates refer to the final canvas rather than the source frame.
    if overlay_list:
        from app.pipeline import overlays as _ov
        next_input = len(cmd_inputs)
        extra_inputs, ov_filter, video_label = _ov.build(
            overlay_list, width, height, video_label, next_input,
            os.path.dirname(os.path.abspath(out_path)))
        for path in extra_inputs:
            cmd += ["-i", path]
        if ov_filter:
            filter_complex += ";" + ov_filter

    if speed and abs(speed - 1.0) > 0.001:
        vf, af = speed_filters(speed, speed_pitched)
        filter_complex += f";{video_label}{vf}[vspd]"
        video_label = "[vspd]"
        a_in = audio_map[1]
        a_in = a_in if a_in.startswith("[") else f"[{a_in}]"
        filter_complex += f";{a_in}{af}[aspd]"
        audio_map = ["-map", "[aspd]"]
        duration = duration / max(speed, 0.001)

    video_map = video_label

    cmd += [
        "-filter_complex", filter_complex,
        "-map", video_map,
        *audio_map,
        "-t", str(duration),
        # veryfast/crf 20 encodes several times quicker than medium/crf 18 and the
        # difference is not visible once the clip is re-compressed by TikTok/Shorts
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        *extra,
        "-movflags", "+faststart",
        out_path,
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + proc.stderr[-2000:])
    return out_path


# ---------------------------------------------------------------------------
# Preview proxy
# ---------------------------------------------------------------------------
# Editing should feel instant, and it cannot if every change costs an ffmpeg
# pass over a 1080p source. So the pipeline also writes a small, seek-friendly
# copy of the source that a browser can stream and scrub freely. The editor
# plays THAT, draws captions as DOM elements on top, and only renders a real
# MP4 when the user exports. Same idea as an NLE's proxy workflow.

PROXY_WIDTH = 640           # enough to judge framing and read captions
PROXY_CRF = 30              # small file; quality is irrelevant for a preview
PROXY_KEYFRAME_SECONDS = 1  # dense keyframes so scrubbing lands where you drop it


def make_proxy(video_path: str, out_path: str) -> str:
    """Writes a small, densely-keyframed preview copy of the source.

    Runs AFTER the clips are delivered, never before -- it is an editing
    convenience, and nobody should wait on it to see what they paid for.
    """
    fps_probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        num, _, den = fps_probe.stdout.strip().partition("/")
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    gop = max(1, int(round(fps * PROXY_KEYFRAME_SECONDS)))
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vf", f"scale={PROXY_WIDTH}:-2",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", str(PROXY_CRF),
         "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
         "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k",
         "-movflags", "+faststart",
         out_path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("proxy render failed:\n" + proc.stderr[-1200:])
    return out_path
