"""Optional AI narration. Off by default -- most of the time you want the
real speakers' actual voices, not a synthetic one. Use this only when a
project genuinely calls for narration (e.g. dubbing into another language).
"""

import json
import os

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova",
          "sage", "shimmer", "verse", "marin", "cedar"]


def write_narration_script(numbered_transcript: str, start_index: int, end_index: int, language: str) -> str:
    prompt = f"""Utterances {start_index}-{end_index} of this transcript describe one moment:

{numbered_transcript}

Write an ORIGINAL {language} voiceover script (40-70 words, natural spoken pace ~15-25s) that
narrates this moment for a {language}-speaking audience. Do not translate word for word -- write
it fresh, in your own words. Return ONLY the script text, nothing else.
"""
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def generate_voiceover_audio(text: str, out_path: str, voice: str = "onyx") -> str:
    resp = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini-tts",
            "voice": voice,
            "input": text,
            "instructions": "Speak as an energetic, clear short-form video narrator. Natural pacing.",
        },
        timeout=120,
    )
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return out_path
