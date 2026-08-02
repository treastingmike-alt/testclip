"""Subtitle translation: original audio, captions in another language.

The audio stays untouched -- the voice, energy and delivery are the clip. The
captions are what travel: a Hindi clip with English subtitles reaches everyone
who would have scrolled past a language they don't speak.

Word-karaoke cannot survive translation (different words, different lengths,
different order), so translated captions are whole lines timed to the original
utterance spans -- see subtitles.build_ass_lines for why that is the honest
rendering.
"""

from app.pipeline.analyzer import _openai_chat

LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    # Hindi written in the Latin alphabet, which is how a very large number of
    # people actually read and write it online -- and what most Indian creators
    # caption with. Distinct from "hi", not a transliteration toggle on it:
    # asking for Devanagari and asking for Hinglish are different outputs.
    "hi-latn": "Hinglish",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
    "id": "Indonesian",
    "ja": "Japanese",
}

_PROMPT = """You are subtitling a short vertical video. Translate each numbered
line into {language}. These are spoken captions, so keep them tight and natural
-- how a native speaker would subtitle it, not a literal word-for-word gloss.
Keep names, numbers and quoted phrases as they are. Do not merge, split, add or
drop lines.
{note}
Lines:
{lines}

Return ONLY valid JSON: {{"lines": ["...", "..."]}} with exactly {n} entries,
in the same order.
"""

# Hinglish is not a language the model can be pointed at by name and trusted --
# left alone it tends to answer in Devanagari, or to "translate" into formal
# Hindi and then transliterate that, which reads nothing like how anyone
# actually writes. It needs to be described.
_NOTES = {
    "hi-latn": """
Hinglish here means Hindi written in the Latin alphabet, the way Indian
creators caption: spell words how they sound (kya, matlab, bilkul, thoda),
never in Devanagari. Leave English words that were spoken in English as
English -- code-switching mid-sentence is correct and should be preserved, not
translated away. Write the register people actually speak, not formal Hindi.
""",
}


def translate_lines(caption_lines: list, target_lang: str) -> list:
    """Returns copies of caption_lines with 'text' translated.

    Raises ValueError for an unsupported language code, RuntimeError if the
    model response cannot be used -- callers surface both to the user rather
    than silently rendering the original text as if it were translated.
    """
    language = LANGUAGES.get(target_lang)
    if not language:
        raise ValueError(
            f"Unsupported language '{target_lang}'. "
            f"Choose from: {', '.join(sorted(LANGUAGES))}"
        )

    texts = [(line.get("text") or "").strip() for line in caption_lines]
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))

    result = _openai_chat(
        _PROMPT.format(language=language, lines=numbered, n=len(texts),
                       note=_NOTES.get(target_lang, "")),
        temperature=0.2,
    )
    translated = result.get("lines")
    if not isinstance(translated, list) or len(translated) != len(texts):
        raise RuntimeError(
            f"Translation returned {len(translated) if isinstance(translated, list) else 'no'} "
            f"lines for {len(texts)} inputs."
        )

    return [
        {**line, "text": str(new).strip()}
        for line, new in zip(caption_lines, translated)
    ]
