# Alignment benchmark: scorer specification (v1)

Implementation contract for the `qab` scorer. The README is the consumer contract; this document defines the arithmetic. Where they disagree, fix one.

## 1. Scope

The alignment leaderboard: word-level alignment of submitted segments against a word-level ground truth, at any granularity. Word timing and segmentation are independent tasks specified in [TASKS.md](TASKS.md).

**Riwayah.** Every case declares a `riwayah`. v1 admits `hafs_an_asim` only: all `S:A:W` references and the ground truth use Hafs verse and word numbering, and the scorer's bundled word-count table is Hafs. Another riwayah is a new enum value, its own table, and its own corpus config.

Out of scope: per-word *timing* submissions (a segment per word is allowed, its times are not scored), ASR-only submissions, hidden test splits, composite ranking scores.

## 2. Ground truth (`Case`, schema 2)

One record per recording. Times in seconds, float, from the start of the audio. The dataset row carries the same fields plus audio and descriptive columns (docs/CORPUS.md); `qab.corpus.case_from_row` is the mapping.

```json
{
  "schema_version": 2,
  "id": "jaber-inshiqaq",
  "duration_s": 128.208,
  "riwayah": "hafs_an_asim",
  "reciter": "Ali Jaber",
  "description": null,
  "style": "murattal",
  "content": "quran_only",
  "noisy": false,
  "multi_surah": false,
  "words": [
    { "word": "Basmala:1", "start_s": 0.98, "end_s": 1.55 },
    { "word": "84:1:1",    "start_s": 5.78, "end_s": 6.25 }
  ],
  "non_quran": [ { "start_s": 45.0, "end_s": 47.1 } ]
}
```

- `words`: every spoken word instance, in time order. `word` is `S:A:W` for Quran, or `Basmala:k` / `Isti'adha:k` for the k-th word of a formula (Basmala has 4 words, Isti'adha 5). A repeated word or verse appears once per recitation. Instances are non-overlapping and ordered by `start_s`.
- `non_quran`: intervals of speech that is not Quran and not a formula. No labels inside. Disjoint from `words`.
- Facets (`riwayah`, `style`, `content`, `noisy`, `multi_surah`) are the leaderboard's slicing keys; every case carries all five.

Validation: ordered, non-overlapping instances; every Quran `word` resolves against the Hafs table (chapter, verse, word in range); formula indices within the formula length; last instance ends within `duration_s + 1.0`; `non_quran` disjoint from `words`; no unknown fields.

## 3. Submission (schema 1)

One file per case plus `submission.json`.

```json
{
  "schema_version": 1,
  "case_id": "jaber-inshiqaq",
  "runtime_seconds": 3.2,
  "segments": [
    { "start_s": 5.78, "end_s": 22.30, "reference": "84:1:1-84:5:3", "confidence": 0.97 }
  ]
}
```

- `reference`: `S:A:W-S:A:W` in full (same chapter, start ordinal ≤ end ordinal), `Basmala`, `Isti'adha`, or `null`. `S:A-S:A` and bare `S:A:W` are validation errors. A single word is `S:A:W-S:A:W` with equal ends.
- `segments` ordered by `start_s`; `end_s > start_s`. Two consecutive segments may overlap by at most **0.5 s**; more is a validation error.
- `confidence` in [0, 1]; all-or-nothing across the segments that can be Quran-claiming (span or `Basmala` references) of a file, and across the files of one submission (4.9). `Isti'adha` and `null` segments may carry it; it is ignored.
- `runtime_seconds` > 0, optional per file, all-or-nothing across the submission (4.7).
- Unknown fields and non-finite numbers are rejected. A segment ending after `duration_s + 1.0` is a scoring error.

`submission.json`: `system`, `version`, optional `hardware_class` (`cpu` | `gpu`), `hardware` (free text, detail view only), `contact`, `url`. Reported runtime without a `hardware_class` (or without the file) is an error.

A submission set must contain exactly one file per case of the corpus version; a missing or unknown `case_id` is an error, never an empty score.

## 4. Scoring

Definitions:

- **Quran instance**: a `words` entry whose label is `S:A:W`. **Formula instance**: a `Basmala:k` / `Isti'adha:k` entry. Both take part in attribution (4.1) and cleanliness (4.4); only Quran instances and Quran-lane claim words enter the word counts (4.3); formula instances and formula claims are the formula lane (4.8).
- **ordinal(w)**: zero-based word position of `S:A:W` within its chapter.
- **Token**: a Quran instance or claim word is `(chapter, ordinal)`; a formula word is `(kind, k)`.
- **Basmala class** (Hafs): the tokens `1:1:1 … 1:1:4` and `Basmala:1 … Basmala:4` share one matching key, `(class, k)`. Every other token is its own key.
- **Claim K** of a segment: the increasing list of tokens expanded from its reference (`null` → empty; `Basmala` → the 4 class tokens; `Isti'adha` → its 5 tokens; a span → its ordinals, the first four of chapter 1 being class tokens).
- **τ_pad = 2.0 s**, **ε = 0.5 s**: constants of the benchmark version.

### 4.1 Attribution

Every instance is attributed to the segment whose `[start_s, end_s]` contains its midpoint; with the 0.5 s overlap allowance, a midpoint inside two segments goes to the earlier one. An instance inside no segment is **uncovered**; a Quran instance inside a `null` segment is **denied**. The **content C** of a segment is its attributed instances in time order.

### 4.2 Matching

Per segment, match K to C by longest common subsequence on matching keys. K has distinct keys, so the LCS is the longest increasing run of claim positions along C: each claim word matches at most one instance, matches are monotone in time, and a repeated ordinal in C matches at most once. Ties prefer the earliest instances.

**Lanes.** The kind of the matched *instance* decides where a pair counts: a pair on a Quran instance is a Quran-lane match, a pair on a formula instance a formula-lane match. A segment is **Quran-claiming** when its claim holds a plain Quran token, or a class token that matched a Fatiha (`1:1:k`) instance; every token of such a segment, class tokens included, is a **Quran-lane claim word**. A segment is a **formula segment** when its reference is `Isti'adha`, or `Basmala` / a span made only of class tokens none of which matched a Quran instance; its tokens are formula-lane and never false Quran claims. `null` is a **null segment**. So `Basmala` and `1:1:1-1:1:4` behave identically wherever they are placed: over al-Fatiha's opening both are Quran-claiming with four matched words; over any other chapter's opening both are formula segments; over silence both are formula extras. A longer span that includes the class (`1:1:1-1:2:1`) is Quran-claiming and its unmatched class tokens are false claims.

Per segment: **matched** = pairs in the LCS (all kinds); **false claims** = Quran-lane claim words − Quran-lane matches; **unaccounted** = |C| − matched.

### 4.3 Words

- Words found (recall) = Σ Quran-lane matches / |Quran instances|
- Words correct (precision) = Σ Quran-lane matches / Σ Quran-lane claim words
- Words F1 = harmonic mean; 0 when either side is 0 or undefined while the other is defined (an empty submission on a Quran case scores 0); `null` only when there is nothing to score on either side.

### 4.4 Clean segments

A Quran-claiming segment is **clean** iff

1. `|C| == |K| == matched`, counting every instance in C and every token in K, Quran and formula alike; and
2. `max(first.start_s − τ_pad, prev.end_s − ε) ≤ start_s ≤ first.start_s + ε`; and
3. `last.end_s − ε ≤ end_s ≤ min(last.end_s + τ_pad, next.start_s + ε)`; and
4. `end_s − start_s ≥ (last.end_s − first.start_s) / 2`; and
5. the segment overlaps `non_quran` intervals by at most ε in total,

where `first` / `last` are the earliest / latest matched instances and `prev` / `next` are the nearest instances of any kind before / after them that are not in C (absent → that bound is dropped). Conditions 2 to 4 make the segment cover its words: a sliver around a word's midpoint is attributed the word (4.1) but is not clean.

Clean segments = clean / Quran-claiming segments. Formula and null segments are outside both numerator and denominator. A formula segment is **exact** under the same condition 1 (4.8).

### 4.5 Repeats

**Truth events.** Walk Quran instances in time order. An event occurs at instance i when the previous Quran instance is in the same chapter and `ordinal(i) ≤ ordinal(i−1)`. A chapter change resets. The anchor is instance i.

**Predicted events.** Walk Quran-claiming segments in order (formula and null segments are skipped, not resetting). Reading class tokens as `1:1:k`, an event occurs at segment j when its first Quran-lane token and the previous Quran-claiming segment's last one are in the same chapter and `first_j ≤ last_{j−1}`. The anchor is segment j.

**Matching.** A truth event is **caught** iff its anchor instance is matched inside a segment that is a predicted-event anchor; each predicted anchor catches at most one truth event (the earliest). A predicted event that catches nothing is **invented**. The board scores event detection; the distance between the anchor segment's first claimed ordinal and the true restart ordinal is reported as a diagnostic (median repeat position error, in words).

- Repeats caught = caught / truth events; Repeats real = caught / predicted events; Repeats F1 = harmonic mean.
- No truth events and no predicted events → all three `null`, excluded from equal-case means. Truth events with no predictions, or predictions with no truth events, score 0.

### 4.6 Words per segment

Σ Quran-lane claim words / Quran-claiming segments. Descriptor, not ranked.

### 4.7 Runtime

Per case RTF = `runtime_seconds / duration_s`. Pooled RTF = Σ `runtime_seconds` / Σ `duration_s` (duration-weighted), reported under the submission's `hardware_class`. All-or-nothing: runtime on a strict subset of cases scores as not reported. Reported runtime without a `hardware_class` is an error.

### 4.8 Formulas

Scored per occurrence; reported in full, off the leaderboard. An **occurrence** is a run of consecutive formula instances of one kind in `words` with increasing k; a k that does not increase starts a new occurrence (two Basmalas in a row are two). It is **detected** when more than half of its instances are matched inside one segment (4.2); **exact** when that segment matched all of them and satisfies clean condition 1. A formula segment that detects no occurrence is **extra**. Per kind: occurrences, detected, exact, missed (= occurrences − detected), extra. Formula granularity is not free: a formula split over several single-word segments is missed with extras, by design of the majority rule.

Cross-lane consequences fall out of 4.2–4.4: a Quran segment whose range holds formula instances it does not claim is not clean and leaves them unaccounted (the occurrence is missed unless another segment detects it); Quran claimed over formula audio is false claims; a formula segment over Quran audio leaves those Quran instances unaccounted (Words found) and is extra.

### 4.9 Consumer confidence

Confidence is scored once per Quran-claiming segment. Its outcome `y` is 1 when the segment is clean under 4.4 and 0 otherwise. Fixed user-facing tiers are green (`c >= 0.80`), amber (`0.60 <= c < 0.80`), and red (`c < 0.60`).

Let `N` be all Quran-claiming output segments, `Cg` clean green segments, and `Wg` non-clean green segments:

- Trusted coverage = `max(0, (Cg − 4 Wg) / N)`.
- Unsafe green = `Wg / (Cg + Wg)`, undefined when there are no green segments.
- Green coverage = `(Cg + Wg) / N`; safe green coverage = `Cg / N`.

The four-to-one penalty makes 80% green accuracy the break-even point. Amber and red segments remain in `N` and earn zero rather than a penalty. Missing recitation is handled by Words found and Words F1; alignment accepts arbitrary output granularity and therefore has no universal set of reference alignment segments.

A green segment is a critical non-Quran error when it has false Quran claims and more than half its duration overlaps annotated `non_quran` audio. This count is a diagnostic, not a separate weight.

Not reported gives `null` headline values (`state: not_reported`). Confidence is all-or-nothing across the submission: reporting it on a strict subset of cases scores as not reported. The detailed report includes segment counts, correctness and accuracy for every tier; green precision; segment-level Brier, Brier skill, AUROC and 10-bin ECE; minimum confidence on a correct segment; and maximum confidence on a wrong segment. Calibration diagnostics do not affect rank.

### 4.10 Diagnostics (detail view)

Denied Quran words; uncovered Quran words; false claims of segments more than half of whose duration overlaps `non_quran` intervals; word-weighted clean fraction (Quran instances inside clean segments / all Quran instances); median repeat position error; per-segment table (lane, claim words, content words, matched, false, unaccounted, clean, non-Quran overlap).

## 5. Aggregation and report

- **Pooled** (the leaderboard row): the underlying counts summed across all cases, then the ratios. Consumer confidence pools Quran segments; RTF pools seconds. Nothing on the leaderboard is a mean of per-case values.
- **Equal-case**: unweighted mean of per-case values, skipping undefined ones. Detail view only.
- **Slices**: pooled and equal-case over the cases sharing a facet value, keyed `facet=value` (`style=hadr`, `noisy=true`, `riwayah=hafs_an_asim`, ...).

Pooling makes long recordings weigh more; that is intended (the word is the unit) and is balanced by corpus composition and slices, not by averaging.

The report is a JSON document: `scorer_version`, `corpus_version` (the label the scorer was given), `corpus_fingerprint` (sha256 of the scored truth: ids, durations, riwayah, words, non_quran; the leaderboard checks it against the selected dataset version), `submission` (metadata echoed), `runtime_reported`, `confidence_reported`, `headline` (words_f1, clean_segments, repeats_f1, words_per_segment, rtf + hardware_class, trusted_coverage, unsafe_green), `pooled`, `equal_case`, `slices`, `formulas`, `confidence`, `diagnostics`, `cases[]`.

## 6. Tooling

- `qab validate <dir>`: schema, ordering, overlap, reference validity; non-zero exit on any error.
- `qab score <dir> [--corpus vX] [--cases dir] [--out file] [--summary]`: validates, then the full report. Deterministic; no network with `--cases`.
- `qab fetch --corpus vX [--revision sha] --out dir`: audio and case JSON per recording from the Hub.
- Library: `evaluate(load_cases(version, revision=...), submissions, meta, corpus_version=version) -> dict`. A corpus must be non-empty with unique ids; a submission set must cover it exactly.
- The Hafs table (`qab/data/hafs.json`) is the per-verse word count of the QUL QPC Hafs text (`text_qpc_hafs`, v3.12), whitespace-tokenised: 6236 verses, 77,433 words. A submitter's word indices must come from the same tokenisation.

## 7. Tests (tests/test_scoring.py)

Granularity invariance (word counts); repeat blindness and catch; micro-segments not clean; segment must cover its words; non-Quran speech not clean; leak into a formula neighbour; unmatched class tokens in a Quran span are false; Basmala confidence consistency; empty submission scores 0; missed repeats score 0; consecutive Basmalas are two occurrences; exact needs every word; non-finite rejected; segment beyond audio; runtime needs metadata; duplicate or empty corpus; fingerprint and position error; CLI validate and score; invented repeat (`0-9, 7-9` over `0-11`: 3 false, 2 missed, one invented); chapter change resets repeats; clean pad 1.9 s vs 2.1 s; leak 0.4 s vs 0.6 s; nothing-extra rule; null vs gap (same words, different `denied`/`uncovered`); formula segment off the headline; Quran swallowing a Basmala not clean and formula missed; `Basmala` over Quran audio costs those words and is extra; Quran over formula audio is false; Basmala class identical over Fatiha and over another chapter's opening; a Fatiha span beyond the class is Quran; fixed confidence tier edges; four-to-one unsafe-green penalty; confidence and runtime all-or-nothing across cases; runtime requires hardware_class; overlap 0.4 s accepted, 0.6 s rejected; shorthand references rejected; single-word span accepted; overlap attribution to the earlier segment; pooling is not a mean; missing case is an error; facet slices; no repeats anywhere is `null`; false-over-non-Quran diagnostic.

## 8. Roadmap boards

- **Word timing**: per-word submissions `{word, start_s, end_s}`; median absolute onset/offset error, fraction within 50 / 100 / 200 ms.
- **Segmentation**: submitted intervals against the corpus `segments` column with boundary-tolerant pairing. Text-free.
