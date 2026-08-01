"""Transcribes audio with Deepgram, preserving word-level timestamps.

Word-level data is what lets us later snap clip boundaries to real speech
edges and build accurate burned-in captions, instead of trusting an LLM's
guessed second values.
"""

from app import env  # noqa: F401  -- ensures .env is loaded

import os

import requests

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")


def _require_key():
    if not DEEPGRAM_API_KEY:
        raise RuntimeError(
            "No Deepgram API key is configured, so speech can't be transcribed. "
            "Add DEEPGRAM_API_KEY=... to backend/.env and restart the backend."
        )


def transcribe(audio_path: str) -> dict:
    _require_key()
    with open(audio_path, "rb") as f:
        resp = requests.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "model": "nova-2",
                "smart_format": "true",
                "utterances": "true",
                "punctuate": "true",
                "detect_language": "true",
            },
            headers={
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "audio/mp3",
            },
            data=f.read(),
            timeout=300,
        )
    if resp.status_code in (401, 403):
        raise RuntimeError(
            "Deepgram rejected the API key. Check DEEPGRAM_API_KEY in "
            "backend/.env (was the key rotated or the project deleted?)."
        )
    resp.raise_for_status()
    return resp.json()


def get_utterances(transcript_json: dict) -> list:
    """Each utterance: {start, end, transcript, words: [{word, start, end}, ...]}"""
    return transcript_json["results"]["utterances"]


def build_numbered_transcript(utterances: list) -> str:
    """Numbered so the LLM can reference utterances by index instead of inventing timestamps."""
    lines = [f"[{i}] ({u['start']:.1f}-{u['end']:.1f}) {u['transcript']}" for i, u in enumerate(utterances)]
    return "\n".join(lines)
