---
license: cc-by-4.0
pretty_name: Quran Recitation Alignment Benchmark
language:
  - ar
task_categories:
  - automatic-speech-recognition
tags:
  - quran
  - recitation
  - forced-alignment
  - benchmark
size_categories:
  - n<1K
{{CONFIGS}}
---

# Quran Recitation Alignment Benchmark

[![GitHub](https://img.shields.io/badge/GitHub-quran--alignment--benchmark-black?logo=github)](https://github.com/QUD-Technologies/quran-alignment-benchmark) [![Leaderboard](https://img.shields.io/badge/Leaderboard-quran--alignment--leaderboard-blue)](https://huggingface.co/spaces/hetchyy/quran-alignment-leaderboard)

Audio recordings of Quran recitation with a reviewed word-level ground truth: every recited word, in the order it was recited, with its start and end time, plus the reviewed segmentation and non-Quran regions. This is the corpus behind the [Quran Recitation Alignment Benchmark](https://github.com/QUD-Technologies/quran-alignment-benchmark); the task, scoring rules, leaderboard and submission format are documented there, not here.

16 recordings · 357 minutes · 18,421 recited words · Hafs ʿan ʿĀṣim · murattal, mujawwad, hadr and muallim · studio, prayer and teaching captures.

## Using the dataset

One config per corpus version, one split.

```python
from datasets import load_dataset

ds = load_dataset("hetchyy/quran-alignment-benchmark", "v1", split="test")
row = ds[0]
row["audio"]            # decoded audio; use Audio(decode=False) to get the MP3 bytes instead
row["truth"]["words"]   # [{"word": "84:1:1", "start_s": 5.78, "end_s": 6.25}, ...]
row["segments"]         # [{"start_s", "end_s", "first_word", "last_word"}, ...]
```

To run an aligner on the recordings and score it, use the benchmark package rather than reading the parquet yourself:

```bash
pip install "quran-alignment-benchmark[corpus]"
qab fetch --corpus v1 --out corpus/    # <id>.mp3 + <id>.json per recording
qab score submissions/ --corpus v1     # the same report the leaderboard shows
```

## Columns

| column | meaning |
|---|---|
| `id` | stable recording id; the `case_id` of a submission |
| `audio` | the recording (MP3, byte-for-byte the reviewed file) |
| `riwayah` | reading whose numbering the truth uses (`hafs_an_asim`) |
| `reciter`, `style`, `content`, `noisy`, `description` | declared: who, murattal / mujawwad / hadr / muallim, quran_only / prayer, degraded capture or not, free text |
| `passages`, `multi_surah`, `duration_s`, `recited_words`, `wpm`, `repeat_events` | derived from the truth |
| `truth` | `words[{word, start_s, end_s}]` and `non_quran[{start_s, end_s}]`; `word` is `S:A:W` (chapter:verse:word, Hafs numbering) or `Basmala:k` / `Isti'adha:k` |
| `segments` | the reviewed segmentation, edges from the first and last word times |

Times are seconds from the start of the audio. A repeated word appears once per recitation. Full column definitions, annotation method and known gaps: [docs/CORPUS.md](https://github.com/QUD-Technologies/quran-alignment-benchmark/blob/main/docs/CORPUS.md).

## Versioning

A config is immutable once a score has been published against it. A re-annotation or an added recording becomes the next config (`v2`). Scores always name the config they were computed on.

## License

CC BY 4.0 for the annotations (`truth`, `segments`) and descriptive columns, and for the benchmark repository. The audio recordings are not covered by this license.
## Corpus issues and new audio

[Report an audio or ground-truth problem](https://github.com/QUD-Technologies/quran-alignment-benchmark/issues/new?template=corpus-issue.yml) through a GitHub issue.

To [suggest new audio](https://github.com/QUD-Technologies/quran-alignment-benchmark/issues/new?template=new-audio.yml), provide only:

- An audio link or file.
- The reciter's name.
- Why it would be a useful addition to the corpus.

No other metadata or annotations are needed. If accepted, the recording will be ingested, annotated, and released in the next corpus version.

