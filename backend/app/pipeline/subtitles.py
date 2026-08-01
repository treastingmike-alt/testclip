"""Builds short-form captions as an ASS subtitle file with word-by-word highlighting.

Why ASS instead of SRT
----------------------
SRT carries no styling, so ffmpeg's `subtitles` filter falls back to libass
defaults -- including PlayResY=288. Every FontSize and MarginV then gets scaled
by 1920/288, which is how a MarginV of 120 ended up ~800px off the bottom,
landing captions in the middle of the frame on top of people's faces.

Writing ASS ourselves lets us declare PlayResX/PlayResY as the real 1080x1920
output, so every number below is honest pixels, and lets us emit one event per
word so the active word can be highlighted as it is spoken.
"""

# --- caption feel -------------------------------------------------------------
MAX_WORDS_PER_CUE = 4          # keep lines short enough to read in a glance

# Caption sizes are px on the 1080x1920 reference canvas -- the same number the
# editor shows. Bounds keep text readable without swallowing the frame.
# Captions are burned in BEFORE the speed filter runs, so subtitle timings stay
# on the ORIGINAL timeline and the speed change carries them along with the
# picture. Scaling them here as well would double-apply the speed.
# ---------------------------------------------------------------------------
# Caption animation
# ---------------------------------------------------------------------------
# How a cue ENTERS the frame, independent of how it is coloured. Style says what
# captions look like; animation says how they arrive. Kept separate so any style
# can wear any animation.
#
# ASS override tags, applied per cue:
#   \fad(in,out)          fade in/out, milliseconds
#   \t(t0,t1,tags)        interpolate tags between two times
#   \move(x0,y0,x1,y1,..) slide the line between two points
#   \fscx/\fscy           horizontal/vertical scale, percent
#
# Durations stay short (<=220ms). Anything slower reads as sluggish on a clip
# where each cue is on screen for well under two seconds.
ANIMATIONS = {
    "none":    {"name": "None",      "desc": "Cuts straight in"},
    "fade":    {"name": "Fade",      "desc": "Soft fade in and out"},
    "pop":     {"name": "Pop",       "desc": "Scales up with a slight overshoot"},
    "riseup":  {"name": "Rise",      "desc": "Slides up into place"},
    "punch":   {"name": "Punch",     "desc": "Snaps in oversized, settles"},
    "bounce":  {"name": "Bounce",    "desc": "Overshoots then springs back"},
}


def animation_tags(anim: str, cue_ms: int, play_res: tuple, margin_v: int) -> str:
    """Leading ASS override tags that animate a cue's entrance.

    cue_ms is the cue's own duration, so effects never outlast the line they
    belong to -- a 200ms entrance on a 150ms cue would simply never finish.
    """
    if not anim or anim == "none":
        return ""
    span = max(60, min(220, cue_ms // 3))

    # Fade-in only, NEVER a fade-out here. A cue is emitted as one Dialogue line
    # PER WORD (that is how the karaoke highlight works), so a fade-out on this
    # line would fade the caption away after the first word and the next word's
    # line would cut back in -- read as a flicker, not an entrance.
    if anim == "fade":
        return f"\\fad({span},0)"

    if anim == "pop":
        return f"\\fscx60\\fscy60\\t(0,{span},\\fscx100\\fscy100)\\fad({span // 2},0)"

    if anim == "punch":
        return (f"\\fscx130\\fscy130\\t(0,{span},\\fscx100\\fscy100)"
                f"\\fad({span // 3},0)")

    if anim == "bounce":
        half = max(30, span // 2)
        return (f"\\fscx70\\fscy70\\t(0,{half},\\fscx112\\fscy112)"
                f"\\t({half},{span},\\fscx100\\fscy100)\\fad({half},0)")

    if anim == "riseup":
        w, h = play_res
        y_end = h - margin_v
        y_start = y_end + int(h * 0.045)
        return f"\\move({w // 2},{y_start},{w // 2},{y_end},0,{span})\\fad({span},0)"

    return ""


MIN_CAPTION_PX = 28
MAX_CAPTION_PX = 160

# Shipped caption fonts (all SIL Open Font License -- free for commercial use).
# `family` is the name INSIDE the ttf, which is what libass matches on; the
# renderer points ffmpeg's fontsdir at FONTS_DIR so these work on any machine,
# not just one that happens to have them installed.
import os as _os
import re as _re
FONTS_DIR = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "assets", "fonts"))
def _family_of(path: str) -> str:
    """The name libass will actually match this file by.

    This is fussier than it looks. libass asks its font provider for a family,
    and on a machine where the font is not installed system-wide the only thing
    that matches is name record 1 -- NOT record 16, the "typographic family".
    PIL returns record 16, which is why asking for "Poppins" silently rendered
    in Helvetica: the file calls itself "Poppins ExtraBold".

    When the subfamily is anything other than Regular it has to be appended too,
    or a weight-specific file never matches: "Mukta" misses, "Mukta ExtraBold"
    hits. Verified against libass's own fontselect output for every shipped face.
    """
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(path, fontNumber=0, lazy=True)
        family = f["name"].getDebugName(1)
        subfamily = (f["name"].getDebugName(2) or "Regular").strip()
        if not family:
            raise ValueError("no name record 1")
        family = family.strip()
        if subfamily.lower() != "regular" and \
                not family.lower().endswith(subfamily.lower()):
            family = f"{family} {subfamily}"
        return family
    except Exception:
        pass
    try:
        from PIL import ImageFont
        return ImageFont.truetype(path, 12).getname()[0]
    except Exception:
        return None


def _has_devanagari(path: str) -> bool:
    """Whether this face can actually draw Hindi, rather than tofu boxes."""
    try:
        from fontTools.ttLib import TTFont          # optional, usually absent
        f = TTFont(path, fontNumber=0, lazy=True)
        return any(0x0915 in t.cmap for t in f["cmap"].tables if t.isUnicode())
    except Exception:
        pass
    try:
        from PIL import ImageFont
        return ImageFont.truetype(path, 12).getmask("\u0915").getbbox() is not None
    except Exception:
        return False


def _discover_fonts() -> dict:
    """Every font in assets/fonts, keyed by a slug derived from its filename.

    Dropping a .ttf into the folder is all it takes to offer it -- there is no
    second list to keep in sync, which is exactly how the old hardcoded map
    ended up hiding eight installed faces.
    """
    found = {}
    if not _os.path.isdir(FONTS_DIR):
        return found
    for name in sorted(_os.listdir(FONTS_DIR)):
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        path = _os.path.join(FONTS_DIR, name)
        stem = _os.path.splitext(name)[0]
        # "Baloo2-VariableFont_wght" -> "baloo2";  "LilitaOne-Regular" -> "lilitaone"
        slug = stem.split("-")[0].split("_")[0].lower()
        family = _family_of(path)
        if not family:
            continue
        label = _re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem.split("-")[0])
        found[slug] = {
            "family": family,
            "label": label,
            "file": name,
            "devanagari": _has_devanagari(path),
        }
    return found


FONTS = {
    # The system face the classic style was designed around; not a shipped file.
    "impact": {"family": "Arial Black", "label": "Impact",
               "file": None, "devanagari": False},
}
FONTS.update(_discover_fonts())

# Slugs that older saved recipes used, before the registry was derived from the
# filenames on disk. A stored clip must keep rendering in the font it was made
# with, so these map forward rather than silently falling back to the default.
FONT_ALIASES = {"archivo": "archivoblack"}


def font_entry(font_id: str) -> dict:
    return FONTS.get(font_id) or FONTS.get(FONT_ALIASES.get(font_id, ""), None)


def resolve_font(font_id: str) -> str:
    """Family name for a font id; None means keep the style's own font."""
    entry = font_entry(font_id)
    return entry["family"] if entry else None
MAX_CUE_SECONDS = 1.8          # ...and swap them often enough to feel alive
MIN_WORD_SECONDS = 0.12        # floor so very fast words still register

# --- look ---------------------------------------------------------------------
# ASS colours are &HAABBGGRR -- byte order is reversed from hex you'd write in CSS.
# Each preset controls the base text, the spoken-word highlight, casing, and how
# hard the active word pops (fscx/fscy percentage).
# Each style varies on four independent axes, not just size:
#   position   where the caption block sits (bottom / middle / lower third)
#   box        "none" = outlined text, "word" = filled chip behind the spoken
#              word, "line" = a solid plate behind the whole caption
#   active     how the spoken word is marked (colour, and optionally scale)
#   uppercase  casing
# BorderStyle 3 is what makes a box possible at all: it renders the outline as
# a filled rectangle using OutlineColour, so an alpha-FF outline on the base
# style plus a per-word override gives a chip behind only the active word.
STYLES = {
    "classic": {
        "font": "Arial Black", "size": 76,
        "base": "&H00FFFFFF", "active": "&H004EF2D8",       # lime accent
        "outline": 5, "uppercase": False, "active_scale": 100,
        "position": "bottom", "box": "none", "keyword": "&H0000A5FF",
    },
    "fitbox": {
        # White text, blue chip travelling under the spoken word.
        "font": "Arial Black", "size": 66,
        "base": "&H00FFFFFF", "active": "&H00FFFFFF",
        "outline": 14, "uppercase": False, "active_scale": 100,
        "position": "bottom", "box": "word", "box_color": "&H00F66C2B",
        # amber, deliberately unlike the blue chip so the two signals stay separate
        "keyword": "&H0000A5FF",
    },
    "tech": {
        # Sits across the middle of the frame, no plate, soft cyan highlight.
        "font": "Arial Black", "size": 80,
        "base": "&H00FFFFFF", "active": "&H00FFE9BF",
        "outline": 4, "uppercase": False, "active_scale": 100,
        "position": "middle", "box": "none", "keyword": "&H00FFC34F",
    },
    "business": {
        # Dark text on a solid light plate -- the clean corporate look.
        "font": "Arial Black", "size": 58,
        "base": "&H00807A72", "active": "&H001E1A1A",
        "outline": 18, "uppercase": False, "active_scale": 100,
        "position": "lower", "box": "line", "box_color": "&H00F5F3EF",
        "keyword": "&H002020C8",
    },
    "gameplay": {
        # Loud caps for split-screen, where the caption competes with motion.
        "font": "Arial Black", "size": 84,
        "base": "&H00FFFFFF", "active": "&H003AD43A",
        "outline": 6, "uppercase": True, "active_scale": 118,
        "position": "bottom", "box": "none", "keyword": "&H0000D4FF",
    },
}

# Alignment codes: 2 = bottom-centre, 5 = middle-centre.
POSITIONS = {
    "bottom": {"alignment": 2},
    "lower": {"alignment": 2, "margin_frac": 0.13},
    "middle": {"alignment": 5},
}

COLOR_OUTLINE = "&H00000000"    # black
BOX_TRANSPARENT = "&HFF000000"  # alpha FF = fully transparent box

PLAY_RES_X = 1080
PLAY_RES_Y = 1920

FILLERS = {"um", "uh", "eh", "mm", "hmm", "erm", "ah"}


def _ass_time(seconds: float) -> str:
    """ASS uses H:MM:SS.cc (centiseconds, single-digit hour)."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    """Braces open override blocks in ASS, and a trailing backslash would escape."""
    return text.replace("\\", "∖").replace("{", "(").replace("}", ")")


def _word_text(word: dict) -> str:
    return word.get("punctuated_word") or word.get("word") or ""


def group_words(words: list) -> list:
    """Chunks words into cues, breaking on length, duration, or sentence end."""
    cues = []
    current = []

    for word in words:
        current.append(word)
        text = _word_text(word)
        span = current[-1]["end"] - current[0]["start"]

        ends_sentence = text.endswith((".", "!", "?"))
        if len(current) >= MAX_WORDS_PER_CUE or span >= MAX_CUE_SECONDS or ends_sentence:
            cues.append(current)
            current = []

    if current:
        cues.append(current)
    return cues


def _header(margin_v: int, st: dict, play_res: tuple,
            title_style: str = None, title_font: str = None) -> str:
    res_x, res_y = play_res
    ts = title_look(title_style, title_font)
    pos = POSITIONS.get(st.get("position", "bottom"), POSITIONS["bottom"])
    alignment = pos["alignment"]
    if "margin_frac" in pos:
        margin_v = int(res_y * pos["margin_frac"])

    box = st.get("box", "none")
    if box == "none":
        border_style, outline_colour = 1, COLOR_OUTLINE
    else:
        # BorderStyle 3 draws OutlineColour as a filled box behind the text.
        # For a per-word chip the base box is fully transparent (alpha FF) and
        # only the active word overrides it back to opaque.
        border_style = 3
        outline_colour = st["box_color"] if box == "line" else BOX_TRANSPARENT

    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{st["font"]},{st["size"]},{st["base"]},{st["base"]},{outline_colour},&H00000000,-1,0,0,0,100,100,0,0,{border_style},{st["outline"]},0,{alignment},60,60,{margin_v},1
Style: Title,{ts["font"]},{ts["size"]},{ts["colour"]},{ts["colour"]},{ts["edge"]},&H00000000,-1,0,0,0,100,100,0,0,{ts["border_style"]},{ts["outline"]},{ts["shadow"]},8,90,90,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# The title must not read as "another caption". It was previously 54px while
# captions run 58-84px, so it was literally the smallest text on screen -- which
# is why it neither caught the eye nor announced what the clip was about. It now
# sits clearly above every caption size, in a heavier plate, and holds longer.
TITLE_STYLE = {
    "font": "Arial Black", "size": 92,
    "colour": "&H00101010",         # near-black text
    "plate": "&H00FFFFFF",          # on a white plate
    "outline": 24,                  # plate padding, via BorderStyle 3
    "seconds": 4.5,                 # long enough to actually be read
    "top_frac": 0.07,               # distance from the top of the frame
}


# A title is a design decision, not a fixed asset. The white plate reads as
# "documentary caption" and is wrong for a gaming or meme clip, so the plate is
# now one option among several rather than the only thing on offer.
#
# BorderStyle 3 fills OutlineColour as a box behind the glyphs (the plate looks);
# BorderStyle 1 strokes it as an edge (the outline looks). `shadow` is the ASS
# drop-shadow distance in px.
TITLE_LOOKS = {
    "plate": {                       # white block, near-black text -- the default
        "colour": "&H00101010", "edge": "&H00FFFFFF",
        "border_style": 3, "outline": 24, "shadow": 0,
    },
    "ink": {                         # inverted plate: dark block, white text
        "colour": "&H00FFFFFF", "edge": "&H00161212",
        "border_style": 3, "outline": 24, "shadow": 0,
    },
    "outline": {                     # white text with a hard black stroke, no box
        "colour": "&H00FFFFFF", "edge": "&H00000000",
        "border_style": 1, "outline": 7, "shadow": 0,
    },
    "shadow": {                      # white text on a soft drop shadow
        "colour": "&H00FFFFFF", "edge": "&H00000000",
        "border_style": 1, "outline": 2, "shadow": 5,
    },
    "lime": {                        # highlighter block, the meme/gaming look
        "colour": "&H00101010", "edge": "&H004EF2D8",
        "border_style": 3, "outline": 24, "shadow": 0,
    },
    "clean": {                       # bare white text, nothing behind it
        "colour": "&H00FFFFFF", "edge": "&H00000000",
        "border_style": 1, "outline": 0, "shadow": 0,
    },
}
DEFAULT_TITLE_LOOK = "plate"


def title_look(name: str = None, font: str = None) -> dict:
    """Resolve a title look, with the font overridable independently.

    Look and typeface are separate choices: the same white plate reads very
    differently in Anton than in Merriweather, and forcing them to move together
    would collapse the useful combinations.
    """
    look = TITLE_LOOKS.get(name or DEFAULT_TITLE_LOOK, TITLE_LOOKS[DEFAULT_TITLE_LOOK])
    return {
        **look,
        "font": resolve_font(font) or TITLE_STYLE["font"],
        "size": TITLE_STYLE["size"],
    }


def _title_events(title: str, play_res: tuple) -> str:
    """A title card pinned near the top for the opening seconds.

    Reference clips from finished tools nearly always open with one: it gives
    the viewer the premise before the speaker gets there, which is most of why
    they feel edited rather than merely cut.
    """
    if not title:
        return ""
    res_y = play_res[1]
    margin = int(res_y * TITLE_STYLE["top_frac"])
    end = _ass_time(TITLE_STYLE["seconds"])
    text = _escape(title.strip())
    # \an8 = top-centre, independent of the caption style's own alignment.
    return (f"Dialogue: 0,0:00:00.00,{end},Title,,0,0,{margin},,"
            f"{{\\an8}}{text}\n")


def build_ass(words: list, clip_start_time: float, out_path: str,
              margin_v: int = 150, style: str = "classic",
              play_res: tuple = (PLAY_RES_X, PLAY_RES_Y),
              title: str = "", keywords: list = None,
              font: str = None, size_px: int = None,
              animation: str = None,
              title_style: str = None, title_font: str = None) -> str:
    """Writes an ASS file whose timings are relative to the clip's own start.

    words:           Deepgram word objects with absolute 'start'/'end' seconds
    clip_start_time: where this clip begins in the source video
    margin_v:        distance in real pixels from the bottom of the 1920px frame
                     to the bottom of the caption block
    style:           key into STYLES; unknown names fall back to classic
    """
    st = STYLES.get(style, STYLES["classic"])
    family = resolve_font(font)
    if family:
        st = {**st, "font": family}
    # Absolute px against the 1080x1920 reference canvas. None means "whatever
    # this caption style was designed at", which is what most people want.
    if size_px:
        st = {**st, "size": max(MIN_CAPTION_PX, min(MAX_CAPTION_PX, int(size_px)))}
    lines = [_header(margin_v, st, play_res, title_style, title_font)]
    lines.append(_title_events(title, play_res))

    # Keywords stay coloured for the whole cue, independent of the karaoke
    # highlight. Reference clips from finished tools use this to make the point
    # of a line readable at a glance, even when paused mid-scroll.
    key_set = {k.strip().lower() for k in (keywords or []) if k and k.strip()}

    for cue in group_words(words):
        rendered = [_escape(_word_text(w)) for w in cue]
        if st["uppercase"]:
            rendered = [t.upper() for t in rendered]

        for i, word in enumerate(cue):
            start = word["start"] - clip_start_time
            # Hold the highlight until the next word actually begins, so there is
            # never a gap where nothing is lit.
            if i + 1 < len(cue):
                end = cue[i + 1]["start"] - clip_start_time
            else:
                end = word["end"] - clip_start_time
            end = max(end, start + MIN_WORD_SECONDS)

            if end <= 0:
                continue

            parts = []
            for j, token in enumerate(rendered):
                bare = _word_text(cue[j]).strip(".,!?;:\u0964").lower()
                if j == i:
                    # Inline overrides need BOTH the &H prefix and a trailing &.
                    # Without the trailing &, libass mis-parses the tag and leaks
                    # stray punctuation into the rendered line. \r resets every
                    # override back to the style in one tag.
                    scale = (f"\\fscx{st['active_scale']}\\fscy{st['active_scale']}"
                             if st["active_scale"] != 100 else "")
                    # A "word" box paints the chip by turning the transparent
                    # base outline opaque for this word only.
                    chip = (f"\\3c{st['box_color']}&\\3a&H00&"
                            if st.get("box") == "word" else "")
                    parts.append(f"{{\\c{st['active']}&{scale}{chip}}}{token}{{\\r}}")
                elif bare and bare in key_set:
                    parts.append(f"{{\\c{st.get('keyword', st['active'])}&}}{token}{{\\r}}")
                else:
                    parts.append(token)
            text = " ".join(parts)

            # Animate only the FIRST word event of a cue. Re-triggering the
            # entrance on every word would make the line jitter continuously
            # instead of arriving once.
            if animation and animation != "none" and i == 0:
                cue_ms = int(max(0.0, (cue[-1]["end"] - cue[0]["start"])) * 1000)
                tags = animation_tags(animation, cue_ms, play_res, margin_v)
                if tags:
                    text = "{" + tags + "}" + text

            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{text}"
            )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def first_meaningful_word_time(words: list, limit: int = 4) -> float:
    """Start time of the first non-filler word, used to trim weak clip openings."""
    for word in words[:limit]:
        token = _word_text(word).strip().lower().strip(".,!?")
        if token and token not in FILLERS:
            return word["start"]
    return words[0]["start"] if words else 0.0


def build_ass_lines(caption_lines: list, out_path: str,
                    margin_v: int = 150, style: str = "classic",
                    play_res: tuple = (PLAY_RES_X, PLAY_RES_Y),
                    title: str = "", font: str = None, size_px: int = None,
                    animation: str = None,
                    title_style: str = None, title_font: str = None) -> str:
    """Whole-line captions with no karaoke, for translated subtitles.

    A translation has different words with different lengths from the speech,
    so per-word highlighting would be lying about timing. Whole lines timed to
    the original utterance spans stay honest: the line appears exactly while
    that sentence is being said. Times here are already clip-relative.
    """
    st = STYLES.get(style, STYLES["classic"])
    family = resolve_font(font)
    if family:
        st = {**st, "font": family}
    if size_px:
        st = {**st, "size": max(MIN_CAPTION_PX, min(MAX_CAPTION_PX, int(size_px)))}

    out = [_header(margin_v, st, play_res, title_style, title_font),
           _title_events(title, play_res)]
    for line in caption_lines:
        text = _escape((line.get("text") or "").strip())
        if not text:
            continue
        if st["uppercase"]:
            text = text.upper()
        start, end = float(line["start"]), float(line["end"])
        if end <= start:
            continue
        colour = f"\\c{st['active']}" if st.get("box") == "none" else ""
        # One cue per line here (no karaoke), so the entrance animation plays
        # once per sentence rather than once per word -- the same tags, applied
        # at the only granularity a translated line has.
        anim = ""
        if animation and animation != "none":
            anim = animation_tags(animation, int((end - start) * 1000),
                                  play_res, margin_v)
        # animation_tags returns BARE tags; unbraced they render as literal
        # text, so colour and animation share one override block.
        override = f"{{{anim}{colour}}}" if (anim or colour) else ""
        out.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{override}{text}"
        )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return out_path
