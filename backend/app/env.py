"""Loads backend/.env into the environment, once, before anything reads keys.

The pipeline modules read their API keys at import time, so this must be
imported FIRST in app/main.py. It exists because the alternative -- exporting
keys by hand in whichever terminal happens to start uvicorn -- broke the moment
the backend was restarted from a fresh shell: Deepgram received "Token None"
and returned an unexplained 401.

Values already present in the real environment win over the file, so a
deliberate `DEEPGRAM_API_KEY=... uvicorn ...` still behaves as expected.
"""

import os

ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))


def load() -> int:
    """Parses simple KEY=VALUE lines. Returns how many values were applied."""
    if not os.path.exists(ENV_PATH):
        return 0

    applied = 0
    with open(ENV_PATH, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if key not in os.environ:
                os.environ[key] = value
                applied += 1
            elif os.environ[key] != value:
                # The classic trap: a stale export in ~/.zshrc silently beating a
                # freshly rotated key in .env. Cost a real debugging session once.
                print(f"[clipper] WARNING: {key} from your shell environment is "
                      f"OVERRIDING a different value in backend/.env. If you just "
                      f"rotated this key, unset the old export (check ~/.zshrc) "
                      f"or start from a fresh terminal.")
    return applied


load()
