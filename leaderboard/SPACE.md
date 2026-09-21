---
title: Quran Alignment Benchmark
emoji: 📐
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
hf_oauth: true
license: cc-by-4.0
short_description: Compare Quran recitation alignment systems
---

# Quran Alignment Benchmark

Compare systems, explore the dataset, understand the metrics, and submit predictions using the four tabs in the app. Alignment confidence follows a consumer contract: green segments are intended to be safe to use, amber segments should be reviewed, and red segments are probably incorrect.

[Dataset](https://huggingface.co/datasets/hetchyy/quran-alignment-benchmark) · [Scorer and local scoring](https://github.com/Hetchy/quran-alignment-benchmark)

Prediction files and contact information are stored privately. Public results contain scores and the system details supplied by the submitter.

## Corpus issues and new audio

[Report an audio or ground-truth problem](https://github.com/Hetchy/quran-alignment-benchmark/issues/new?template=corpus-issue.yml) through a GitHub issue.

To [suggest new audio](https://github.com/Hetchy/quran-alignment-benchmark/issues/new?template=new-audio.yml), provide only:

- An audio link or file.
- The reciter's name.
- Why it would be a useful addition to the corpus.

No other metadata or annotations are needed. If accepted, the recording will be ingested, annotated, and released in the next corpus version.
