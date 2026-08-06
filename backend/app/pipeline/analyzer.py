"""Picks the best clip-worthy moments.

Grounding
---------
The LLM never outputs raw timestamps. Timestamps guessed by a model reading a
wall of text land mid-sentence, because the model is approximating rather than
measuring. Instead it chooses *utterance index ranges* from a numbered list, and
the real start/end times are looked up from Deepgram's word-level data. A clip
therefore can never begin or end mid-word.

Why this is windowed and scored
-------------------------------
Asking one prompt to read an hour-long transcript and return "the 3 best bits"
produces skim-quality answers: the model anchors on whatever appeared early and
never really weighs the back half. So instead:

  1. The transcript is split into overlapping windows small enough to read
     closely. Overlap means a strong moment straddling a boundary still gets
     seen whole by one of the windows.
  2. Each window nominates candidates and scores them against an explicit
     rubric, so ranking is comparable across windows rather than vibes.
  3. Candidates are pooled, ranked globally, and greedily de-overlapped.
  4. Weak openings ("so...", "um...") are trimmed off the front.

Clip length is never targeted or corrected. A moment runs as long as it needs to
feel complete, so durations vary widely by design.

That last step matters more than it sounds: a clip whose first second is filler
loses the viewer before the hook lands.
"""

from app import env  # noqa: F401  -- ensures .env is loaded

import json
import os
import re

import requests

from app.pipeline import energy

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
# Editorial judgement is the whole job here, so this defaults to a full-size
# model rather than a mini one. Override if you want to trade quality for cost.
OPENAI_MODEL = os.environ.get("CLIPPER_MODEL", "gpt-4o")

# How many selection passes to run, so the three can be A/B'd on the same video
# without a code change:
#   1 = nominate per window and take the top scorers (fastest, fewest API calls)
#   2 = + compare the finalists against each other
#   3 = + refine each clip's in/out points  (default)
# Parsed defensively: this runs at import, so a typo in the env must not take
# the whole server down on startup.
def _selection_passes() -> int:
    try:
        return max(1, min(3, int(os.environ.get("CLIPPER_PASSES", "3"))))
    except (TypeError, ValueError):
        return 3


SELECTION_PASSES = _selection_passes()

WINDOW_SIZE = 110              # utterances per window -- small enough to read closely
WINDOW_OVERLAP = 20            # so moments spanning a boundary survive intact
# High enough that small moments (a joke, a one-liner) get nominated alongside
# the obvious big stories instead of being crowded out by them.
CANDIDATES_PER_WINDOW = 6

# There is deliberately no target duration. A clip should run exactly as long as
# the moment needs to feel complete -- stretching a tight moment to hit a minimum
# pads it, and cutting a longer one to hit a maximum truncates the payoff. Length
# is an outcome of where the moment genuinely starts and ends, not an input.
#
# These two bounds are sanity guards only, not targets: below the floor a "clip"
# is a fragment rather than a moment, and above the ceiling something has gone
# wrong with index selection. Nothing is ever padded or trimmed to reach them.
MIN_USABLE_SECONDS = 8.0
MAX_SANE_SECONDS = 300.0

# Virality rubric. These are the levers that actually decide whether a short
# gets watched and reshared, rather than generic "is this good content":
#   hook        the first 2 seconds; nothing else matters if this fails
#   standalone  incomprehensible clips are worthless however punchy
#   emotion     surprise/outrage/delight/awe -- flat clips do not travel
#   ending      drifting past the punchline kills the replay and the share
#   payoff      the hook's promise is actually delivered
#   share       would someone send this to a friend or argue about it
WEIGHTS = {
    "hook": 0.26, "standalone": 0.20, "emotion": 0.18,
    "ending": 0.16, "payoff": 0.12, "share": 0.08,
}

FILLER_OPENERS = {
    "so", "and", "but", "um", "uh", "like", "yeah", "okay", "ok", "well",
    "right", "you know", "i mean", "anyway", "then",
}


# Some newer models (gpt-5.x and the reasoning line) only accept the default
# temperature. Rather than hardcode a list that goes stale, we discover it once
# per process from the API's own error and stop sending the parameter.
_MODEL_REJECTS_TEMPERATURE = set()


def _openai_chat(prompt: str, retries: int = 2, temperature: float = 0.4) -> dict:
    last_error = None
    for attempt in range(retries + 1):
        try:
            payload = {
                "model": OPENAI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
            if OPENAI_MODEL not in _MODEL_REJECTS_TEMPERATURE:
                payload["temperature"] = temperature

            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json=payload,
                timeout=180,
            )

            if resp.status_code == 400 and "temperature" in resp.text:
                # Learn it and retry immediately; costs one wasted call per process.
                _MODEL_REJECTS_TEMPERATURE.add(OPENAI_MODEL)
                print(f"[clipper] {OPENAI_MODEL} does not accept a custom "
                      f"temperature -- using the model default from here on.")
                continue
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    "OpenAI rejected the API key. Check OPENAI_API_KEY in "
                    "backend/.env."
                )
            resp.raise_for_status()
            return json.loads(resp.json()["choices"][0]["message"]["content"])
        except (requests.RequestException, ValueError, KeyError) as e:
            last_error = e
    raise RuntimeError(f"OpenAI request failed after {retries + 1} attempts: {last_error}")


def _windows(total: int) -> list:
    """Overlapping [start, end) index ranges covering all utterances."""
    if total <= WINDOW_SIZE:
        return [(0, total)]

    step = WINDOW_SIZE - WINDOW_OVERLAP
    spans = []
    start = 0
    while start < total:
        spans.append((start, min(start + WINDOW_SIZE, total)))
        if start + WINDOW_SIZE >= total:
            break
        start += step
    return spans


def _numbered_slice(utterances: list, lo: int, hi: int) -> str:
    return "\n".join(
        f"[{i}] ({utterances[i]['start']:.1f}-{utterances[i]['end']:.1f}) "
        f"{energy.summarize(utterances[i])}{utterances[i]['transcript']}"
        for i in range(lo, hi)
    )


def _duration(utterances: list, start_idx: int, end_idx: int) -> float:
    return utterances[end_idx]["end"] - utterances[start_idx]["start"]


PROMPT = """You are a short-form editor who has studied why clips go viral. You are
picking moments from a longer video.

Below is a NUMBERED transcript slice. Format: [index] (start-end) [prosody] text

The prosody tags are measured from the actual audio and tell you HOW a line was
delivered -- information the words alone hide:
  LOUD      shouted, excited, or laughing
  quiet     hushed -- often a confession or a serious beat
  dynamic   big volume swings inside the line (animated delivery)
  fast      rapid delivery -- excitement or a rant
  slow      deliberate -- weight, emphasis
  pause:Ns  silence before the line -- often a setup beat or a reveal

Untagged lines are ordinary delivery. LOUD/dynamic/fast clusters are where the
room came alive; that is usually where the clip is. But energy alone is not a
clip -- a loud line with no substance is still noise.

{intent}{length}Nominate up to {n} ranges. Hunt for ALL of these shapes, not just stories:
- a complete joke or funny exchange that lands on its punchline
- a sharp one-liner, roast, or genuine spontaneous reaction
- advice or an opinion delivered with real conviction
- a mini-story with a real ending
- a surprising fact, number, reveal, or admission

WHAT ACTUALLY TRAVELS. A clip gets watched and reshared when it has:
- a first line that creates an unresolved question in someone's head
- genuine emotion -- surprise, delight, outrage, awe. Flat clips do not travel
- a payoff that rewards the wait, landing on the strongest line
- a reason to send it to someone: it is funny, useful, or arguable

COMPLETENESS IS NON-NEGOTIABLE. Every clip must contain its own setup AND its
payoff. A punchline without the setup that makes it funny, a reaction without
the thing being reacted to, or advice without the problem it solves is a
fragment, not a clip -- never nominate it, no matter how punchy it sounds.
When in doubt, include one more sentence of setup rather than one less.

Let each moment run exactly as long as it needs. Do NOT aim for a duration.
A tight 15-second complete joke can outperform a 60-second story; a story that
needs 90 seconds to pay off should get all 90. Short is only good when COMPLETE.

Avoid: pure setup with no payoff, mid-topic fragments, rambling, admin chatter
("welcome back", "hit subscribe"), and anything needing prior context.

Score honestly from 1-10. Be discriminating -- most of a transcript is not
clip-worthy and 5 is a normal score. Reserve 9+ for moments you would bet money
on. If a window has nothing strong, return fewer candidates or an empty list.

Return ONLY valid JSON:
{{
  "candidates": [
    {{
      "start_index": <int, first utterance index, inclusive>,
      "end_index": <int, last utterance index, inclusive>,
      "title": "<punchy title, max 60 chars, no clickbait caps.

LANGUAGE RULE:
Write the title in the SAME language the speaker uses. An English clip gets a plain English title -- do not translate or transliterate it.

SCRIPT RULE (applies ONLY to languages written in a non-Latin script -- Hindi, Urdu, Punjabi, Marathi, Bhojpuri, etc.):
Romanise it. Hindi speech gets a HINGLISH title: Hindi words spelled phonetically in English letters, English words left as English. Write 'Brake fail hua', NOT 'ब्रेक फेल हुआ'. More examples: 'Paisa hi sab kuch hai', 'Shaadi ab ek business hai', 'Economy ne sab barbaad kar diya'. Indian audiences scan Roman script instantly and skip Devanagari in a feed.
Spell it how people type, not by transliteration standard: 'hai' not 'haiṁ', 'kya' not 'kyaa', 'nahi' not 'nahii'.

If the speech is already in a Latin-script language (English, Spanish, French, Portuguese, German, Indonesian), just write a normal title in that language and ignore the script rule entirely.

This title is burned into the video and used as the download filename, so it must read like a real creator wrote it, not like a translation>",
      "hook": "<one sentence in English: why this grabs someone in the first 2 seconds>",
      "intent_match": <true only when this directly matches the user's optional request; false when there is no request>,
      "scores": {{
        "hook": <1-10, does the first line create an unresolved question>,
        "standalone": <1-10, fully understandable with zero context>,
        "emotion": <1-10, real emotional charge -- funny, shocking, moving>,
        "ending": <1-10, lands on its strongest line, nothing trails after>,
        "payoff": <1-10, the hook's promise is delivered>,
        "share": <1-10, would someone send this to a friend or argue about it>
      }}
    }}
  ]
}}

Transcript:
{transcript}
"""

# Advisory only -- phrased as a preference, never a hard filter, so a great
# moment outside the requested band still wins over a padded one inside it.
LENGTH_PREFS = {
    "any": "",
    "short": "Prefer moments that are complete in under 30 seconds.",
    "medium": "Prefer moments that run roughly 30-60 seconds.",
    "long": "Prefer longer moments, roughly 60-90 seconds, with room to develop.",
    "extended": "Prefer extended moments over 90 seconds -- full stories or arguments.",
}

# A specific request is a priority lane, not a filter. A multi-clip result still
# needs the video's strongest general moments.
INTENT_TEMPLATE = """WHAT THIS USER IS LOOKING FOR: {intent}
Nominate strong direct matches, and mark those candidates with intent_match=true.
Also nominate the strongest unrelated moment in this section when one exists.
Never nominate a weak or incomplete match just to satisfy the request.

"""

LENGTH_TEMPLATE = """LENGTH PREFERENCE: {preference}
This is advisory only. A stronger complete moment outside the range still wins.

"""


def _collect_candidates(utterances: list, on_progress=None, intent: str = "",
                        length_guidance: str = "") -> list:
    spans = _windows(len(utterances))
    candidates = []
    intent_block = INTENT_TEMPLATE.format(intent=intent) if intent else ""
    length_block = (LENGTH_TEMPLATE.format(preference=length_guidance)
                    if length_guidance else "")

    for n, (lo, hi) in enumerate(spans, start=1):
        if on_progress:
            on_progress(n, len(spans))

        data = _openai_chat(PROMPT.format(
            n=CANDIDATES_PER_WINDOW,
            intent=intent_block,
            length=length_block,
            transcript=_numbered_slice(utterances, lo, hi),
        ))

        for c in data.get("candidates", []):
            try:
                start_idx = int(c["start_index"])
                end_idx = int(c["end_index"])
            except (KeyError, TypeError, ValueError):
                continue

            # Keep the model honest: clamp to this window and to reality.
            start_idx = max(lo, min(start_idx, hi - 1))
            end_idx = max(start_idx, min(end_idx, hi - 1))

            scores = c.get("scores") or {}
            total = sum(
                WEIGHTS[k] * float(scores.get(k, 5) or 5) for k in WEIGHTS
            )

            candidates.append({
                "start_index": start_idx,
                "end_index": end_idx,
                "title": (c.get("title") or "Untitled moment").strip()[:80],
                "hook": (c.get("hook") or "").strip(),
                "intent_match": bool(intent) and c.get("intent_match") is True,
                "scores": scores,
                "score": round(total, 2),
            })

    return candidates


# A clip assembled without the model's help aims for this much material. Long
# enough to carry a complete thought, short enough that a sparse video can
# actually produce one.
FALLBACK_TARGET_SECONDS = 30.0

# A silence this long is a scene change, not a breath. Never build a fallback
# clip across one: the two sides are unrelated, and stitching them produces a
# clip that jumps mid-thought.
FALLBACK_MAX_GAP_SECONDS = 6.0


def _fallback_candidates(utterances: list) -> list:
    """Mechanically built candidates, for when the model nominates nothing.

    The nomination prompt is deliberately strict -- it is told to be
    discriminating and to skip intro chatter -- which is right for a rich
    podcast and wrong as a last word. On a sparse video (a short vlog, a mostly
    silent gameplay recording, a talk with long pauses) it can legitimately
    return nothing, and the pipeline then failed the whole job with "nothing
    scored well enough". The user is left with no clips, having already paid for
    transcription.

    A worse clip is worth more than no clip. So when the model declines, group
    consecutive speech into runs of roughly FALLBACK_TARGET_SECONDS, never
    bridging a long silence, and let the normal comparison and refinement passes
    sort them out.

    Scored at the bottom of the scale on purpose: these must never outrank a
    real nomination if any exist.
    """
    spans = []
    start = 0
    while start < len(utterances):
        end = start
        while end + 1 < len(utterances):
            gap = utterances[end + 1]["start"] - utterances[end]["end"]
            if gap > FALLBACK_MAX_GAP_SECONDS:
                break                       # a scene change, not a breath
            if _duration(utterances, start, end) >= FALLBACK_TARGET_SECONDS:
                break
            end += 1

        if _duration(utterances, start, end) >= MIN_USABLE_SECONDS:
            spans.append((start, end))
        start = end + 1

    return [{
        "start_index": lo,
        "end_index": hi,
        "title": (utterances[lo].get("transcript") or "Moment").strip()[:80],
        "hook": "",
        "intent_match": False,
        "scores": {},
        # Below any real nomination's floor, so this can only ever be a
        # last resort rather than competition for a genuine pick.
        "score": 1.0,
        "fallback": True,
    } for lo, hi in spans]


def _measure(utterances: list, clip: dict) -> dict:
    """Attaches the clip's real duration. Deliberately does not alter the range.

    This used to extend or trim end_index to force the clip into a target band.
    That fought the whole point of choosing boundaries by meaning: it padded
    tight moments with whatever came next, and cut the resolution off long ones.
    """
    duration = _duration(utterances, clip["start_index"], clip["end_index"])
    return {**clip, "duration": round(duration, 1)}


def _overlaps(a: dict, b: dict) -> bool:
    return a["start_index"] <= b["end_index"] and b["start_index"] <= a["end_index"]


# --- pass 2: comparative selection ------------------------------------------
# Window scores are not calibrated against each other. A window of mediocre
# material still rates its best moment an 8, while a genuinely strong window
# rates three things an 8. Ranking on those numbers alone therefore mixes
# "best of a weak stretch" in with real highlights. This pass puts the finalists
# side by side so they are judged against each other rather than against their
# neighbours.

SHORTLIST_MULTIPLIER = 3
# Floor on how many finalists reach the head-to-head pass. Without it, asking for
# 1 clip compared only 3 candidates -- the opposite of what "give me your single
# best moment" should do. Wanting fewer clips means the bar is HIGHER, so the
# comparison pool must be deeper, not shallower.
MIN_SHORTLIST = 10

SELECT_PROMPT = """You are the final editor choosing which clips actually ship.

Below are candidate moments already shortlisted from a longer video, each with
its full text. They were scored in isolation, so their numbers are NOT
comparable -- judge them against each other now, by reading them.

{intent}Pick the {n} strongest as standalone short-form videos. Compare directly: if two
make a similar point, keep only the better-told one. Prefer a moment that earns
its length over one padded to fill time, and prefer a concrete story or a sharp
claim over a general observation.

Reject fragments outright: if a candidate cannot be fully understood by someone
who has never seen the video, it does not ship, regardless of its scores.

Judge every candidate by how it ENDS as much as how it opens -- a clip whose
last line is its punchline beats one that trails off after the point landed.
When quality is close, prefer a varied set: a short punchy moment next to a
longer story serves a viewer better than {n} clips of the same shape. Never
pick a weaker clip just for variety, and do not penalize two strong moments for
being adjacent in the video -- back-to-back gems are both worth shipping.

Return ONLY valid JSON:
{{
  "chosen": [
    {{
      "id": <int, the candidate id>,
      "title": "<punchy title, max 60 chars. WRITE IT IN THE SAME LANGUAGE AND SCRIPT THE SPEAKER USES -- this is burned into the video and becomes the download filename, so it must read like the creator wrote it>",
      "hook": "<one sentence in English: what makes someone stop scrolling>",
      "why_over_others": "<one sentence: why this beat the ones you dropped>",
      "keywords": ["<2-4 words spoken IN the clip that carry its meaning -- names, numbers, the punchline word. Exact words as spoken, no paraphrasing>"]
    }}
  ]
}}

Candidates:
{candidates}
"""

SELECT_INTENT_TEMPLATE = """The creator asked for: {intent}
When a strong labelled match exists, include one. Fill the remaining places with
the strongest moments overall. Do not make the whole batch about the request
unless those moments genuinely beat the general candidates.

"""


def _candidate_text(utterances: list, clip: dict, max_chars: int = 900) -> str:
    text = " ".join(
        utterances[i]["transcript"]
        for i in range(clip["start_index"], clip["end_index"] + 1)
    )
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _balance_intent_selection(chosen: list, shortlist: list, n_clips: int,
                              intent: str) -> list:
    """Keep one strong requested moment without turning the request into a filter."""
    if not intent or n_clips < 2 or not chosen:
        return chosen

    selected = list(chosen)
    selected_ranges = {(c["start_index"], c["end_index"]) for c in selected}
    matches = [c for c in shortlist if c.get("intent_match")]
    general = [c for c in shortlist if not c.get("intent_match")]

    if matches and not any(c.get("intent_match") for c in selected):
        selected[-1] = matches[0]
        selected_ranges = {(c["start_index"], c["end_index"]) for c in selected}

    selected_matches = sorted(
        [c for c in selected if c.get("intent_match")],
        key=lambda c: c["score"], reverse=True,
    )
    available_general = [
        c for c in general
        if (c["start_index"], c["end_index"]) not in selected_ranges
    ]
    for extra in selected_matches[1:]:
        if not available_general or extra["score"] > available_general[0]["score"] + 1:
            continue
        selected[selected.index(extra)] = available_general.pop(0)

    return selected[:n_clips]


def _final_selection(utterances: list, shortlist: list, n_clips: int,
                     intent: str = "") -> list:
    """Head-to-head comparison of finalists. Falls back to score order."""
    if len(shortlist) <= n_clips:
        return shortlist

    blocks = "\n\n".join(
        f"[id {n}] ({clip['duration']:.0f}s)"
        f"{' [matches request]' if clip.get('intent_match') else ''}\n"
        f"{_candidate_text(utterances, clip)}"
        for n, clip in enumerate(shortlist)
    )

    try:
        intent_block = SELECT_INTENT_TEMPLATE.format(intent=intent) if intent else ""
        data = _openai_chat(SELECT_PROMPT.format(
            n=n_clips, intent=intent_block, candidates=blocks))
        chosen = []
        for entry in data.get("chosen", []):
            idx = int(entry["id"])
            if not 0 <= idx < len(shortlist) or any(c["_id"] == idx for c in chosen):
                continue
            clip = {
                **shortlist[idx],
                "_id": idx,
                "title": (entry.get("title") or shortlist[idx]["title"]).strip()[:80],
                "hook": (entry.get("hook") or shortlist[idx]["hook"]).strip(),
                "why_over_others": (entry.get("why_over_others") or "").strip(),
                "keywords": [str(k) for k in (entry.get("keywords") or [])][:4],
            }
            chosen.append(clip)
            if len(chosen) >= n_clips:
                break
        if chosen:
            clean = [{k: v for k, v in c.items() if k != "_id"} for c in chosen]
            return _balance_intent_selection(clean, shortlist, n_clips, intent)
    except (RuntimeError, KeyError, TypeError, ValueError):
        pass

    return _balance_intent_selection(shortlist[:n_clips], shortlist, n_clips, intent)


# --- pass 3: boundary refinement --------------------------------------------
# The window passes work at utterance granularity, and utterances are often
# 10+ seconds with a transition welded onto the end of the same utterance as the
# payoff ("...and that's why it works. Anyway, next up we have..."). No prompt
# can fix that at utterance level -- the cut point is INSIDE the utterance. So
# this pass re-segments the clip's neighbourhood into sentences built from
# word-level timestamps and lets the model choose sentence boundaries instead.
# Cuts stay grounded: a sentence edge is a real word timestamp, never a guess.

MAX_SENTENCE_WORDS = 40        # fallback split for speech with no punctuation

REFINE_PROMPT = """For each clip below you are shown its current cut as numbered SENTENCES,
plus a few sentences of surrounding context. Lines marked >> are currently
inside the clip. Choose the exact first and last sentence.

Adjust each range so that:
- it STARTS on the sentence that hooks hardest -- drop throat-clearing lead-in
- it ENDS exactly on its strongest closing sentence: the punchline, the payoff,
  the mic-drop. This is where most clips go wrong. If anything comes after the
  payoff -- a trailing reaction, "anyway...", a transition into the next topic
  ("let's go try the next one") -- cut the end back to the payoff sentence. If
  the true punchline sits just outside the current range, extend to include it
  and stop right there.

Judge the boundaries purely on where the moment begins and ends. Ignore how long
that makes the clip -- do not shorten one that needs its length, and never extend
one past its ending to make it longer.

Returning the range unchanged is a valid answer, but inspect every ending
closely before concluding it is already right.

Return ONLY valid JSON:
{{"clips": [{{"id": <int>, "start_sentence": <int>, "end_sentence": <int>}}]}}

{blocks}
"""

REFINE_PADDING = 4             # utterances of context either side of the clip

SENTENCE_ENDINGS = (".", "!", "?", "।", "|")


def _split_sentences(words: list) -> list:
    """Groups word objects into sentences on punctuation, grounded in timestamps.

    Returns [{"text", "start", "end", "words"}]. Falls back to a hard split for
    long punctuation-free stretches so one run-on can't swallow the whole clip.
    """
    sentences = []
    current = []
    for word in words:
        current.append(word)
        token = (word.get("punctuated_word") or word.get("word") or "").strip()
        if token.endswith(SENTENCE_ENDINGS) or len(current) >= MAX_SENTENCE_WORDS:
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)

    return [
        {
            "text": " ".join((w.get("punctuated_word") or w.get("word") or "") for w in sent),
            "start": sent[0]["start"],
            "end": sent[-1]["end"],
            "words": sent,
        }
        for sent in sentences if sent
    ]


def _utterance_index_at(utterances: list, t: float) -> int:
    """Utterance containing time t, for keeping overlap checks meaningful."""
    for i, u in enumerate(utterances):
        if u["end"] >= t:
            return i
    return len(utterances) - 1


def _refine_boundaries(utterances: list, clips: list) -> list:
    """One batched call to move every clip's cut points to real sentence edges."""
    if not clips:
        return clips

    per_clip_sentences = []
    blocks = []
    for n, clip in enumerate(clips):
        lo = max(0, clip["start_index"] - REFINE_PADDING)
        hi = min(len(utterances) - 1, clip["end_index"] + REFINE_PADDING)
        words = [w for i in range(lo, hi + 1) for w in utterances[i].get("words", [])]
        sentences = _split_sentences(words)
        if not sentences:
            per_clip_sentences.append(None)
            continue
        per_clip_sentences.append(sentences)

        clip_start = utterances[clip["start_index"]]["start"]
        clip_end = utterances[clip["end_index"]]["end"]
        lines = []
        for s_idx, sent in enumerate(sentences):
            mid = (sent["start"] + sent["end"]) / 2
            marker = ">>" if clip_start <= mid <= clip_end else "  "
            lines.append(f"{marker} [s{s_idx}] {sent['text']}")
        blocks.append(f"--- clip id {n} ---\n" + "\n".join(lines))

    if not blocks:
        return clips

    try:
        # Boundary surgery wants precision, not creativity -- sample near-greedy.
        data = _openai_chat(REFINE_PROMPT.format(blocks="\n\n".join(blocks)),
                            temperature=0.1)
    except RuntimeError:
        return clips

    refined = list(clips)
    for entry in data.get("clips", []):
        try:
            n = int(entry["id"])
            s_first = int(entry["start_sentence"])
            s_last = int(entry["end_sentence"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= n < len(refined) or per_clip_sentences[n] is None:
            continue

        sentences = per_clip_sentences[n]
        s_first = max(0, min(s_first, len(sentences) - 1))
        s_last = max(s_first, min(s_last, len(sentences) - 1))

        chosen = sentences[s_first:s_last + 1]
        start_t, end_t = chosen[0]["start"], chosen[-1]["end"]
        if end_t - start_t < MIN_USABLE_SECONDS:
            continue          # refinement collapsed the clip into a fragment

        refined[n] = {
            **clips[n],
            # word-accurate cut, resolved later in resolve_clip_times
            "refined": {
                "start": start_t,
                "end": end_t,
                "words": [w for sent in chosen for w in sent["words"]],
            },
            "start_index": _utterance_index_at(utterances, start_t),
            "end_index": _utterance_index_at(utterances, end_t),
            "duration": round(end_t - start_t, 1),
        }

    return refined


def pick_clips(utterances: list, n_clips: int, on_progress=None,
               intent: str = "", length_pref: str = "") -> list:
    """Three passes: nominate per window, compare finalists, refine boundaries.

    intent:      free-text steer from the user ("when he talks about pricing")
    length_pref: key into LENGTH_PREFS, an advisory bias only -- never a filter,
                 because a great 20s moment beats a padded 45s one even when the
                 user asked for 30-60s.
    """
    intent = intent.strip()
    candidates = _collect_candidates(
        utterances,
        on_progress,
        intent=intent,
        length_guidance=LENGTH_PREFS.get(length_pref, ""),
    )

    if not candidates:
        # The model found nothing it considered strong. That is a judgement, not
        # an error -- but failing the job over it hands the user nothing after
        # they have already paid for transcription. Build something usable
        # instead and let the later passes pick the best of it.
        candidates = _fallback_candidates(utterances)
        print(f"[clipper] model nominated no moments; falling back to "
              f"{len(candidates)} speech run(s)")

    # Overlapping windows nominate the same moment twice; dedupe on identical ranges.
    seen = set()
    unique = []
    for c in candidates:
        key = (c["start_index"], c["end_index"])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    measured = [_measure(utterances, c) for c in unique]
    valid = [c for c in measured
             if MIN_USABLE_SECONDS <= c["duration"] <= MAX_SANE_SECONDS]

    # Fall back to the unfiltered set rather than returning nothing for a short video.
    pool = valid or measured
    pool.sort(key=lambda c: c["score"], reverse=True)

    # Shortlist more than we need so the comparative pass has real choices,
    # keeping them non-overlapping so it never picks two versions of one moment.
    shortlist = []
    for c in pool:
        if len(shortlist) >= max(n_clips * SHORTLIST_MULTIPLIER, MIN_SHORTLIST):
            break
        if not any(_overlaps(c, picked) for picked in shortlist):
            shortlist.append(c)

    if SELECTION_PASSES >= 2:
        if on_progress:
            on_progress(-1, len(shortlist))      # signals the comparison stage
        chosen = _final_selection(utterances, shortlist, n_clips, intent=intent)
    else:
        chosen = shortlist[:n_clips]

    if SELECTION_PASSES >= 3:
        chosen = _refine_boundaries(utterances, chosen)

    # Refinement can nudge two clips into each other; keep the stronger one.
    deduped = []
    for c in sorted(chosen, key=lambda c: c["score"], reverse=True):
        if not any(_overlaps(c, picked) for picked in deduped):
            deduped.append(c)

    # Rendering and the dashboard preserve this order, so clip 1 is always the
    # strongest result rather than simply the earliest moment in the source.
    deduped.sort(key=lambda c: c["score"], reverse=True)
    return deduped


# A clip must never END on a segue or a channel CTA -- that is the single most
# common way an otherwise good clip feels broken. The refine pass is told this,
# but one sampled answer is not reliable enough, so this deterministic guard
# backstops it: if the final sentence pattern-matches a transition/CTA, drop it.
# Patterns are deliberately conservative and anchored to observed failures; a
# false positive here would cut real content, so vague words don't qualify.
# Deliberately narrow: ONLY unambiguous channel CTAs. Transition words are NOT
# listed here -- "let's go" is often the peak excitement of a clip, and "the next
# one is even better" is a hook, not junk. Segues need surrounding context to
# judge, which is the refine pass's job, not a regex's.
TRAILING_JUNK = re.compile(
    r"comment\s+section|hit\s+(the\s+)?(like|subscribe)|smash\s+that|"
    r"subscribe\s+(to|for|karo)|like\s+and\s+share|share\s+and\s+subscribe|"
    r"चैनल\s+को\s+subscribe|comment\s+में\s+बता|like\s+ज़रूर\s+कर",
    re.IGNORECASE,
)
MAX_TAIL_STRIP = 2             # at most two sentences off the end


def _strip_trailing_junk(words: list) -> list:
    """Drops sentence(s) off the end if they are segues/CTAs, never real content."""
    if not words:
        return words

    sentences = _split_sentences(words)
    stripped = 0
    while len(sentences) > 1 and stripped < MAX_TAIL_STRIP:
        last = sentences[-1]
        if not TRAILING_JUNK.search(last["text"]):
            break
        remaining_end = sentences[-2]["end"]
        remaining_start = sentences[0]["start"]
        if remaining_end - remaining_start < MIN_USABLE_SECONDS:
            break              # stripping would collapse the clip -- keep as is
        sentences.pop()
        stripped += 1

    return [w for sent in sentences for w in sent["words"]]


def _trim_weak_opening(words: list, start_time: float) -> float:
    """Skips a leading filler word so the clip opens on the hook itself."""
    if not words:
        return start_time

    for word in words[:3]:
        token = (word.get("punctuated_word") or word.get("word") or "").lower().strip(".,!? ")
        if not token:
            continue
        if token in FILLER_OPENERS:
            continue
        # First real word -- only trim if it costs less than a second of runway.
        return word["start"] if word["start"] - start_time < 1.0 else start_time
    return start_time


def resolve_clip_times(clip: dict, utterances: list) -> dict:
    """Turns chosen indices into real, grounded start/end times plus caption words."""
    refined = clip.get("refined")
    if refined:
        # Sentence-level cut from pass 3: already word-accurate.
        words = _strip_trailing_junk(refined["words"])
        start_time = _trim_weak_opening(words, refined["start"])
        end_time = words[-1]["end"] if words else refined["end"]
        words = [w for w in words if w["end"] > start_time]
        return {**clip, "start": start_time, "end": end_time, "words": words}

    start_idx = max(0, min(clip["start_index"], len(utterances) - 1))
    end_idx = max(start_idx, min(clip["end_index"], len(utterances) - 1))

    words = []
    for u in utterances[start_idx:end_idx + 1]:
        words.extend(u.get("words", []))

    words = _strip_trailing_junk(words)
    start_time = _trim_weak_opening(words, utterances[start_idx]["start"])
    end_time = words[-1]["end"] if words else utterances[end_idx]["end"]

    # Drop any words trimmed off the front so captions stay in sync.
    words = [w for w in words if w["end"] > start_time]

    return {
        **clip,
        "start": start_time,
        "end": end_time,
        "words": words,   # word-level timestamps drive the highlighted captions
    }
