from app.pipeline import analyzer


def _candidate(index, score, intent_match=False):
    return {
        "start_index": index,
        "end_index": index,
        "title": f"Moment {index}",
        "hook": "",
        "intent_match": intent_match,
        "scores": {},
        "score": score,
    }


def test_intent_keeps_one_match_and_one_stronger_general_moment():
    requested = _candidate(0, 9.0, True)
    weaker_requested = _candidate(1, 8.4, True)
    general = _candidate(2, 8.8)

    result = analyzer._balance_intent_selection(
        [requested, weaker_requested],
        [requested, general, weaker_requested],
        n_clips=2,
        intent="when they explain pricing",
    )

    assert requested in result
    assert general in result
    assert sum(bool(item["intent_match"]) for item in result) == 1


def test_intent_adds_a_strong_match_when_comparison_missed_it():
    requested = _candidate(0, 8.7, True)
    strongest = _candidate(1, 9.3)
    runner_up = _candidate(2, 9.0)

    result = analyzer._balance_intent_selection(
        [strongest, runner_up],
        [strongest, runner_up, requested],
        n_clips=2,
        intent="the launch announcement",
    )

    assert strongest in result
    assert requested in result


def test_pick_clips_returns_highest_score_first(monkeypatch):
    utterances = [
        {"start": i * 30.0, "end": i * 30.0 + 20.0, "transcript": f"Line {i}", "words": []}
        for i in range(3)
    ]
    candidates = [
        _candidate(0, 6.2),
        _candidate(1, 9.4),
        _candidate(2, 8.1),
    ]
    monkeypatch.setattr(analyzer, "SELECTION_PASSES", 1)
    monkeypatch.setattr(analyzer, "_collect_candidates", lambda *args, **kwargs: candidates)

    result = analyzer.pick_clips(utterances, n_clips=3)

    assert [item["score"] for item in result] == [9.4, 8.1, 6.2]

