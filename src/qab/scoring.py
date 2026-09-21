"""Per-case scoring: attribution, matching, word counts, clean segments, repeats, formulas, confidence.

Implements docs/SPEC.md section 4. Everything is counted in words (instances and
claim tokens); segment granularity never enters a ratio.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from . import refs
from .schema import DURATION_SLACK_S, Case, Segment, Submission

TAU_PAD_S = 2.0
EPSILON_S = 0.5


@dataclass
class SegmentScore:
    index: int
    reference: str | None
    lane: str                      # "quran" | "formula" | "null"
    claim_words: int               # Quran-lane claim tokens (|K| in 4.3)
    content_words: int             # attributed instances of every kind (|C|)
    matched: int                   # matched pairs of every kind
    matched_quran: int
    false_claims: int
    unaccounted: int
    clean: bool
    confidence: float | None
    non_quran_overlap_s: float


@dataclass
class FormulaCounts:
    occurrences: int = 0
    detected: int = 0
    exact: int = 0
    extra: int = 0

    @property
    def missed(self) -> int:
        return self.occurrences - self.detected


@dataclass
class CaseScore:
    case_id: str
    duration_s: float
    facets: dict[str, str]
    quran_instances: int
    matched_words: int
    claimed_words: int
    quran_segments: int
    clean_segments: int
    truth_repeats: int
    predicted_repeats: int
    caught_repeats: int
    repeat_position_errors: list[int]           # |first claimed ordinal - anchor ordinal| per caught event
    formulas: dict[str, FormulaCounts]
    confidence_segments: list[tuple[float, int, bool]]  # (confidence, clean, critical non-Quran)
    runtime_seconds: float | None
    denied: int
    uncovered: int
    false_over_non_quran: int
    instances_in_clean: int
    segments: list[SegmentScore] = field(default_factory=list)

    @property
    def words_found(self) -> float | None:
        return self.matched_words / self.quran_instances if self.quran_instances else None

    @property
    def words_correct(self) -> float | None:
        return self.matched_words / self.claimed_words if self.claimed_words else None


def f1(precision: float | None, recall: float | None) -> float | None:
    """Harmonic mean; 0 when either side is 0 or undefined while the other is defined."""
    if precision is None and recall is None:
        return None
    if not precision or not recall:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# --- attribution -----------------------------------------------------------------------------

def attribute(segments: list[Segment], midpoints: list[float]) -> list[int | None]:
    """Segment index containing each midpoint; the earliest segment on overlap; None if uncovered."""
    starts = [s.start_s for s in segments]
    prefix_max_end: list[float] = []
    running = float("-inf")
    for s in segments:
        running = max(running, s.end_s)
        prefix_max_end.append(running)
    out: list[int | None] = []
    for mid in midpoints:
        last = bisect.bisect_right(starts, mid) - 1
        if last < 0:
            out.append(None)
            continue
        first = bisect.bisect_left(prefix_max_end, mid, 0, last + 1)
        if first <= last and segments[first].start_s <= mid <= segments[first].end_s:
            out.append(first)
        else:
            out.append(None)
    return out


# --- matching --------------------------------------------------------------------------------

def match(claim_keys: list[tuple], content_keys: list[tuple]) -> list[tuple[int, int]]:
    """LCS of the claim (distinct, increasing keys) against the content; earliest instances on ties.

    Returns (content position, claim position) pairs. Because claim keys are
    distinct, the LCS is the longest strictly increasing subsequence of claim
    positions along the content, computed in O(n log n).
    """
    pos = {key: i for i, key in enumerate(claim_keys)}
    mapped = [(j, pos[key]) for j, key in enumerate(content_keys) if key in pos]
    if not mapped:
        return []
    n = len(mapped)
    best_from = [1] * n
    heads: list[int] = []   # heads[l]: max claim position heading a chain of length l+1 to the right
    for i in range(n - 1, -1, -1):
        p = mapped[i][1]
        lo, hi = 0, len(heads)
        while lo < hi:
            m = (lo + hi) // 2
            if heads[m] > p:
                lo = m + 1
            else:
                hi = m
        best_from[i] = lo + 1
        if lo == len(heads):
            heads.append(p)
        elif heads[lo] < p:
            heads[lo] = p
    need, last_p = max(best_from), -1
    pairs: list[tuple[int, int]] = []
    for i in range(n):
        j, p = mapped[i]
        if p > last_p and best_from[i] == need:
            pairs.append((j, p))
            last_p = p
            need -= 1
            if need == 0:
                break
    return pairs


# --- case scoring ----------------------------------------------------------------------------

def score_case(case: Case, submission: Submission) -> CaseScore:
    if submission.case_id != case.id:
        raise ValueError(f"submission {submission.case_id!r} scored against case {case.id!r}")
    segments = submission.segments
    if segments and segments[-1].end_s > case.duration_s + DURATION_SLACK_S:
        raise ValueError(f"{case.id}: a segment ends after the audio ({segments[-1].end_s:.3f} s)")
    words = case.words
    tokens = [w.token for w in words]
    keys = [refs.match_key(t) for t in tokens]
    is_quran = [t[0] == "q" for t in tokens]
    owner = attribute(segments, [w.midpoint for w in words])

    content: list[list[int]] = [[] for _ in segments]
    for i, seg_index in enumerate(owner):
        if seg_index is not None:
            content[seg_index].append(i)

    matched_instance_in: dict[int, int] = {}
    seg_claim_tokens = [refs.claim_tokens(s.reference) for s in segments]
    seg_matches: list[list[tuple[int, int]]] = []     # (instance index, claim position)
    for j, seg in enumerate(segments):
        claim_keys = [refs.match_key(t) for t in seg_claim_tokens[j]]
        pairs = match(claim_keys, [keys[i] for i in content[j]])
        resolved = [(content[j][cpos], kpos) for cpos, kpos in pairs]
        seg_matches.append(resolved)
        for i, _ in resolved:
            matched_instance_in[i] = j

    seg_scores: list[SegmentScore] = []
    lane: list[str] = []
    quran_lane_tokens: list[list[int]] = []
    for j, seg in enumerate(segments):
        ctoks = seg_claim_tokens[j]
        matched_kpos = {kpos: i for i, kpos in seg_matches[j]}
        # A segment is Quran-claiming when it has a plain Quran token or a Basmala-class token
        # that matched a Fatiha instance; then every one of its tokens is a Quran-lane claim
        # word. A reference made only of class tokens that matched formula instances (or
        # nothing) is a formula segment, whichever spelling it used.
        plain = any(t[0] == "q" and not refs.in_basmala_class(t) for t in ctoks)
        class_to_quran = any(refs.in_basmala_class(t) and k in matched_kpos and is_quran[matched_kpos[k]]
                             for k, t in enumerate(ctoks))
        if seg.reference is None:
            seg_lane, qtoks = "null", []
        elif plain or class_to_quran:
            seg_lane, qtoks = "quran", list(range(len(ctoks)))
        else:
            seg_lane, qtoks = "formula", []
        lane.append(seg_lane)
        quran_lane_tokens.append(qtoks)
        matched_quran = sum(1 for i, _ in seg_matches[j] if is_quran[i])
        claim_words = len(qtoks)
        unaccounted = len(content[j]) - len(seg_matches[j])
        nq_overlap = sum(max(0.0, min(seg.end_s, iv.end_s) - max(seg.start_s, iv.start_s)) for iv in case.non_quran)
        if seg_lane == "quran":
            clean = _clean(seg, content[j], seg_matches[j], ctoks, words, nq_overlap)
        elif seg_lane == "formula":
            clean = len(content[j]) == len(ctoks) == len(seg_matches[j])
        else:
            clean = False
        seg_scores.append(SegmentScore(
            index=j, reference=seg.reference, lane=seg_lane, claim_words=claim_words,
            content_words=len(content[j]), matched=len(seg_matches[j]), matched_quran=matched_quran,
            false_claims=claim_words - matched_quran, unaccounted=unaccounted, clean=clean,
            confidence=seg.confidence if seg_lane == "quran" else None, non_quran_overlap_s=nq_overlap))

    truth_anchors = _truth_repeat_anchors(tokens, is_quran)
    pred_anchors = _predicted_repeat_anchors(lane, seg_claim_tokens, quran_lane_tokens)
    caught, used, position_errors = 0, set(), []
    for i in truth_anchors:
        j = matched_instance_in.get(i)
        if j is not None and j in pred_anchors and j not in used:
            caught += 1
            used.add(j)
            position_errors.append(abs(pred_anchors[j] - tokens[i][2]))

    formulas = _formulas(tokens, segments, lane, seg_claim_tokens, seg_matches, content, matched_instance_in)

    conf_segments: list[tuple[float, int, bool]] = []
    if submission.confidence_reported:
        for j, s in enumerate(seg_scores):
            if s.lane != "quran":
                continue
            if segments[j].confidence is None:
                raise ValueError(f"{case.id}: Quran-claiming segment {j} carries no confidence")
            duration = segments[j].end_s - segments[j].start_s
            critical = s.false_claims > 0 and s.non_quran_overlap_s * 2 > duration
            conf_segments.append((segments[j].confidence, int(s.clean), critical))

    denied = sum(1 for i, j in enumerate(owner) if is_quran[i] and j is not None and lane[j] == "null")
    uncovered = sum(1 for i, j in enumerate(owner) if is_quran[i] and j is None)
    in_clean = sum(1 for i, j in enumerate(owner)
                   if is_quran[i] and j is not None and lane[j] == "quran" and seg_scores[j].clean)
    over_nq = sum(s.false_claims for j, s in enumerate(seg_scores)
                  if s.non_quran_overlap_s * 2 > segments[j].end_s - segments[j].start_s)
    return CaseScore(
        case_id=case.id, duration_s=case.duration_s, facets=case.facets,
        quran_instances=sum(is_quran),
        matched_words=sum(s.matched_quran for s in seg_scores),
        claimed_words=sum(s.claim_words for s in seg_scores),
        quran_segments=sum(1 for s in seg_scores if s.lane == "quran"),
        clean_segments=sum(1 for s in seg_scores if s.lane == "quran" and s.clean),
        truth_repeats=len(truth_anchors), predicted_repeats=len(pred_anchors), caught_repeats=caught,
        repeat_position_errors=position_errors,
        formulas=formulas, confidence_segments=conf_segments, runtime_seconds=submission.runtime_seconds,
        denied=denied, uncovered=uncovered, false_over_non_quran=over_nq,
        instances_in_clean=in_clean, segments=seg_scores)


def _clean(seg, content_j, matches, claim_tokens, words, nq_overlap) -> bool:
    """SPEC 4.4: exactly the claimed words; edges within tau_pad outside them and epsilon inside
    them, at most epsilon into a neighbouring instance; the segment at least half as long as the
    span of its words; at most epsilon of non-Quran speech."""
    if not (len(content_j) == len(claim_tokens) == len(matches)):
        return False
    if nq_overlap > EPSILON_S + 1e-9:
        return False
    matched_instances = [i for i, _ in matches]
    lo_i, hi_i = min(matched_instances), max(matched_instances)
    inside = set(content_j)
    prev_end = next((words[i].end_s for i in range(lo_i - 1, -1, -1) if i not in inside), None)
    next_start = next((words[i].start_s for i in range(hi_i + 1, len(words)) if i not in inside), None)
    first, last = words[lo_i], words[hi_i]
    lower = first.start_s - TAU_PAD_S
    if prev_end is not None:
        lower = max(lower, prev_end - EPSILON_S)
    upper = last.end_s + TAU_PAD_S
    if next_start is not None:
        upper = min(upper, next_start + EPSILON_S)
    covers = (seg.end_s - seg.start_s) >= 0.5 * (last.end_s - first.start_s) - 1e-9
    return (covers and lower - 1e-9 <= seg.start_s <= first.start_s + EPSILON_S + 1e-9
            and last.end_s - EPSILON_S - 1e-9 <= seg.end_s <= upper + 1e-9)


def _truth_repeat_anchors(tokens, is_quran) -> list[int]:
    anchors, prev = [], None
    for i, t in enumerate(tokens):
        if not is_quran[i]:
            continue
        chapter, ordinal_ = t[1], t[2]
        if prev is not None and prev[0] == chapter and ordinal_ <= prev[1]:
            anchors.append(i)
        prev = (chapter, ordinal_)
    return anchors


def _predicted_repeat_anchors(lane, claim_tokens, quran_lane_tokens) -> dict[int, int]:
    """Anchor segment index -> its first claimed ordinal."""
    anchors: dict[int, int] = {}
    prev: tuple[int, int] | None = None
    for j, seg_lane in enumerate(lane):
        if seg_lane != "quran":
            continue
        toks = [_as_quran(claim_tokens[j][k]) for k in quran_lane_tokens[j]]
        chapter, first, last = toks[0][0], toks[0][1], toks[-1][1]
        if prev is not None and prev[0] == chapter and first <= prev[1]:
            anchors[j] = first
        prev = (chapter, last)
    return anchors


def _as_quran(token) -> tuple[int, int]:
    """(chapter, ordinal) of a Quran-lane claim token; Basmala:k reads as al-Fatiha 1:1:k."""
    if token[0] == "f":
        return (1, token[2] - 1)
    return (token[1], token[2])


def _formulas(tokens, segments, lane, claim_tokens, seg_matches, content, matched_in) -> dict[str, FormulaCounts]:
    """Formula lane (SPEC 4.8). An occurrence is a run of consecutive same-kind formula instances
    with increasing k (a restart opens a new occurrence); detected when more than half of its
    instances are matched inside one segment; exact when that segment matched all of them and
    holds nothing else."""
    counts = {kind: FormulaCounts() for kind in refs.FORMULAS}
    runs: list[tuple[str, list[int]]] = []
    for i, t in enumerate(tokens):
        if t[0] != "f":
            continue
        continues = (runs and runs[-1][0] == t[1] and runs[-1][1][-1] == i - 1
                     and tokens[runs[-1][1][-1]][2] < t[2])
        if continues:
            runs[-1][1].append(i)
        else:
            runs.append((t[1], [i]))
    detecting: set[int] = set()
    for kind, members in runs:
        counts[kind].occurrences += 1
        by_segment: dict[int, int] = {}
        for i in members:
            j = matched_in.get(i)
            if j is not None:
                by_segment[j] = by_segment.get(j, 0) + 1
        if not by_segment:
            continue
        j, n = max(by_segment.items(), key=lambda kv: (kv[1], -kv[0]))
        if n * 2 > len(members):
            counts[kind].detected += 1
            detecting.add(j)
            if n == len(members) and len(content[j]) == len(claim_tokens[j]) == len(seg_matches[j]):
                counts[kind].exact += 1
    for j, seg in enumerate(segments):
        if lane[j] == "formula" and j not in detecting:
            kind = seg.reference if seg.reference in refs.FORMULAS else refs.BASMALA
            counts[kind].extra += 1
    return counts
