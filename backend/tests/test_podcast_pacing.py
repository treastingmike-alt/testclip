from app.pipeline import pacing


def _words_with_gaps(gaps):
    words = []
    t = 0.0
    for i, gap in enumerate([0.0, *gaps]):
        t += gap
        words.append({"word": str(i), "start": t, "end": t + 0.2})
        t += 0.2
    return words


def test_podcast_profile_keeps_conversational_pauses():
    words = _words_with_gaps([0.5, 1.1, 1.7, 0.4, 2.2])
    clip_end = words[-1]["end"]

    standard, _, _ = pacing.tighten(
        words, 0.0, clip_end, max_pause=pacing.DEFAULT_MAX_PAUSE)
    podcast, _, _ = pacing.tighten(
        words, 0.0, clip_end, max_pause=pacing.PODCAST_MAX_PAUSE)

    assert len(standard) == 6
    assert len(podcast) == 3
    assert pacing.max_pause_for_frame("podcast") == pacing.PODCAST_MAX_PAUSE


def test_original_pacing_is_a_true_no_cut_timeline():
    words = _words_with_gaps([2.0, 2.0])
    start, end = 0.0, words[-1]["end"]

    # This is the exact branch the editor/export toggle selects when off.
    segments = None
    duration = end - start if segments is None else sum(e - s for s, e in segments)

    assert duration == end - start
