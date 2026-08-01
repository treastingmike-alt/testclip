"""Turning the model's raw rubric ratings into the number a user sees.

The raw score is a weighted mean of six axes, on a scale where the model was
told "5 is a normal moment in a transcript". That is the right thing to RANK by
and the wrong thing to DISPLAY, because a clip only reaches the user after
surviving nomination out of hundreds of windows and then a head-to-head ranking
pass. Showing a top-five-of-hundreds finalist as 6.9 scores it against all raw
footage rather than against what it actually is.

So this rescales the finalist's number onto the scale this market already uses.
It is a calibration choice, not a discovery -- worth stating plainly rather than
dressing up as a technical improvement.

What it does NOT do is change which clip wins. The map is strictly monotonic in
the raw score, so ordering is identical before and after: the best clip stays
the best clip, and no clip can overtake one ranked above it.

(An earlier version blended in the single strongest axis, on the theory that
virality is peak-driven rather than an average. That theory may well be right,
but as a display-only tweak it let a lower-ranked clip show a higher number than
one above it. If it is right, it belongs in selection -- not in the label.)
"""

# (raw, displayed) anchors, linearly interpolated between. Anchored so that a
# typical shipped clip lands at 8.5-9.2 and only a genuinely exceptional one
# clears 9.5.
CURVE = [
    (0.0, 6.5),
    (4.0, 7.9),
    (5.5, 8.4),
    (7.0, 9.0),
    (8.5, 9.5),
    (10.0, 9.9),
]


def _curve(value: float) -> float:
    """Piecewise-linear map through CURVE. Monotonic by construction."""
    value = max(0.0, min(10.0, value))
    for i in range(len(CURVE) - 1):
        x0, y0 = CURVE[i]
        x1, y1 = CURVE[i + 1]
        if value <= x1:
            span = x1 - x0
            t = (value - x0) / span if span else 0.0
            return y0 + t * (y1 - y0)
    return CURVE[-1][1]


def presentation_score(raw_score: float, scores: dict) -> tuple:
    """(headline, calibrated_subscores) for display.

    `raw_score` stays untouched for ranking; this only affects what is shown.
    Sub-scores go through the same curve so the bars agree with the headline --
    a 9.0 headline above a row of half-empty bars reads as a bug.
    """
    headline = round(_curve(float(raw_score or 0)), 1)
    calibrated = {
        k: round(_curve(float(v)), 1)
        for k, v in (scores or {}).items() if v is not None
    }
    return headline, calibrated
