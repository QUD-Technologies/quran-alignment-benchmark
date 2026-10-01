"""Corpus access: cases from the Hugging Face dataset, a local case directory, or a fetched copy.

`load_cases("v1")` reads config `v1`, split `test` of the dataset and turns each
row into a `Case`. `fetch` writes the same cases as JSON next to the audio
files so a system can be run on them offline; `qab score` then reads that
directory with `--cases`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .schema import Case, Submission, SubmissionMeta

DATASET = "hetchyy/quran-alignment-benchmark"
SPLIT = "test"
CASE_FIELDS = ("id", "riwayah", "reciter", "description", "style", "content", "noisy", "multi_surah")
_CORPUS_EXTRA = "loading from the Hub needs the corpus extra: pip install 'qab[corpus] @ git+https://github.com/QUD-Technologies/quran-alignment-benchmark'"


def case_from_row(row: dict[str, Any]) -> Case:
    """Build a `Case` from one dataset row (audio column ignored)."""
    truth = row["truth"]
    return Case(
        duration_s=row["duration_s"],
        segments=row.get("segments", []),
        words=[{"word": w["word"], "start_s": w["start_s"], "end_s": w["end_s"]} for w in truth["words"]],
        non_quran=[{"start_s": iv["start_s"], "end_s": iv["end_s"]} for iv in truth["non_quran"]],
        **{k: row[k] for k in CASE_FIELDS},
    )


def _unique(cases: list[Case]) -> list[Case]:
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate case ids: {dupes}")
    return cases


def load_cases(version: str = "v1", cases_dir: str | Path | None = None,
               dataset: str = DATASET, revision: str | None = None) -> list[Case]:
    """Cases for a corpus version: from `cases_dir/*.json` when given, else from the Hub.

    By default the selected corpus config is read from the current dataset.
    """
    if cases_dir is not None:
        files = sorted(Path(cases_dir).glob("*.json"))
        if not files:
            raise FileNotFoundError(f"no case files in {cases_dir}")
        return _unique([Case.model_validate_json(f.read_text(encoding="utf-8")) for f in files])
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise ImportError(_CORPUS_EXTRA) from exc
    ds = load_dataset(dataset, version, split=SPLIT, revision=revision)
    ds = ds.remove_columns([c for c in ("audio",) if c in ds.column_names])
    return _unique([case_from_row(row) for row in ds])


def fetch(out_dir: str | Path, version: str = "v1", dataset: str = DATASET,
          revision: str | None = None) -> list[Path]:
    """Write `<id>.mp3` and `<id>.json` (the case) for every recording into `out_dir`."""
    try:
        from datasets import Audio, load_dataset
    except ImportError as exc:
        raise ImportError(_CORPUS_EXTRA) from exc
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ds = load_dataset(dataset, version, split=SPLIT, revision=revision).cast_column("audio", Audio(decode=False))
    written: list[Path] = []
    for row in ds:
        case = case_from_row(row)
        audio = row["audio"]
        audio_path = out / f"{case.id}.mp3"
        audio_path.write_bytes(audio["bytes"] if audio.get("bytes") else Path(audio["path"]).read_bytes())
        case_path = out / f"{case.id}.json"
        case_path.write_text(case.model_dump_json(indent=1), encoding="utf-8")
        written += [audio_path, case_path]
    return written


def load_submissions(directory: str | Path) -> tuple[list[Submission], SubmissionMeta | None]:
    """All `*.json` submissions in a directory plus `submission.json` metadata when present."""
    d = Path(directory)
    if not d.is_dir():
        raise FileNotFoundError(f"not a directory: {d}")
    meta_path = d / "submission.json"
    meta = SubmissionMeta.model_validate_json(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    subs = [Submission.model_validate_json(f.read_text(encoding="utf-8"))
            for f in sorted(d.glob("*.json")) if f.name != "submission.json"]
    if not subs:
        raise FileNotFoundError(f"no submission files in {d}")
    return subs, meta