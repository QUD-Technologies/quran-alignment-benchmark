"""Build a corpus version from reviewed Inspector samples: parquet rows, audio, dataset card.

Maintainer tool. Reads every recording listed in tools/corpus_<version>.json
from the Inspector bucket, turns its reviewed `detailed.json` into a `qab.Case`
(the same validation the scorer applies), derives the descriptive columns from
the truth, and writes `<out>/<version>/test-00000-of-00001.parquet` with the
audio embedded, plus the dataset card. `--push` uploads the folder to the Hub.

    python tools/export_corpus.py --version v1 --out build/corpus [--push]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from qab import hafs, refs
from qab.schema import Case
from qab.scoring import _truth_repeat_anchors

ROOT = Path(__file__).resolve().parent
DATASET = "QUD-Technologies/quran-alignment-benchmark"
FORMULA_LOCATION_PREFIX = "0:0:"
MAX_EDGE_OVERLAP_S = 0.2


def read_bucket(fs, bucket: str, path: str) -> bytes:
    return fs.read_bytes(f"hf://buckets/{bucket}/{path}")


def trim_end(mp3: bytes, at_s: float) -> bytes:
    """Cut the tail off an MP3 without re-encoding: whole frames only, so the kept audio is the
    original bytes and the cut lands on the first frame boundary at or after `at_s`."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-t", str(at_s), "-c", "copy", "-f", "mp3", "-"],
                          input=mp3, capture_output=True, check=True).stdout


def clamp_to(words: list[dict], non_quran: list[dict], segments: list[dict], duration: float) -> None:
    """Pull truth intervals back inside a trimmed recording."""
    for interval in [*words, *non_quran, *segments]:
        if interval["start_s"] >= duration:
            raise SystemExit(f"trim drops an annotated interval starting at {interval['start_s']:.3f} s")
        interval["end_s"] = min(interval["end_s"], duration)


def duration_s(mp3: bytes) -> float:
    """Decoded length: count PCM samples rather than trust a header."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                          input=mp3, capture_output=True, check=True)
    return len(proc.stdout) / 2 / 16000


def truth_from_detailed(detailed: dict, pad_tail: bool = True) -> tuple[list[dict], list[dict], list[dict]]:
    """(words, non_quran, segments) from a reviewed Inspector document.

    Word times are padded to the reviewed segment (docs/CORPUS.md): first word to
    the segment start, each word's end to the next word's start, and, unless
    `pad_tail` is false, the last word to the segment end.
    """
    words, non_quran, segments = [], [], []
    reviewed = sorted((seg for entry in detailed["entries"] for seg in entry["segments"]),
                      key=lambda seg: seg["time_start"])
    for seg in reviewed:
        ref = seg.get("matched_ref") or ""
        uid = seg["segment_uid"]
        if seg["time_end"] <= seg["time_start"]:
            raise SystemExit(f"segment {uid} has no duration")
        if not ref:
            non_quran.append({"start_s": seg["time_start"] / 1000, "end_s": seg["time_end"] / 1000})
            continue
        timings = seg.get("word_timings") or []
        if not timings:
            raise SystemExit(f"segment {uid} ({ref}) has no word timings")
        seg_words = []
        for w in timings:
            location = w["location"]
            if location.startswith(FORMULA_LOCATION_PREFIX):
                label = f"{ref}:{location[len(FORMULA_LOCATION_PREFIX):]}"
            else:
                label = location
            seg_words.append({"word": label, "start_s": w["start_ms"] / 1000, "end_s": w["end_ms"] / 1000})
        got = [w["word"] for w in seg_words]
        if got != _expected_labels(ref):
            raise SystemExit(f"segment {uid} words {got[:3]}... do not spell {ref}")
        for a, b in zip(seg_words, seg_words[1:]):
            if b["start_s"] < a["start_s"] or a["end_s"] <= a["start_s"]:
                raise SystemExit(f"segment {uid}: word times out of order at {a['word']} / {b['word']}")
        pad_segment(seg_words, seg["time_start"] / 1000, seg["time_end"] / 1000, pad_tail)
        words.extend(seg_words)
        segments.append({"start_s": seg_words[0]["start_s"], "end_s": seg_words[-1]["end_s"],
                         "first_word": got[0], "last_word": got[-1]})
    trim_overlaps(words, segments)
    for seg in segments:
        if seg["end_s"] <= seg["start_s"]:
            raise SystemExit(f"segment {seg['first_word']}-{seg['last_word']} collapsed to zero length")
    return words, non_quran, segments


def pad_segment(seg_words: list[dict], start_s: float, end_s: float, pad_tail: bool) -> None:
    seg_words[0]["start_s"] = start_s
    for a, b in zip(seg_words, seg_words[1:]):
        a["end_s"] = b["start_s"]
    if pad_tail:
        seg_words[-1]["end_s"] = end_s
    elif seg_words[-1]["end_s"] > end_s:
        seg_words[-1]["end_s"] = end_s


def trim_overlaps(words: list[dict], segments: list[dict]) -> int:
    """Tile words across reviewed segment edges: when a segment's padded last word runs past the
    next segment's first word (the Inspector allows a small edge overlap), the earlier word ends
    where the next begins. Anything beyond MAX_EDGE_OVERLAP_S is a review fault, not padding."""
    trimmed = 0
    for a, b in zip(words, words[1:]):
        if b["start_s"] < a["end_s"] - 1e-9:
            if a["end_s"] - b["start_s"] > MAX_EDGE_OVERLAP_S:
                raise SystemExit(f"words {a['word']} / {b['word']} overlap by {a['end_s'] - b['start_s']:.3f} s")
            for seg in segments:
                if seg["end_s"] == a["end_s"] and seg["last_word"] == a["word"]:
                    seg["end_s"] = b["start_s"]
            a["end_s"] = b["start_s"]
            trimmed += 1
    return trimmed


def _expected_labels(ref: str) -> list[str]:
    if ref in refs.FORMULAS:
        return [f"{ref}:{k}" for k in range(1, refs.FORMULAS[ref] + 1)]
    return [refs.token_label(t) for t in refs.parse_span(ref).tokens()]


def passages(case: Case) -> tuple[str, bool]:
    """Chapter list with verse ranges for partial chapters; Fatiha dropped when anything else exists."""
    seen: dict[int, set[int]] = {}
    order: list[int] = []
    for w in case.words:
        t = w.token
        if t[0] != "q":
            continue
        chapter, verse, _ = hafs.location(t[1], t[2])
        if chapter not in seen:
            seen[chapter] = set()
            order.append(chapter)
        seen[chapter].add(verse)
    multi = len(order) > 1
    shown = [c for c in order if not (multi and c == 1)]
    if shown == list(range(78, 115)):
        return "Juz' Amma", multi
    if shown == list(range(67, 78)):
        return "Juz' Tabarak", multi
    parts = []
    for c in shown:
        lo, hi = min(seen[c]), max(seen[c])
        name = hafs.chapter_name(c)
        full = len(seen[c]) == hafs.verse_count(c)
        parts.append(name if full else f"{name} {lo}" if lo == hi else f"{name} {lo}-{hi}")
    return ", ".join(parts), multi


def build_row(fs, bucket: str, riwayah: str, rec: dict) -> dict:
    base = f"samples/{rec['sample']}"
    detailed = json.loads(read_bucket(fs, bucket, f"{base}/detailed.json"))
    mp3 = read_bucket(fs, bucket, f"{base}/audio/{rec['chapter']}.mp3")
    if rec.get("trim_end_s"):
        mp3 = trim_end(mp3, rec["trim_end_s"])
    dur = duration_s(mp3)
    words, non_quran, segments = truth_from_detailed(detailed, pad_tail=rec.get("pad_tail", True))
    if rec.get("trim_end_s"):
        clamp_to(words, non_quran, segments, round(dur, 3))
    quran_seconds = sum(s["end_s"] - s["start_s"] for s in segments if ":" in s["first_word"] and s["first_word"].split(":")[0] not in refs.FORMULAS)
    case = Case(id=rec["id"], duration_s=round(dur, 3), riwayah=riwayah, reciter=rec["reciter"], description=rec["description"],
                style=rec["style"], content=rec["content"], noisy=rec["noisy"], multi_surah=False,
                words=words, non_quran=non_quran)
    text, multi = passages(case)
    case = case.model_copy(update={"multi_surah": multi})
    tokens = [w.token for w in case.words]
    recited = sum(1 for t in tokens if t[0] == "q")
    repeats = len(_truth_repeat_anchors(tokens, [t[0] == "q" for t in tokens]))
    return {
        "id": case.id,
        "audio": {"bytes": mp3, "path": f"{case.id}.mp3"},
        "riwayah": case.riwayah,
        "description": case.description,
        "reciter": case.reciter,
        "style": case.style,
        "content": case.content,
        "noisy": case.noisy,
        "passages": text,
        "multi_surah": multi,
        "duration_s": round(dur, 3),
        "recited_words": recited,
        "wpm": round(recited / (quran_seconds / 60)) if quran_seconds else 0,
        "repeat_events": repeats,
        "truth": {"words": words, "non_quran": non_quran},
        "segments": segments,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--dataset", default=DATASET)
    args = ap.parse_args()
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datasets import Audio, Features, Value
    from huggingface_hub import HfFileSystem

    manifest = json.loads((ROOT / f"corpus_{args.version}.json").read_text(encoding="utf-8"))
    fs = HfFileSystem()
    rows = []
    for rec in manifest["recordings"]:
        row = build_row(fs, manifest["bucket"], manifest["riwayah"], rec)
        rows.append(row)
        print(f"{row['id']:<22} {row['duration_s'] / 60:6.1f} min {row['recited_words']:6d} words {row['wpm']:4d} wpm "
              f"{row['repeat_events']:3d} repeats  {row['passages']}", flush=True)
    features = Features({
        "id": Value("string"), "audio": Audio(), "riwayah": Value("string"), "description": Value("string"), "reciter": Value("string"),
        "style": Value("string"), "content": Value("string"), "noisy": Value("bool"), "passages": Value("string"),
        "multi_surah": Value("bool"), "duration_s": Value("float64"),
        "recited_words": Value("int32"), "wpm": Value("int32"), "repeat_events": Value("int32"),
        "truth": {"words": [{"word": Value("string"), "start_s": Value("float64"), "end_s": Value("float64")}],
                  "non_quran": [{"start_s": Value("float64"), "end_s": Value("float64")}]},
        "segments": [{"start_s": Value("float64"), "end_s": Value("float64"),
                      "first_word": Value("string"), "last_word": Value("string")}],
    })
    out = args.out / args.version
    out.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=features.arrow_schema)
    # One row group per recording, with a page index: the Hub's dataset viewer scans a whole
    # row group to reach a row and refuses anything over 300 MB, which a single group of
    # embedded-audio rows blows past.
    pq.write_table(table, out / "test-00000-of-00001.parquet", compression="zstd",
                   row_group_size=1, write_page_index=True)
    versions = sorted(p.name for p in args.out.iterdir() if p.is_dir() and (p / "test-00000-of-00001.parquet").exists())
    configs = "configs:\n" + "".join(f"  - config_name: {v}\n    data_files:\n      - split: test\n        path: {v}/test-*\n" for v in versions)
    card = (ROOT / "dataset_card.md").read_text(encoding="utf-8").replace("{{CONFIGS}}\n", configs)
    (args.out / "README.md").write_text(card, encoding="utf-8")
    total_min = sum(r["duration_s"] for r in rows) / 60
    print(f"{len(rows)} rows, {total_min:.1f} min, {sum(r['recited_words'] for r in rows)} words -> {out}")
    if args.push:
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(args.dataset, repo_type="dataset", exist_ok=True)
        api.upload_folder(repo_id=args.dataset, repo_type="dataset", folder_path=str(args.out),
                          commit_message=f"corpus {args.version}")
        print("pushed", args.dataset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
