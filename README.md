# Quran Recitation Alignment Benchmark

[![Dataset](https://img.shields.io/badge/%F0%9F%A4%97%20Dataset-quran--alignment--benchmark-blue)](https://huggingface.co/datasets/hetchyy/quran-alignment-benchmark) [![Leaderboard](https://img.shields.io/badge/Leaderboard-quran--alignment--leaderboard-blue)](https://huggingface.co/spaces/hetchyy/quran-alignment-leaderboard) [![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-green)](https://creativecommons.org/licenses/by/4.0/)

A public benchmark for systems that align Quran recitation audio to the text. Given a recording, a system produces timed segments, each labelled with the exact words recited in it. The benchmark scores those labels against a word-by-word ground truth and publishes the results on a leaderboard.

- Corpus: [`hetchyy/quran-alignment-benchmark`](https://huggingface.co/datasets/hetchyy/quran-alignment-benchmark) on the Hugging Face Hub (audio, ground truth, descriptive columns)
- Scorer: this repository, installed from a git tag (package and CLI `qab`)
- Specification: [docs/SPEC.md](docs/SPEC.md) · Corpus notes: [docs/CORPUS.md](docs/CORPUS.md)
- Leaderboard: [browse results, dataset, metrics, and submissions](https://huggingface.co/spaces/hetchyy/quran-alignment-leaderboard)

## The task

**Input.** An audio recording of recitation. Any length, any reciter, any style. It may contain repeated words and verses, long pauses, the opening formulas (Isti'adha, Basmala), and speech that is not Quran.

**Output.** An ordered list of segments. Each segment is a time range and a claim about what was recited inside it:

- a **word span** such as `2:5:1-2:6:3` (chapter 2, verse 5 word 1 through verse 6 word 3), always within one chapter and always in reading order;
- `Basmala` or `Isti'adha`;
- `null`, for audio that is not Quran.

Segments may be any size: one per stop, one per verse, one per word, one per any other splitting method. The benchmark scores what a segment claims against what was recited in that time and does not prescribe granularity: the word counts are the same whichever way a correct alignment is cut. Only *Clean segments* is counted per segment, and it is read next to *Words / segment*.

**Riwayah.** v1 is Hafs ʿan ʿĀṣim only. Verse and word numbering follow Hafs throughout. Every recording carries a `riwayah` column so other readings can join later with their own numbering and corpus.

## Who it is for

**Developers building or improving a system.** A fixed corpus, a fixed scorer, and a shared vocabulary for comparing approaches.

**End users choosing a system.** Anyone who needs recitation audio tied to its text and wants to know which system to trust for their material:

- *Captioned video.* Every segment becomes a caption. How many captions are correct as displayed, how many have errors, and does the system notice when the reciter repeats a verse?
- *Players and apps.* Seeking to a verse, isolating a verse, looping a passage. Is a verse's audio cut where the verse starts and ends, and is a repeated verse treated as two?

Other uses: memorisation and tajweed tools, searchable recitation archives, dataset construction, research, and isolating Quran speech from other audio (khutbahs, duas, lectures).

## How scoring works

The ground truth for every recording is a **word-level timeline**: each recited word, in the order it was recited, with its start and end time. If the reciter recites a verse twice, the timeline contains it twice.

For each submitted segment, the benchmark collects the recited words whose midpoint falls inside its time range and compares them with the words the segment claims:

- A segment that claims words which were not recited in its range loses those words.
- A segment whose range contains recited words it does not claim leaves those words unaccounted for.
- A segment that spans a repeat (a backward jump in the recitation) but claims a single run of text accounts for only one part; the other is left unaccounted for.

Word counts never depend on segment size: a system emitting one segment per verse and one emitting one per stop are scored on the same recited words, and a correct alignment scores the same at either granularity. Coarser segments hide boundary errors inside them; that shows up in *Clean segments*, not in the word counts.

Opening formulas (`Basmala`, `Isti'adha`) are scored in their own report and do not enter the headline word counts, but they are not ignored: a Quran segment that swallows a formula's audio is not clean, Quran claimed over a formula is a false claim, and Quran audio labelled as a formula counts as words not found. `null` segments are accepted and reported.

The full rules are in [docs/SPEC.md](docs/SPEC.md).

## The leaderboard

Scores are ratios in 0 to 1 unless stated; higher is better unless stated.

Leaderboard values are pooled over the whole corpus: counts are summed across all recordings before a ratio is taken, so every recording contributes in proportion to its words. Nothing is averaged per recording. Per-recording values and slices by `riwayah`, `style`, `content`, `noisy` and `multi_surah` are in the detail view; the leaderboard can be filtered by any of them.

**Words F1** *(balance of found and correct)*. The headline. Combines *Words found* (of the words recited, how many the system accounted for) and *Words correct* (of the words the system claimed, how many were really recited where it said).

**Clean segments** *(fraction of segments with zero errors)*. A segment is clean when its range holds exactly the words it claims and covers them: nothing missing, nothing extra, no non-Quran speech inside, edges within half a second of the words' own edges on the inside and at most 2 s of silence beyond them on the outside, at most half a second into a neighbouring word. For captioned video this is the share of captions that are right as displayed. It is per segment, so read it next to *Words / segment*.

**Repeats F1** *(balance of caught and real)*. A repeat is the reciter going back: re-reciting words already recited before continuing. A submission marks one by opening a new segment whose claim starts at or before where the previous claim ended. Combines *Repeats caught* and *Repeats real*. It scores whether the event was marked and the restart word landed in that segment; how far the claimed restart is from the real one is a detail-view diagnostic.

**Words / segment** *(granularity)*. Average number of claimed words per segment. Not a score; context for the others.

**RTF** *(processing time per second of audio, optional, lower is better)*. Self-reported, shown separately for `cpu` and `gpu`. Reported for every recording or for none.

**Trusted coverage** and **Unsafe green** *(consumer confidence, optional)*. Confidence is scored once per Quran segment. Scores at least 0.80 are green (safe to use), 0.60 to below 0.80 are amber (review recommended), and below 0.60 are red (probably incorrect). Trusted coverage rewards correct green segments and makes each incorrect green segment cancel four correct ones. Unsafe green is the share of green segments that are not clean. The detailed report retains tier counts and probability-calibration diagnostics.

## What is in the corpus (v1)

16 recordings, 357 minutes, 18,421 recited words, 213 repeat events, Hafs ʿan ʿĀṣim. Four styles, single and multi-chapter, clean and degraded captures, dense repeats, adult and child voices, formulas and non-Quran speech (prayer captures with athan and dua).

| id | reciter | style | content | noisy | passages | min | words | wpm | repeats |
|---|---|---|---|---|---|---|---|---|---|
| `luhaidan-fatir` | Muhammad Al-Luhaidan | murattal | quran_only | yes | Faatir | 18.2 | 898 | 50 | 27 |
| `luhaidan-haqqah` | Muhammad Al-Luhaidan | murattal | quran_only | yes | Al-Haaqqa 25-37 | 2.4 | 96 | 41 | 13 |
| `luhaidan-qiyamah` | Muhammad Al-Luhaidan | murattal | quran_only | yes | Al-Qiyaama (+ Fatiha) | 4.0 | 189 | 50 | 0 |
| `jaber-inshiqaq` | Ali Jaber | murattal | quran_only | | Al-Inshiqaaq | 2.1 | 107 | 58 | 0 |
| `jaber-nisa` | Ali Jaber | hadr | quran_only | | An-Nisaa 100-102 | 1.0 | 116 | 121 | 0 |
| `afasy-baqarah` | Mishary Al-Afasy | hadr | quran_only | | Al-Baqara | 79.9 | 6186 | 78 | 28 |
| `mandour-taha` | Ahmed Mandour | murattal | quran_only | yes | Taa-Haa 1-98 | 21.4 | 1066 | 51 | 31 |
| `ayyub-juz-amma` | Muhammad Ayyub | murattal | quran_only | | Juz' Amma | 57.9 | 2354 | 46 | 13 |
| `badri-juz-tabarak` | Abdul Qadir Badri | hadr | quran_only | | Juz' Tabarak | 24.2 | 2661 | 114 | 0 |
| `abdulbasit-raad` | Abdul Basit Abdul Samad | mujawwad | quran_only | | Ar-Ra'd | 38.1 | 869 | 28 | 3 |
| `diban-sad` | Ahmed Diban | hadr | quran_only | | Saad | 4.4 | 736 | 170 | 1 |
| `husary-fath` | Mahmoud Khalil Al-Husary | mujawwad | quran_only | | Al-Fath | 24.8 | 584 | 30 | 7 |
| `prayer-baqarah-yasin` | Adil Yusuf | murattal | prayer | | Al-Baqara 285-286, Yaseen 77-83 | 12.5 | 209 | 42 | 4 |
| `witr-muawwidhat` | Mishary Al-Afasy | murattal | prayer | | Al-Ikhlaas, Al-Falaq, An-Naas | 11.9 | 133 | 44 | 0 |
| `taraweeh-maryam` | Ibrahim Idris | murattal | prayer | | Maryam 12-57, Al-Furqaan 61-77, An-Naba 1-40, Al-Aadiyaat, Al-Qadr, Al-Maa'un | 34.4 | 1099 | 44 | 14 |
| `minshawi-luqman` | Muhammad Siddiq Al-Minshawi | muallim | quran_only | yes | Luqman | 19.3 | 1118 | 58 | 72 |

Column meanings, how the ground truth was produced, and what v1 does not yet cover are in [docs/CORPUS.md](docs/CORPUS.md).

## Submission format

One JSON file per recording, named `<id>.json`, plus one `submission.json` with the metadata.

```json
{
  "schema_version": 1,
  "case_id": "jaber-inshiqaq",
  "runtime_seconds": 3.2,
  "segments": [
    { "start_s": 0.98,  "end_s": 4.84,   "reference": "Basmala" },
    { "start_s": 5.78,  "end_s": 22.30,  "reference": "84:1:1-84:5:3",  "confidence": 0.97 },
    { "start_s": 23.42, "end_s": 33.12,  "reference": "84:6:1-84:6:8",  "confidence": 0.99 },
    { "start_s": 34.20, "end_s": 44.33,  "reference": "84:7:1-84:8:4",  "confidence": 0.91 },
    { "start_s": 45.00, "end_s": 47.10,  "reference": null },
    { "start_s": 47.50, "end_s": 52.53,  "reference": "84:6:1-84:7:4",  "confidence": 0.91 }
  ]
}
```

The last segment claims words already claimed two segments earlier: that is how a submission marks a repeat.

| Field | Meaning |
|---|---|
| `case_id` | Recording `id` from the corpus. |
| `segments` | Ordered by `start_s`. Consecutive segments may overlap by at most 0.5 s. |
| `reference` | A word span `S:A:W-S:A:W` within one chapter and in reading order; or `Basmala`; or `Isti'adha`; or `null`. |
| `confidence` | Optional. The system's score, 0 to 1, that the segment is safe to use as supplied. Green is ≥0.80, amber is ≥0.60 and <0.80, and red is <0.60. Either every Quran segment carries one or none does, across the whole submission. |
| `runtime_seconds` | Optional. Total processing time for this recording. Reported for every recording or for none. |

A span is always written in full as `surah:ayah:word-surah:ayah:word`, with words numbered from 1 within each verse. A single word is `2:5:1-2:5:1`. Whole-verse or bare-word shorthands are rejected by the validator.

In Hafs, verse 1:1 of al-Fatiha is the Basmala, and the same text opens every other chapter. An opening Basmala may be submitted anywhere either as `Basmala` or as `1:1:1-1:1:4`; the two score the same.

`submission.json`:

```json
{
  "system": "example-aligner",
  "version": "1.4.0",
  "hardware_class": "gpu",
  "hardware": "1x NVIDIA L40S, 8 vCPU",
  "contact": "you@example.org",
  "url": "https://example.org/aligner"
}
```

`hardware_class` (`cpu` | `gpu`) is required when runtime is reported.

## Running your system and scoring locally

```bash
pip install "qab[corpus] @ git+https://github.com/Hetchy/quran-alignment-benchmark@v0.2.0"

qab fetch --corpus v1 --out corpus/          # <id>.mp3 and <id>.json (the ground truth) per recording
# run your system over corpus/*.mp3 and write submissions/<id>.json
qab validate submissions/
qab score submissions/ --corpus v1 --summary  # or --out report.json for the full report
```

Ground truth is public; local scores are the leaderboard scores. `qab score --cases corpus/` scores against the fetched copy without touching the network. Every report names the scorer version and a fingerprint of the truth it was scored against.

Because the truth is public, a score says how a system performs on these recordings when run without access to them: do not train, tune or look up on the corpus audio or annotations. Submissions are taken on trust; the leaderboard reports performance on this corpus, not a guarantee on other material.

From Python:

```python
from qab import load_cases, load_submissions, evaluate

cases = load_cases("v1")                        # from the Hub; or load_cases(cases_dir="corpus/")
submissions, meta = load_submissions("submissions/")
report = evaluate(cases, submissions, meta, corpus_version="v1")
print(report["headline"])
```

Converting your system's output: if its native output is per verse, per word, or per stop, emit it as is; do not merge or split to match any expected granularity. If it outputs one label for a span that covers a repeat, decide how it represents repeats: the benchmark recognises a repeat only as a new segment whose claim starts at or before the previous claim's end.

## How to submit

Use the **Submit** tab in the [leaderboard](https://huggingface.co/spaces/hetchyy/quran-alignment-leaderboard). Enter your system's name, description, website/repository, private contact email, and the number of result-affecting parameters exposed to intended users. For a demo these are user controls; for a package these are documented options, not internal developer settings. When the count is nonzero, briefly explain how those controls help with different recordings in the description.

Upload a ZIP or individual `<id>.json` prediction files. The website validates each recording and the whole submission, computes a private score preview, and creates the submission metadata and benchmark versions automatically. Website uploads may omit `case_id` and `schema_version`; the filename supplies the ID. The local format above remains supported.

You can preview a partial upload, but publication requires valid results for every recording in the latest corpus. Sign in with Hugging Face, review the results, and explicitly confirm publication. Your account privately owns the system name. CPU and GPU runs use the same system name but appear as separate rows, labelled (CPU) and (GPU). Resubmitting replaces only the selected run profile on the same task/corpus; previous submissions are retained privately. Prediction files, account identity, and contact email are never public. Files are retained indefinitely for reproducibility and integrity checks.

Inputs to the system are audio only. Publicly exposed user controls such as silence thresholds and style settings are allowed. Benchmark annotations, known-passage hints, training/tuning on this corpus, and manual prediction corrections are not allowed. Local scoring remains available without submitting.

## Corpus issues and new audio

[Report an audio or ground-truth problem](https://github.com/Hetchy/quran-alignment-benchmark/issues/new?template=corpus-issue.yml) through a GitHub issue.

To [suggest new audio](https://github.com/Hetchy/quran-alignment-benchmark/issues/new?template=new-audio.yml), provide only:

- An audio link or file.
- The reciter's name.
- Why it would be a useful addition to the corpus.

No other metadata or annotations are needed. If accepted, the recording will be ingested, annotated, and released in the next corpus version.

## Segmentation & Timing

**v1** includes the alignment leaderboard described above and two optional parallel leaderboards on the same recordings:

- **Word timing.** For systems that output per-word times: how close each word's start and end are to the ground truth.
- **Segmentation.** For systems that detect where the reciter stops (waqf): how well the detected segments match the actual waqf.

The Space also accepts independently scored **segmentation** and **word timing** results under the same system identity. CPU/GPU runs and task replacements are independent. Select one or several tasks, upload files, preview all selected tasks together, and publish them with one confirmation.

- Segmentation receives the full recording and returns `{"segments": [{"start_s": 1.0, "end_s": 4.0}]}`. No text or references are required. `runtime_seconds` is optional, with CPU/GPU hardware metadata.
- Timing receives the existing reviewed clips and their supplied references. Return one `<recording-id>.json` with `{"clips": [{"words": [{"start_s": 0.0, "end_s": 0.7}, null]}, {"words": null}]}` in dataset segment and word order. Times are clip-relative. `words: null` means all words in that clip are untimed. No runtime field.
- For multiple tasks in one ZIP, use `alignment/<id>.json`, `segmentation/<id>.json`, and `timing/<id>.json`. For one task, a flat ZIP or individual JSON files work. The Space handles metadata and versioning.

From a checkout of this repository, install `pip install -e ".[corpus]"` to use the new task commands. Prepare timing inputs from the selected corpus (requires FFmpeg on PATH):

```bash
qab fetch --corpus v1 --out corpus
qab prepare-timing --cases corpus --out timing-inputs
qab score predictions --task timing --cases corpus --out timing-report.json
qab score predictions --task segmentation --cases corpus --out segmentation-report.json
```

`prepare-timing` uses the existing segment start/end boundaries without added context. Each recording's input JSON lists its WAV clips, references, and ordered words without target word timestamps. Local case files from older fetches must be fetched again to include reviewed segments.

The Space's **Metrics** tab explains all scores and tolerances. The implementation contract for the additional tasks is [docs/TASKS.md](docs/TASKS.md).

## Versioning

The corpus is versioned (dataset config `v1`, `v2`, ...). A score is always attached to the corpus version it was computed on. The website opens on the latest version; historical corpus versions have separate read-only views and never mix with current results. Only the latest corpus accepts submissions. The report carries `corpus_version` and `scorer_version`.

## License

[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) for everything in this repository and for the dataset's annotations and descriptive columns. The audio recordings are not covered by this license.
