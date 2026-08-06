"""Chooses emoji for caption cues, using the model to judge relevance.

Emoji are decoration. Decoration must never be why a paid job fails, so every
error path here returns "no emoji" and lets the clip render plain -- a missing
emoji is invisible to the user, while a failed render costs them credits and a
video.

Two things make the difference between this looking authored and looking
automated:

  DENSITY  An emoji on every cue reads as noise. The model is told to mark only
           cues that genuinely earn one, and the result is capped afterwards
           regardless of what came back.
  DRAWABILITY  A font can map a codepoint and still draw nothing -- see the note
           in subtitles._covers, which is the trap that once made the editor
           promise emoji the export silently dropped. Every suggestion is
           checked against the shipped face before it is used.
"""

import os
import re

from app.pipeline import subtitles
from app.pipeline.analyzer import _openai_chat

# Roughly one cue in three. Past that the emoji stop being emphasis and start
# being wallpaper -- and the eye has nowhere to land.
MAX_DENSITY = 0.34

# Skin-tone modifiers and ZWJ sequences (families, professions, couples) are the
# ones that fall apart: a font either lacks the composed glyph and draws the
# pieces separately, or draws nothing. Single codepoints are what survive.
_ZWJ = "‍"
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_VARIATION_SELECTOR = 0xFE0F

_PROMPT = """You are adding emoji to captions for a short-form video clip.

Below are the numbered caption cues, in order. Pick the cues where ONE emoji
genuinely reinforces what is being said -- a concrete object, a strong emotion,
a number, a reaction. Skip cues that are connective tissue or filler, and skip
cues that are already vivid without help.

Rules:
- Mark at most {budget} cues. Fewer is better than forcing them.
- Exactly one emoji per marked cue, never two.
- Single common emoji only. No flags, no skin-tone modifiers, and no combined
  sequences such as families or professions -- they do not render reliably.
- If a cue does not clearly call for an emoji, leave it out entirely.

Cues:
{cues}

Reply with JSON only, in this exact shape:
{{"emoji": {{"3": "\U0001f525", "7": "\U0001f4b0"}}}}
"""


def _is_simple_emoji(text: str) -> bool:
    """One drawable pictograph, not a composed sequence."""
    if not text or _ZWJ in text:
        return False
    points = [ord(c) for c in text if ord(c) != _VARIATION_SELECTOR]
    if len(points) != 1:
        return False
    point = points[0]
    if point in _SKIN_TONES:
        return False
    # The ranges the caption renderer already recognises as emoji; anything
    # outside them would not be routed to the emoji face downstream anyway.
    return (0x1F300 <= point <= 0x1FAFF or 0x2600 <= point <= 0x27BF
            or 0x1F000 <= point <= 0x1F2FF or point in (0x2B50, 0x2B55))


def _drawable(text: str, face: dict) -> bool:
    """Whether the shipped emoji face can actually put this on screen.

    A cmap hit is not enough. subtitles._covers is the honest test and is reused
    rather than reimplemented, because the subtle half of it -- COLRv1 and sbix
    glyphs that exist but rasterise to nothing -- is exactly what this guard is
    for.
    """
    path = os.path.join(subtitles.FONTS_DIR, face["file"])
    return all(subtitles._covers(path, ord(c)) for c in text
               if ord(c) != _VARIATION_SELECTOR)


def _cue_text(cue: list) -> str:
    return " ".join(
        (w.get("punctuated_word") or w.get("word") or "") for w in cue
    ).strip()


def plan(words: list, style: str = "classic") -> dict:
    """{cue index: emoji} for the cues worth marking. Empty dict on any problem.

    Cues are grouped here with the same rule build_ass uses, so the indices this
    returns line up with the ones it iterates. Passing the map in -- rather than
    calling the model from inside the renderer -- keeps build_ass free of
    network calls and testable without one.
    """
    face = subtitles.emoji_font()
    if not face:
        # No face can draw emoji, so skip the API call entirely rather than pay
        # for suggestions that would all be discarded.
        return {}

    max_words = subtitles.STYLES.get(
        style, subtitles.STYLES["classic"]).get("max_words")
    cues = list(subtitles.group_words(words, max_words))
    texts = [_cue_text(c) for c in cues]
    if not any(texts):
        return {}

    budget = max(1, int(len(cues) * MAX_DENSITY))
    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(texts) if t)

    try:
        reply = _openai_chat(
            _PROMPT.format(budget=budget, cues=listing), temperature=0.3)
    except (RuntimeError, ValueError) as exc:
        print(f"[clipper] emoji suggestions unavailable, captions will render "
              f"without them: {exc}")
        return {}

    raw = reply.get("emoji") if isinstance(reply, dict) else None
    if not isinstance(raw, dict):
        return {}

    chosen = {}
    for key, mark in raw.items():
        if len(chosen) >= budget:
            break
        try:
            index = int(str(key).strip().rstrip("."))
        except ValueError:
            continue
        if not (0 <= index < len(cues)) or index in chosen:
            continue
        mark = (mark or "").strip()
        if _is_simple_emoji(mark) and _drawable(mark, face):
            chosen[index] = mark

    if chosen:
        print(f"[clipper] emoji on {len(chosen)} of {len(cues)} cues")
    return chosen
