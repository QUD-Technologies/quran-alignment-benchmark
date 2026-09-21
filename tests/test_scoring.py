"""SPEC section 7 tests: every rule the scorer promises, on small synthetic cases."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from qab import hafs
from qab.report import evaluate
from qab.schema import Case, Submission, SubmissionMeta
from qab.scoring import score_case

# --- builders -------------------------------------------------------------------------------

def loc(chapter: int, ordinal: int) -> str:
    c, a, w = hafs.location(chapter, ordinal)
    return f"{c}:{a}:{w}"


def span(chapter: int, first: int, last: int) -> str:
    return f"{loc(chapter, first)}-{loc(chapter, last)}"


def make_case(labels: list[str], *, step: float = 1.0, gap: float = 0.0, non_quran=(), start: float = 0.0,
              **facets) -> Case:
    """One word per `step` seconds, each `step - gap` long, labels in recitation order."""
    words, t = [], start
    for label in labels:
        words.append({"word": label, "start_s": round(t, 3), "end_s": round(t + step - gap, 3)})
        t += step
    base = dict(id="t", style="murattal", content="quran_only", noisy=False, multi_surah=False)
    base.update(facets)
    return Case(duration_s=t + 5, words=words, non_quran=[{"start_s": a, "end_s": b} for a, b in non_quran], **base)


def sub(segments, case_id="t", runtime=None) -> Submission:
    return Submission(case_id=case_id, runtime_seconds=runtime,
                      segments=[dict(start_s=a, end_s=b, reference=ref, **({"confidence": c[0]} if c else {}))
                                for a, b, ref, *c in segments])


def chapter_words(chapter: int, first: int, last: int) -> list[str]:
    return [loc(chapter, o) for o in range(first, last + 1)]


CH = 2   # al-Baqarah: plenty of ordinals


# --- granularity invariance ----------------------------------------------------------------

def test_granularity_invariance():
    labels = chapter_words(CH, 0, 19)
    case = make_case(labels)
    whole = sub([(0.0, 20.0, span(CH, 0, 19))])
    per_word = sub([(o, o + 1.0, span(CH, o, o)) for o in range(20)])
    a, b = score_case(case, whole), score_case(case, per_word)
    assert (a.words_found, a.words_correct) == (b.words_found, b.words_correct) == (1.0, 1.0)
    assert a.clean_segments == a.quran_segments and b.clean_segments == b.quran_segments
    assert (a.truth_repeats, a.predicted_repeats, a.caught_repeats) == (b.truth_repeats, b.predicted_repeats, b.caught_repeats) == (0, 0, 0)


# --- repeats --------------------------------------------------------------------------------

def test_repeat_blindness_and_catch():
    labels = chapter_words(CH, 0, 9) + chapter_words(CH, 5, 9) + chapter_words(CH, 10, 11)   # repeat of 5..9
    case = make_case(labels)
    assert score_case(case, sub([(0, 17, span(CH, 0, 11))])).truth_repeats == 1
    blind = score_case(case, sub([(0, 17, span(CH, 0, 11))]))
    assert blind.matched_words == 12 and blind.quran_instances == 17
    assert blind.caught_repeats == 0 and blind.predicted_repeats == 0
    aware = score_case(case, sub([(0, 10, span(CH, 0, 9)), (10, 15, span(CH, 5, 9)), (15, 17, span(CH, 10, 11))]))
    assert aware.matched_words == 17 and aware.caught_repeats == 1 and aware.predicted_repeats == 1


def test_invented_repeat():
    case = make_case(chapter_words(CH, 0, 11))
    r = score_case(case, sub([(0, 10, span(CH, 0, 9)), (10, 12, span(CH, 7, 9))]))
    assert r.claimed_words == 13 and r.matched_words == 10
    assert sum(s.false_claims for s in r.segments) == 3
    assert r.quran_instances - r.matched_words == 2
    assert r.predicted_repeats == 1 and r.caught_repeats == 0


def test_repeat_resets_on_chapter_change():
    labels = chapter_words(1, 4, 10) + chapter_words(75, 0, 3) + chapter_words(1, 4, 10)
    case = make_case(labels, multi_surah=True)
    r = score_case(case, sub([(0, 7, span(1, 4, 10)), (7, 11, span(75, 0, 3)), (11, 18, span(1, 4, 10))]))
    assert r.truth_repeats == 0 and r.predicted_repeats == 0


# --- clean edges ----------------------------------------------------------------------------

@pytest.mark.parametrize("pad, clean", [(1.9, True), (2.1, False)])
def test_clean_pad(pad, clean):
    case = make_case(chapter_words(CH, 0, 9), start=5.0)          # words 5..15
    r = score_case(case, sub([(5.0 - pad, 15.0 + pad, span(CH, 0, 9))]))
    assert r.clean_segments == (1 if clean else 0)


@pytest.mark.parametrize("leak, clean", [(0.4, True), (0.6, False)])
def test_clean_leak_into_next_word(leak, clean):
    case = make_case(chapter_words(CH, 0, 10))                    # word 10 starts at 10.0
    r = score_case(case, sub([(0.0, 10.0 + leak, span(CH, 0, 9)), (10.0 + leak, 11.0, span(CH, 10, 10))]))
    assert r.segments[0].clean is clean


def test_clean_requires_nothing_extra():
    case = make_case(chapter_words(CH, 0, 4))
    r = score_case(case, sub([(0, 5, span(CH, 0, 3))]))   # holds word 4 too
    assert r.segments[0].unaccounted == 1 and not r.segments[0].clean


# --- null vs gap ----------------------------------------------------------------------------

def test_null_vs_gap():
    labels = chapter_words(CH, 0, 4)
    case = make_case(labels)
    gap = score_case(case, sub([(0, 3, span(CH, 0, 2))]))
    null = score_case(case, sub([(0, 3, span(CH, 0, 2)), (3, 5, None)]))
    assert (gap.words_found, gap.words_correct) == (null.words_found, null.words_correct)
    assert (gap.denied, gap.uncovered) == (0, 2) and (null.denied, null.uncovered) == (2, 0)


# --- formulas -------------------------------------------------------------------------------

def test_formula_segment_off_headline_but_reported():
    labels = [f"Basmala:{k}" for k in range(1, 5)] + chapter_words(CH, 0, 4)
    case = make_case(labels)
    plain = score_case(case, sub([(4, 9, span(CH, 0, 4))]))
    with_b = score_case(case, sub([(0, 4, "Basmala"), (4, 9, span(CH, 0, 4))]))
    for r in (plain, with_b):
        assert (r.words_found, r.words_correct, r.clean_segments) == (1.0, 1.0, 1)
    assert plain.formulas["Basmala"].missed == 1 and with_b.formulas["Basmala"].exact == 1


def test_quran_segment_swallowing_basmala_is_not_clean():
    labels = [f"Basmala:{k}" for k in range(1, 5)] + chapter_words(CH, 0, 4)
    case = make_case(labels)
    r = score_case(case, sub([(0, 9, span(CH, 0, 4))]))
    assert r.matched_words == 5 and r.segments[0].unaccounted == 4 and not r.segments[0].clean
    assert r.formulas["Basmala"].missed == 1


def test_basmala_over_quran_audio_costs_words_and_is_extra():
    case = make_case(chapter_words(CH, 0, 4))
    r = score_case(case, sub([(0, 5, "Basmala")]))
    assert r.words_found == 0 and r.claimed_words == 0
    assert r.formulas["Basmala"].extra == 1
    assert r.quran_segments == 0


def test_quran_claim_over_formula_audio_is_false():
    labels = [f"Basmala:{k}" for k in range(1, 5)] + chapter_words(CH, 0, 4)
    case = make_case(labels)
    r = score_case(case, sub([(0, 4, span(CH, 5, 8)), (4, 9, span(CH, 0, 4))]))
    assert r.segments[0].false_claims == 4 and not r.segments[0].clean
    assert r.formulas["Basmala"].missed == 1


# --- Basmala class --------------------------------------------------------------------------

def test_basmala_class_over_fatiha_opening():
    case = make_case(chapter_words(1, 0, 10))
    a = score_case(case, sub([(0, 4, "Basmala"), (4, 11, span(1, 4, 10))]))
    b = score_case(case, sub([(0, 4, span(1, 0, 3)), (4, 11, span(1, 4, 10))]))
    for r in (a, b):
        assert r.matched_words == 11 and r.claimed_words == 11 and r.words_correct == 1.0
        assert r.clean_segments == 2 and r.quran_segments == 2
        assert r.formulas["Basmala"].extra == 0 and r.formulas["Basmala"].occurrences == 0


def test_basmala_class_over_other_chapter_opening():
    labels = [f"Basmala:{k}" for k in range(1, 5)] + chapter_words(CH, 0, 4)
    case = make_case(labels)
    a = score_case(case, sub([(0, 4, "Basmala"), (4, 9, span(CH, 0, 4))]))
    b = score_case(case, sub([(0, 4, span(1, 0, 3)), (4, 9, span(CH, 0, 4))]))
    for r in (a, b):
        assert r.claimed_words == 5 and r.matched_words == 5 and r.clean_segments == 1 and r.quran_segments == 1
        assert r.formulas["Basmala"].exact == 1 and r.formulas["Basmala"].extra == 0


def test_fatiha_span_beyond_class_is_quran():
    labels = [f"Basmala:{k}" for k in range(1, 5)] + chapter_words(CH, 0, 4)
    case = make_case(labels)
    r = score_case(case, sub([(0, 4, span(1, 0, 5)), (4, 9, span(CH, 0, 4))]))
    assert r.segments[0].lane == "quran" and r.segments[0].claim_words == 6 and r.segments[0].false_claims == 6
    assert r.formulas["Basmala"].detected == 1 and r.formulas["Basmala"].exact == 0


# --- confidence -----------------------------------------------------------------------------

def _pooled(case, submission, meta=None):
    return evaluate([case], [submission], meta, corpus_version="test")


def test_consumer_confidence_states_and_tiers():
    case = make_case(chapter_words(CH, 0, 9))
    none = _pooled(case, sub([(0, 10, span(CH, 0, 9))]))
    assert none["confidence"]["state"] == "not_reported"
    assert none["headline"]["trusted_coverage"] is None
    perfect = _pooled(case, sub([(0, 10, span(CH, 0, 9), 0.8)]))
    assert perfect["headline"]["trusted_coverage"] == 1
    assert perfect["headline"]["unsafe_green"] == 0
    assert perfect["confidence"]["tiers"]["green"]["segments"] == 1


def test_consumer_confidence_penalizes_unsafe_green_four_times():
    case = make_case(chapter_words(CH, 0, 29))
    segments = [(i, i + 5, span(CH, i, i + 4), 0.8) for i in range(0, 25, 5)]
    segments.append((25, 30, span(CH, 35, 39), 0.8))
    rep = _pooled(case, sub(segments))
    assert rep["confidence"]["green_correct"] == 5
    assert rep["confidence"]["green_wrong"] == 1
    assert rep["headline"]["unsafe_green"] == pytest.approx(1 / 6)
    assert rep["headline"]["trusted_coverage"] == pytest.approx(1 / 6)


def test_consumer_confidence_fixed_tier_edges():
    case = make_case(chapter_words(CH, 0, 14))
    rep = _pooled(case, sub([(0, 5, span(CH, 0, 4), 0.8),
                             (5, 10, span(CH, 5, 9), 0.6),
                             (10, 15, span(CH, 10, 14), 0.59)]))
    assert {tier: values["segments"] for tier, values in rep["confidence"]["tiers"].items()} == {
        "green": 1, "amber": 1, "red": 1}


def test_consumer_confidence_no_green_is_zero_coverage_and_undefined_risk():
    case = make_case(chapter_words(CH, 0, 4))
    rep = _pooled(case, sub([(0, 5, span(CH, 0, 4), 0.79)]))
    assert rep["headline"]["trusted_coverage"] == 0
    assert rep["headline"]["unsafe_green"] is None


def test_consumer_confidence_marks_green_quran_over_non_quran_critical():
    case = make_case(chapter_words(CH, 0, 4), non_quran=[(6, 10)])
    rep = _pooled(case, sub([(0, 5, span(CH, 0, 4), 0.8),
                             (6, 10, span(CH, 5, 9), 0.8)]))
    assert rep["confidence"]["critical_non_quran_green"] == 1
    assert rep["confidence"]["green_wrong"] == 1


def test_confidence_all_or_nothing_across_cases():
    a = make_case(chapter_words(CH, 0, 4), id="a")
    b = make_case(chapter_words(CH, 0, 4), id="b")
    rep = evaluate([a, b], [sub([(0, 5, span(CH, 0, 4), 0.9)], "a"), sub([(0, 5, span(CH, 0, 4))], "b")])
    assert rep["confidence_reported"] is False and rep["headline"]["trusted_coverage"] is None


# --- validation -----------------------------------------------------------------------------

@pytest.mark.parametrize("overlap, ok", [(0.4, True), (0.6, False)])
def test_overlap_limit(overlap, ok):
    segs = [(0, 5, span(CH, 0, 4)), (5 - overlap, 10, span(CH, 5, 9))]
    if ok:
        sub(segs)
    else:
        with pytest.raises(ValidationError):
            sub(segs)


@pytest.mark.parametrize("ref", ["2:5-2:7", "2:5:1", "2:5:1-3:1:1", "2:6:1-2:5:1", "1:8:1-1:8:1", "basmala"])
def test_shorthand_rejected(ref):
    with pytest.raises(ValidationError):
        sub([(0, 1, ref)])


def test_single_word_span_accepted():
    sub([(0, 1, "2:5:1-2:5:1")])


def test_overlap_attribution_goes_to_earlier():
    case = make_case(chapter_words(CH, 0, 1))                     # word 1 mid at 1.5
    r = score_case(case, sub([(0, 1.7, span(CH, 0, 1)), (1.3, 3, span(CH, 1, 1))]))
    assert r.segments[0].matched == 2 and r.segments[1].matched == 0


# --- runtime & pooling ----------------------------------------------------------------------

def test_runtime_subset_not_reported():
    a = make_case(chapter_words(CH, 0, 4), id="a")
    b = make_case(chapter_words(CH, 0, 4), id="b")
    meta = SubmissionMeta(system="x", version="1", hardware_class="cpu")
    full = evaluate([a, b], [sub([(0, 5, span(CH, 0, 4))], "a", 1.0), sub([(0, 5, span(CH, 0, 4))], "b", 1.0)], meta)
    assert full["headline"]["rtf"] == pytest.approx(2.0 / (a.duration_s + b.duration_s))
    part = evaluate([a, b], [sub([(0, 5, span(CH, 0, 4))], "a", 1.0), sub([(0, 5, span(CH, 0, 4))], "b")], meta)
    assert part["runtime_reported"] is False and part["headline"]["rtf"] is None


def test_runtime_needs_hardware_class():
    a = make_case(chapter_words(CH, 0, 4), id="a")
    with pytest.raises(ValueError):
        evaluate([a], [sub([(0, 5, span(CH, 0, 4))], "a", 1.0)], SubmissionMeta(system="x", version="1"))


def test_pooling_is_not_a_mean():
    long_case = make_case(chapter_words(CH, 0, 99), id="long")
    short_case = make_case(chapter_words(CH, 0, 1), id="short")
    rep = evaluate([long_case, short_case],
                   [sub([(0, 100, span(CH, 0, 99))], "long"), sub([(0, 2, span(CH, 0, 0))], "short")])
    assert rep["pooled"]["words_found"] == pytest.approx(101 / 102)
    assert rep["equal_case"]["words_found"] == pytest.approx((1.0 + 0.5) / 2)


def test_missing_case_is_an_error():
    a = make_case(chapter_words(CH, 0, 4), id="a")
    b = make_case(chapter_words(CH, 0, 4), id="b")
    with pytest.raises(ValueError):
        evaluate([a, b], [sub([(0, 5, span(CH, 0, 4))], "a")])


def test_slices_by_facet():
    a = make_case(chapter_words(CH, 0, 4), id="a", style="hadr")
    b = make_case(chapter_words(CH, 0, 4), id="b")
    rep = evaluate([a, b], [sub([(0, 5, span(CH, 0, 4))], "a"), sub([(0, 5, span(CH, 0, 4))], "b")])
    assert rep["slices"]["style=hadr"]["cases"] == ["a"] and rep["slices"]["style=murattal"]["cases"] == ["b"]


def test_no_repeats_anywhere_is_dash():
    case = make_case(chapter_words(CH, 0, 4))
    rep = _pooled(case, sub([(0, 5, span(CH, 0, 4))]))
    assert rep["headline"]["repeats_f1"] is None


def test_false_over_non_quran_diagnostic():
    case = make_case(chapter_words(CH, 0, 4), non_quran=[(6, 9)])
    r = score_case(case, sub([(0, 5, span(CH, 0, 4)), (6, 9, span(CH, 5, 7))]))
    assert r.false_over_non_quran == 3


# --- review fixes -----------------------------------------------------------------------------

def test_micro_segments_are_not_clean():
    case = make_case(chapter_words(CH, 0, 4))
    segs = []
    for o in range(5):
        mid = o + 0.5
        segs.append((mid - 1e-6, mid + 1e-6, span(CH, o, o)))
    r = score_case(case, sub(segs))
    assert r.words_found == 1.0 and r.clean_segments == 0


def test_segment_must_cover_its_words_within_epsilon():
    case = make_case(chapter_words(CH, 0, 4))          # words 0..5
    assert score_case(case, sub([(0.4, 4.6, span(CH, 0, 4))])).clean_segments == 1
    assert score_case(case, sub([(0.6, 5.0, span(CH, 0, 4))])).clean_segments == 0
    assert score_case(case, sub([(0.0, 4.4, span(CH, 0, 4))])).clean_segments == 0


def test_non_quran_speech_inside_segment_is_not_clean():
    case = make_case(chapter_words(CH, 0, 2), start=3.0, non_quran=[(0.0, 2.0)])
    assert score_case(case, sub([(0.0, 6.0, span(CH, 0, 2))])).clean_segments == 0
    assert score_case(case, sub([(1.6, 6.0, span(CH, 0, 2))])).clean_segments == 1


def test_leak_into_formula_neighbour_counts():
    labels = chapter_words(CH, 0, 2) + [f"Basmala:{k}" for k in range(1, 5)]
    case = make_case(labels)                             # Basmala:1 starts at 3.0
    assert score_case(case, sub([(0, 3.4, span(CH, 0, 2))])).clean_segments == 1
    assert score_case(case, sub([(0, 3.6, span(CH, 0, 2))])).clean_segments == 0


def test_unmatched_class_tokens_in_quran_span_are_false_claims():
    case = make_case(chapter_words(1, 4, 4))            # only 1:2:1
    r = score_case(case, sub([(0, 1, span(1, 0, 4))]))
    assert r.claimed_words == 5 and r.matched_words == 1 and r.segments[0].false_claims == 4


def test_basmala_confidence_consistency():
    with pytest.raises(ValidationError):
        sub([(0, 4, "Basmala"), (4, 11, span(1, 4, 10), 0.9)])
    case = make_case(chapter_words(1, 0, 10))
    rep = _pooled(case, sub([(0, 4, "Basmala", 0.9), (4, 11, span(1, 4, 10), 0.9)]))
    assert rep["confidence"]["segments"] == 2


def test_empty_submission_scores_zero_not_null():
    case = make_case(chapter_words(CH, 0, 4))
    rep = _pooled(case, Submission(case_id="t", segments=[]))
    assert rep["headline"]["words_f1"] == 0.0


def test_repeats_missed_entirely_is_zero():
    labels = chapter_words(CH, 0, 4) + chapter_words(CH, 2, 4)
    case = make_case(labels)
    rep = _pooled(case, sub([(0, 8, span(CH, 0, 4))]))
    assert rep["headline"]["repeats_f1"] == 0.0


def test_consecutive_basmalas_are_two_occurrences():
    labels = [f"Basmala:{k}" for k in range(1, 5)] * 2 + chapter_words(CH, 0, 1)
    case = make_case(labels)
    r = score_case(case, sub([(0, 4, "Basmala"), (4, 8, "Basmala"), (8, 10, span(CH, 0, 1))]))
    assert r.formulas["Basmala"].occurrences == 2 and r.formulas["Basmala"].exact == 2


def test_formula_exact_needs_every_word():
    labels = [f"Basmala:{k}" for k in range(1, 5)] + chapter_words(CH, 0, 1)
    case = make_case(labels)
    r = score_case(case, sub([(0, 3, span(1, 0, 2)), (3, 6, span(CH, 0, 1))]))
    assert r.formulas["Basmala"].detected == 1 and r.formulas["Basmala"].exact == 0


def test_non_finite_rejected():
    with pytest.raises(ValidationError):
        Submission.model_validate_json('{"case_id":"t","segments":[{"start_s":0,"end_s":NaN,"reference":null}]}')
    with pytest.raises(ValidationError):
        Submission(case_id="t", runtime_seconds=float("inf"), segments=[])


def test_segment_beyond_audio_is_an_error():
    case = make_case(chapter_words(CH, 0, 4))
    with pytest.raises(ValueError):
        score_case(case, sub([(0, case.duration_s + 5, span(CH, 0, 4))]))


def test_runtime_requires_metadata():
    a = make_case(chapter_words(CH, 0, 4), id="a")
    with pytest.raises(ValueError):
        evaluate([a], [sub([(0, 5, span(CH, 0, 4))], "a", 1.0)], None)


def test_duplicate_or_empty_corpus_rejected():
    a = make_case(chapter_words(CH, 0, 4), id="a")
    with pytest.raises(ValueError):
        evaluate([a, a], [sub([(0, 5, span(CH, 0, 4))], "a")])
    with pytest.raises(ValueError):
        evaluate([], [])


def test_report_carries_fingerprint_and_position_error():
    labels = chapter_words(CH, 0, 4) + chapter_words(CH, 2, 4)
    case = make_case(labels)
    rep = _pooled(case, sub([(0, 5, span(CH, 0, 4)), (5, 8, span(CH, 0, 4))]))
    assert len(rep["corpus_fingerprint"]) == 64
    assert rep["diagnostics"]["repeat_position_error_median_words"] == 2


def test_cli_validate_and_score(tmp_path):
    import json
    import subprocess
    import sys
    case = make_case(chapter_words(CH, 0, 4), id="a")
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "a.json").write_text(case.model_dump_json(), encoding="utf-8")
    subs = tmp_path / "subs"
    subs.mkdir()
    (subs / "a.json").write_text(json.dumps({"case_id": "a", "segments": [
        {"start_s": 0, "end_s": 5, "reference": span(CH, 0, 4)}]}), encoding="utf-8")
    def run(*args):
        return subprocess.run([sys.executable, "-m", "qab.cli", *args], capture_output=True, text=True)
    assert run("validate", str(tmp_path / "missing")).returncode == 1
    assert run("validate", str(subs)).returncode == 0
    out = run("score", str(subs), "--cases", str(cases), "--summary")
    assert out.returncode == 0 and "words_f1           1.0" in out.stdout
