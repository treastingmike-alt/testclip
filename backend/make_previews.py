"""Renders one short preview clip per template, for the UI's template picker.

The picker used to show hand-drawn CSS mockups, which could only approximate the
output. These previews are produced by the real renderer with the real caption
styles, so what a user sees in the card is literally what they will get.

Usage:
    ./venv/bin/python make_previews.py path/to/sample.mp4 [--start 12 --dur 5]

Put in a clip you have rights to, ideally a person talking to camera. Output
goes to frontend/public/previews/<template>.mp4 and is picked up automatically.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.main import TEMPLATES, GAMEPLAY_DIR          # noqa: E402
from app.pipeline import render, subtitles            # noqa: E402

MAX_GAMEPLAY_PREVIEWS = 4       # cap so page weight stays trivial

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "frontend", "public", "previews")

# Stand-in caption so every template shows the same words -- the differences you
# see are then purely the template's, not the text's.
SAMPLE_WORDS = [
    ("Here", 0.15, 0.85), ("is", 0.85, 1.15), ("your", 1.15, 1.70),
    ("subtitle", 1.70, 2.60), ("style", 2.60, 3.40),
]


def build_words(offset: float) -> list:
    return [
        {"punctuated_word": w, "word": w.lower(),
         "start": offset + s, "end": offset + e}
        for w, s, e in SAMPLE_WORDS
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="sample video you have rights to")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=4.0)
    args = ap.parse_args()

    if not os.path.exists(args.source):
        print(f"error: {args.source} not found")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = os.path.join(OUT_DIR, "_tmp.ass")
    width, height = render.RATIOS["9:16"]

    try:
        loops = sorted(f for f in os.listdir(GAMEPLAY_DIR)
                       if f.lower().endswith((".mp4", ".mov", ".webm")))
    except FileNotFoundError:
        loops = []
    # One preview per installed loop (capped), so the card can rotate through
    # them and show what "gameplay" actually means with this user's own footage.
    gameplay_loops = [os.path.join(GAMEPLAY_DIR, f) for f in loops[:MAX_GAMEPLAY_PREVIEWS]]

    manifest = {}
    for template_id, cfg in TEMPLATES.items():
        is_split = cfg["frame"] == "gameplay"
        if is_split and not gameplay_loops:
            print(f"  {template_id:9} SKIPPED -- no footage in assets/gameplay/")
            continue

        margin_v = (render.split_caption_margin_v(height) if is_split
                    else render.caption_margin_v(1920, 1080, width, height))
        subtitles.build_ass(build_words(args.start), args.start, tmp,
                            margin_v, style=cfg["caption_style"],
                            play_res=(width, height))

        # Split templates render one variant per installed loop; everything
        # else renders exactly one.
        variants = gameplay_loops if is_split else [None]
        names = []
        for n, loop in enumerate(variants):
            name = template_id if n == 0 else f"{template_id}-{n}"
            out = os.path.join(OUT_DIR, f"{name}.mp4")
            render.render_clip(
                args.source, args.start, args.start + args.dur, out,
                subtitle_path=tmp, gameplay_path=loop, frame=cfg["frame"],
            )
            # Shrink hard: these are ~150px wide in the UI and load on page open.
            small = out.replace(".mp4", "_s.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-i", out, "-vf", "scale=324:576", "-an",
                 "-c:v", "libx264", "-preset", "slow", "-crf", "32",
                 "-movflags", "+faststart", small],
                capture_output=True,
            )
            os.replace(small, out)
            names.append(f"{name}.mp4")
            label = os.path.basename(loop)[:28] if loop else "-"
            print(f"  {name:12} -> {os.path.getsize(out) // 1024:4} KB   {label}")
        manifest[template_id] = names

    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    if os.path.exists(tmp):
        os.remove(tmp)
    print(f"\nWritten to {os.path.abspath(OUT_DIR)}")
    print("Reload the app -- the picker uses them automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
