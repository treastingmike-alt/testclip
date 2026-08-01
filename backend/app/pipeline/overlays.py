"""Burning text and image overlays into a clip -- handles, logos, callouts.

Positions are stored RELATIVE (0..1 of frame width/height), never in pixels.
A clip can be re-exported at 9:16, 1:1 or 16:9 from the same recipe, and a
handle pinned to the bottom-centre has to stay at the bottom-centre in all of
them. Pixels would silently drift or fall off the canvas.

Sizes are relative to frame HEIGHT for the same reason: height is the dimension
that changes least between vertical formats, so text keeps its apparent weight.

The platform UI overlays its own controls on the bottom ~12% and right ~10% of
a vertical video, which is why SAFE_* exists -- the editor draws those guides so
nobody pins their handle underneath a Share button.
"""

import os

# Fractions of the frame covered by platform chrome on Reels/TikTok/Shorts.
SAFE_BOTTOM = 0.12
SAFE_TOP = 0.06
SAFE_RIGHT = 0.10

# Text sizes are a fraction of frame height; clamp so nothing becomes unreadable
# or swallows the frame.
MIN_SIZE = 0.015
MAX_SIZE = 0.25
DEFAULT_SIZE = 0.045


# drawtext text is passed via a FILE, never inline.
#
# Inline `text=` has two nested escaping layers (filter-graph, then drawtext's
# own parser) and they fight: a single quote escaped for drawtext terminates the
# filter-graph quoting, and an apostrophe in "it's" is enough to break the whole
# render. textfile= sidesteps both -- the only thing needing escaping is a path
# we generate ourselves.
def _write_text(text: str, work_dir: str, name: str) -> str:
    path = os.path.join(work_dir, f"_ovtext_{name}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _font_file(font_id: str) -> str:
    """Absolute path to a shipped font file, or None to let ffmpeg choose.

    Reads the same auto-discovered registry the subtitle renderer uses, so a
    font dropped into assets/fonts works for overlays and captions alike --
    this used to be a second hardcoded list that quietly went stale.
    """
    from app.pipeline.subtitles import font_entry, FONTS_DIR
    entry = font_entry(font_id)
    if not entry or not entry.get("file"):
        return None
    path = os.path.join(FONTS_DIR, entry["file"])
    return path if os.path.exists(path) else None


def _escape_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _hex_to_ffmpeg(colour: str, opacity: float = 1.0) -> str:
    """'#ff0044' -> '0xff0044@1.0'. ffmpeg wants 0x, not #."""
    c = (colour or "#ffffff").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return f"0x{c}@{max(0.0, min(1.0, opacity)):.2f}"


def _enable_expr(overlay: dict) -> str:
    """`enable=` clause limiting an overlay to its own time window.

    Times are seconds from the START OF THE CLIP, not the source video: the
    render trims with -ss/-t, so output timestamps already begin at 0 and `t`
    lines up with what the editor's timeline shows.

    An overlay with no window is on for the whole clip, which is what every
    existing saved recipe means.
    """
    start = overlay.get("t_start")
    end = overlay.get("t_end")
    if start is None and end is None:
        return ""
    start = max(0.0, float(start if start is not None else 0.0))
    if end is None:
        return f":enable='gte(t,{start:.3f})'"
    end = float(end)
    if end <= start:
        return ""
    return f":enable='between(t,{start:.3f},{end:.3f})'"


def text_filter(overlay: dict, width: int, height: int,
                work_dir: str, name: str) -> str:
    """A drawtext clause for one text overlay, or '' if it has no content."""
    text = (overlay.get("text") or "").strip()
    if not text:
        return ""
    text_path = _write_text(text, work_dir, name)

    size_frac = float(overlay.get("size") or DEFAULT_SIZE)
    size_px = max(8, int(round(height * max(MIN_SIZE, min(MAX_SIZE, size_frac)))))

    x_frac = float(overlay.get("x", 0.5))
    y_frac = float(overlay.get("y", 0.9))

    # x/y describe the CENTRE of the overlay, so the same recipe centres text of
    # any length -- ffmpeg gives us text_w/text_h to subtract at render time.
    x_expr = f"(w*{x_frac:.4f})-(text_w/2)"
    y_expr = f"(h*{y_frac:.4f})-(text_h/2)"

    parts = [
        f"textfile='{_escape_path(text_path)}'",
        # Without this, drawtext still runs its own expansion over the file and
        # treats % as the start of a %{...} directive -- so "100%" silently
        # renders NOTHING AT ALL rather than erroring. expansion=none makes the
        # file content literal, which is the only sane behaviour for user text.
        "expansion=none",
        f"fontsize={size_px}",
        f"fontcolor={_hex_to_ffmpeg(overlay.get('color'), overlay.get('opacity', 1.0))}",
        f"x={x_expr}",
        f"y={y_expr}",
    ]

    font_path = _font_file(overlay.get("font"))
    if font_path:
        parts.append(f"fontfile='{_escape_path(font_path)}'")

    if overlay.get("plate"):
        parts.append("box=1")
        parts.append(f"boxcolor={_hex_to_ffmpeg(overlay.get('plate'), overlay.get('plate_opacity', 0.75))}")
        parts.append(f"boxborderw={max(4, size_px // 5)}")
    else:
        # An outline keeps text legible over any footage without a plate.
        parts.append(f"borderw={max(2, size_px // 14)}")
        parts.append("bordercolor=0x000000@0.85")

    return "drawtext=" + ":".join(parts) + _enable_expr(overlay)


def build(overlay_list: list, width: int, height: int, video_label: str,
          input_index: int, work_dir: str) -> tuple:
    """Filter chain applying every overlay to `video_label`.

    Returns (extra_inputs, filter_string, out_label). extra_inputs are file
    paths the caller must add as further -i arguments, in order, starting at
    input_index -- image overlays are real ffmpeg inputs, not filter arguments.
    """
    if not overlay_list:
        return [], "", video_label

    # A platform handle is authored as ONE overlay but drawn as two things: the
    # brand mark and the text beside it. Expanding here keeps the editor's model
    # simple (drag one handle) while the render shows what a viewer recognises.
    from app.pipeline.platform_logos import expand_handle
    expanded = []
    for o in overlay_list:
        expanded.extend(expand_handle(o, width, height, work_dir)
                        if o.get("platform") else [o])
    overlay_list = expanded

    texts = [o for o in overlay_list if o.get("type", "text") == "text"]
    images = [o for o in overlay_list
              if o.get("type") == "image" and o.get("path") and os.path.exists(o["path"])]

    extra_inputs = []
    chain = []
    current = video_label

    # Images first so text always sits on top of a logo, never behind it.
    for n, ov in enumerate(images):
        idx = input_index + n
        extra_inputs.append(ov["path"])

        w_px = max(8, int(round(height * float(ov.get("size") or 0.12))))
        x_frac = float(ov.get("x", 0.88))
        y_frac = float(ov.get("y", 0.08))
        opacity = max(0.0, min(1.0, float(ov.get("opacity", 1.0))))

        scaled = f"[ovi{n}]"
        chain.append(
            f"[{idx}:v]scale={w_px}:-1,format=rgba,"
            f"colorchannelmixer=aa={opacity:.2f}{scaled}"
        )
        nxt = f"[ovo{n}]"
        chain.append(
            f"{current}{scaled}overlay="
            f"x=(W*{x_frac:.4f})-(w/2):y=(H*{y_frac:.4f})-(h/2)"
            f"{_enable_expr(ov)}{nxt}"
        )
        current = nxt

    clauses = [c for c in (text_filter(o, width, height, work_dir, str(i))
                           for i, o in enumerate(texts)) if c]
    if clauses:
        nxt = "[ovtext]"
        chain.append(f"{current}{','.join(clauses)}{nxt}")
        current = nxt

    if not chain:
        return [], "", video_label
    return extra_inputs, ";".join(chain), current
