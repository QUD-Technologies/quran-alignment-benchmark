"""Run the live RAS dev Space against all three QAB v1 tasks.

The runner is deliberately resumable. Raw API responses are written before any
adapter or scorer runs, and completed case files are reused on restart.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import os
import shutil
import sys
import threading
import time
import zipfile
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qab.corpus import case_from_row  # noqa: E402
from qab.report import evaluate  # noqa: E402
from qab.schema import Submission, SubmissionMeta  # noqa: E402
from qab.tasks import (  # noqa: E402
    ClipPrediction,
    SegmentationSubmission,
    TimingSubmission,
    evaluate_task,
    reviewed_clips,
)

SPACE = "https://hetchyy-quranic-universal-aligner-dev.hf.space/api/v1"
DATASET_ID = "qud-technologies/quran-alignment-benchmark"
DATASET_SHA = "4c6c520ee85cd6363824e1e7a4dd40ed139020dd"
MODELS = {"Base": "hetchyy/tibyan-base-v1"}
TIMEOUT_S = 4 * 60 * 60
MAX_ATTEMPTS = 8
PRINT_LOCK = threading.Lock()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def load_rows(parquet_path: Path) -> list[dict[str, Any]]:
    return pq.read_table(parquet_path).to_pylist()


def prepare(rows: list[dict[str, Any]], run_dir: Path) -> list[Any]:
    audio_dir = run_dir / "audio"
    case_dir = run_dir / "cases"
    audio_dir.mkdir(parents=True, exist_ok=True)
    case_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for row in rows:
        case = case_from_row(row)
        cases.append(case)
        audio_path = audio_dir / f"{case.id}.mp3"
        if not audio_path.exists() or audio_path.stat().st_size != len(row["audio"]["bytes"]):
            audio_path.write_bytes(row["audio"]["bytes"])
        case_path = case_dir / f"{case.id}.json"
        if not case_path.exists():
            case_path.write_text(case.model_dump_json(indent=1), encoding="utf-8")
    return cases


def headers(token: str | None) -> dict[str, str]:
    result = {"User-Agent": "qab-ras-v1-eval/1"}
    if token:
        result["Authorization"] = f"Bearer {token}"
    return result


def request_json(method: str, url: str, token: str | None, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, url, headers=headers(token), timeout=TIMEOUT_S, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1000]}")
    return response.json()


def create_batch(model: str, device: str, token: str | None) -> str:
    body = {
        "model_name": model,
        "device": device,
        "riwayah": "hafs",
        "pad_left_ms": 100,
        "pad_right_ms": 200,
        "include_word_timestamps": False,
        "discard_session": True,
    }
    return request_json("POST", f"{SPACE}/batches", token, json=body)["batch_id"]


def parse_sse(response: requests.Response) -> dict[str, Any]:
    event = None
    for raw in response.iter_lines(decode_unicode=True):
        line = raw or ""
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if event == "result":
                return payload
            if event == "error":
                status = payload.get("status", 500)
                retry = (payload.get("detail") or {}).get("retry_after_s")
                raise RuntimeError(f"SSE {status} retry={retry}: {json.dumps(payload)[:1000]}")
    raise RuntimeError("SSE stream ended without a result")


def run_alignment_case(
    case: Any,
    model: str,
    batch_id: str,
    run_dir: Path,
    token: str | None,
    device: str = "CPU",
) -> dict[str, Any]:
    out = run_dir / "raw" / "alignment" / model.lower() / f"{case.id}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    audio = run_dir / "audio" / f"{case.id}.mp3"
    url = f"{SPACE}/batches/{batch_id}/items/{case.id}/audio/stream"
    error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        try:
            with audio.open("rb") as source:
                response = requests.post(
                    url,
                    headers=headers(token),
                    files={"audio": (audio.name, source, "audio/mpeg")},
                    stream=True,
                    timeout=TIMEOUT_S,
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1000]}")
                body = parse_sse(response)
            actual_device = body.get("device")
            if actual_device != device:
                raise RuntimeError(
                    f"requested {device} but Space returned {actual_device or 'no device'}"
                )
            record = {
                "case_id": case.id,
                "model": model,
                "requested_device": device,
                "elapsed_s": time.perf_counter() - started,
                "response": body,
            }
            atomic_json(out, record)
            log(f"alignment {model:<5} {case.id:<24} {record['elapsed_s']:8.1f}s ok")
            return record
        except Exception as exc:  # network/service retries are part of the harness
            error = exc
            log(f"alignment {model:<5} {case.id:<24} attempt {attempt} failed: {exc}")
            time.sleep(min(120, 5 * attempt))
    raise RuntimeError(f"alignment {model} {case.id} failed after retries: {error}")


def timing_input(case: Any) -> list[dict[str, Any]]:
    result = []
    for number, (segment, words) in enumerate(reviewed_clips(case), 1):
        first, last = words[0].word, words[-1].word
        entry: dict[str, Any] = {
            "segment": number,
            "time_from": segment.start_s,
            "time_to": segment.end_s,
        }
        if first.startswith("Basmala:"):
            entry["special_type"] = "Basmala"
        elif first.startswith("Isti'adha:"):
            entry["special_type"] = "Isti'adha"
        else:
            entry["ref_from"] = first
            entry["ref_to"] = last
        result.append(entry)
    return result


def run_timing_case(case: Any, run_dir: Path, token: str | None) -> dict[str, Any]:
    out = run_dir / "raw" / "timing" / f"{case.id}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    audio = run_dir / "audio" / f"{case.id}.mp3"
    error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        try:
            with audio.open("rb") as source:
                response = requests.post(
                    f"{SPACE}/timestamps",
                    headers=headers(token),
                    files={"audio": (audio.name, source, "audio/mpeg")},
                    data={
                        "segments": json.dumps(timing_input(case)),
                        "granularity": "words",
                        "riwayah": "hafs",
                    },
                    timeout=TIMEOUT_S,
                )
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1000]}")
            record = {
                "case_id": case.id,
                "elapsed_s": time.perf_counter() - started,
                "response": response.json(),
            }
            atomic_json(out, record)
            log(f"timing       {case.id:<24} {record['elapsed_s']:8.1f}s ok")
            return record
        except Exception as exc:
            error = exc
            log(f"timing       {case.id:<24} attempt {attempt} failed: {exc}")
            time.sleep(min(120, 5 * attempt))
    raise RuntimeError(f"timing {case.id} failed after retries: {error}")


def canonical_special(value: str | None) -> str | None:
    normalized = (value or "").lower().replace("’", "'")
    if "isti" in normalized and "basmala" in normalized:
        return None
    if "basmala" in normalized:
        return "Basmala"
    if "isti" in normalized:
        return "Isti'adha"
    return None


def span(start: str | None, end: str | None) -> str | None:
    if not start or not end:
        return None
    # QAB's claim grammar intentionally distinguishes a class token from a
    # Quran span. Even a one-word Quran claim is expressed as X-X.
    return f"{start}-{end}"


def processing_seconds(record: dict[str, Any]) -> float:
    """Queue-free server runtime stamped by the deployed Space."""
    runtime = ((record.get("response") or {}).get("_meta") or {}).get("runtime") or {}
    processing = runtime.get("processing_seconds")
    queue = runtime.get("queue_seconds")
    total = runtime.get("total_seconds")
    if not all(isinstance(value, (int, float)) for value in (processing, queue, total)):
        raise ValueError(f"{record.get('case_id')}: response has no server runtime metadata")
    if abs(float(processing) + float(queue) - float(total)) > 0.05:
        raise ValueError(f"{record.get('case_id')}: inconsistent server runtime metadata")
    return float(processing)


def alignment_submission(case: Any, record: dict[str, Any], diagnostics: Counter) -> Submission:
    adapted = []
    for row in record["response"].get("segments", []):
        reference = None
        if row.get("kind") == "special" or row.get("special_type"):
            reference = canonical_special(row.get("special_type") or row.get("ref_from"))
            if reference is None:
                diagnostics["unsupported_special_segments"] += 1
        else:
            reference = span(row.get("ref_from"), row.get("ref_to"))
            if row.get("wrap_word_ranges"):
                diagnostics["wraparound_segments_primary_span"] += 1
            if row.get("repeated_ranges"):
                diagnostics["repeated_range_segments_primary_span"] += 1
        adapted.append({
            "start_s": row["time_from"],
            "end_s": row["time_to"],
            "reference": reference,
            "confidence": row.get("confidence") if reference else None,
        })
    return Submission(
        case_id=case.id,
        runtime_seconds=processing_seconds(record),
        segments=adapted,
    )


def segmentation_submission(
    case: Any,
    record: dict[str, Any],
    diagnostics: Counter,
) -> SegmentationSubmission:
    intervals = [
        {"start_s": row["time_from"], "end_s": row["time_to"]}
        for row in record["response"].get("segments", [])
    ]
    intervals.sort(key=lambda value: (value["start_s"], value["end_s"]))
    for left, right in zip(intervals, intervals[1:]):
        if right["start_s"] < left["end_s"]:
            midpoint = (right["start_s"] + left["end_s"]) / 2
            left["end_s"] = midpoint
            right["start_s"] = midpoint
            diagnostics["overlaps_split_at_midpoint"] += 1
    intervals = [value for value in intervals if value["end_s"] > value["start_s"]]
    return SegmentationSubmission(
        case_id=case.id,
        runtime_seconds=processing_seconds(record),
        segments=intervals,
    )


def normalized_location(location: str, expected: list[str]) -> str:
    if not location.startswith("0:0:"):
        return location
    ordinal = location.rsplit(":", 1)[-1]
    prefix = "Isti'adha" if expected and expected[0].startswith("Isti'adha:") else "Basmala"
    return f"{prefix}:{ordinal}"


def timing_submission(case: Any, record: dict[str, Any], diagnostics: Counter) -> TimingSubmission:
    by_segment = {row.get("segment"): row for row in record["response"].get("segments", [])}
    clips = []
    for number, (segment, truth_words) in enumerate(reviewed_clips(case), 1):
        expected = [word.word for word in truth_words]
        duration = segment.end_s - segment.start_s
        returned = by_segment.get(number, {}).get("words")
        if returned is None:
            diagnostics["untimed_clips"] += 1
            clips.append(ClipPrediction(words=None))
            continue
        converted = []
        for item in returned:
            start_s = max(0.0, min(duration, item[1]))
            end_s = max(0.0, min(duration, item[2]))
            if start_s != item[1] or end_s != item[2]:
                diagnostics["rounded_bounds_clamped"] += 1
            converted.append([normalized_location(item[0], expected), start_s, end_s])
        if len(converted) == len(expected):
            words = [
                {"start_s": item[1], "end_s": item[2]} if item[2] > item[1] else None
                for item in converted
            ]
        else:
            diagnostics["partial_word_lists"] += 1
            positions: dict[str, deque[int]] = defaultdict(deque)
            for index, token in enumerate(expected):
                positions[token].append(index)
            words = [None] * len(expected)
            cursor = 0
            for token, start_s, end_s in converted:
                while positions[token] and positions[token][0] < cursor:
                    positions[token].popleft()
                if positions[token]:
                    index = positions[token].popleft()
                    if end_s > start_s:
                        words[index] = {"start_s": start_s, "end_s": end_s}
                    cursor = index + 1
        clips.append(ClipPrediction(words=words))
    return TimingSubmission(case_id=case.id, clips=clips)


def score_all(
    cases: list[Any],
    records: list[dict[str, Any]],
    run_dir: Path,
    device: str,
) -> dict[str, Any]:
    align_by_model = {
        model: {record["case_id"]: record for record in records if record.get("model") == model}
        for model in MODELS
    }
    timing_records = {
        record["case_id"]: record for record in records if record.get("model") is None
    }
    reports: dict[str, Any] = {"alignment": {}, "segmentation": {}}
    adapter_diagnostics: dict[str, Any] = {}
    for model in MODELS:
        alignment_diag: Counter = Counter()
        segmentation_diag: Counter = Counter()
        alignment = [alignment_submission(c, align_by_model[model][c.id], alignment_diag) for c in cases]
        segmentation = [segmentation_submission(c, align_by_model[model][c.id], segmentation_diag) for c in cases]
        meta = SubmissionMeta(
            system=f"RAS dev {model}",
            version=f"{MODELS[model]} via dev Space",
            hardware_class=device.lower(),
            hardware=(
                "Hugging Face Space CPU; exact host CPU is not exposed by the API"
                if device == "CPU" else "Hugging Face ZeroGPU A10G"
            ),
        )
        reports["alignment"][model] = evaluate(cases, alignment, meta, corpus_version="v1")
        reports["segmentation"][model] = evaluate_task(
            "segmentation", cases, segmentation, meta, corpus_version="v1"
        )
        adapter_diagnostics[model] = {
            "alignment": dict(alignment_diag),
            "segmentation": dict(segmentation_diag),
        }
    timing_diag: Counter = Counter()
    timing = [timing_submission(c, timing_records[c.id], timing_diag) for c in cases]
    reports["timing"] = evaluate_task("timing", cases, timing, corpus_version="v1")
    adapter_diagnostics["timing"] = dict(timing_diag)
    evidence = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "space": SPACE,
        "dataset": {"id": DATASET_ID, "revision": DATASET_SHA, "config": "v1", "cases": len(cases)},
        "models": MODELS,
        "requested_device": device,
        "reports": reports,
        "adapter_diagnostics": adapter_diagnostics,
        "performance": performance(records, cases),
    }
    atomic_json(run_dir / "results.json", evidence)
    return evidence


def performance(records: list[dict[str, Any]], cases: list[Any]) -> dict[str, Any]:
    total_audio = sum(case.duration_s for case in cases)
    result: dict[str, Any] = {
        "audio_seconds": total_audio,
        # Every request was launched in one client pool. The slowest request is
        # therefore the reproducible approximation of batch wall time, unlike
        # a later resume run that reads the cached response files in seconds.
        "concurrent_wall_seconds": max(record["elapsed_s"] for record in records),
    }
    for model in MODELS:
        selected = [r for r in records if r.get("model") == model]
        elapsed = sum(processing_seconds(r) for r in selected)
        result[f"alignment_{model.lower()}"] = {
            "sum_processing_seconds": elapsed,
            "rtf": elapsed / total_audio,
            "median_processing_seconds": sorted(processing_seconds(r) for r in selected)[len(selected) // 2],
            "max_processing_seconds": max(processing_seconds(r) for r in selected),
            "sum_queue_seconds": sum(r["response"]["_meta"]["runtime"]["queue_seconds"] for r in selected),
            "sum_client_seconds": sum(r["elapsed_s"] for r in selected),
        }
    selected = [r for r in records if r.get("model") is None]
    elapsed = sum(r["elapsed_s"] for r in selected)
    result["timing"] = {
        "sum_request_seconds": elapsed,
        "rtf": elapsed / total_audio,
        "median_request_seconds": sorted(r["elapsed_s"] for r in selected)[len(selected) // 2],
        "max_request_seconds": max(r["elapsed_s"] for r in selected),
    }
    return result


def write_submission_zip(
    cases: list[Any], records: list[dict[str, Any]], run_dir: Path, device: str,
) -> Path:
    """Write one three-task ZIP accepted by the leaderboard upload adapter."""
    by_case = {record["case_id"]: record for record in records if record.get("model") == "Base"}
    timing_by_case = {record["case_id"]: record for record in records if record.get("model") is None}
    diagnostics = Counter()
    archive = run_dir / f"tibyan-base-v1-{device.lower()}-qab-v1.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for case in cases:
            alignment = alignment_submission(case, by_case[case.id], diagnostics)
            segmentation = segmentation_submission(case, by_case[case.id], diagnostics)
            timing = timing_submission(case, timing_by_case[case.id], diagnostics)
            output.writestr(
                f"alignment/{case.id}.json",
                alignment.model_dump_json(indent=1, exclude_none=True),
            )
            output.writestr(
                f"segmentation/{case.id}.json",
                segmentation.model_dump_json(indent=1, exclude_none=True),
            )
            output.writestr(
                f"timing/{case.id}.json",
                timing.model_dump_json(indent=1, exclude_none=True),
            )
    return archive


CSS = """
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1d212b;--ink:#e7ebf0;--muted:#9aa4b2;--line:#2a2f3a;--accent:#7c9cff;--accent2:#4fd1a1;--warn:#ffb454;--code-bg:#0b0d12}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}.wrap{max-width:1060px;margin:0 auto;padding:0 24px 120px}header.hero{padding:64px 0 40px;border-bottom:1px solid var(--line);margin-bottom:8px}.kicker{color:var(--accent);font-weight:600;letter-spacing:.14em;text-transform:uppercase;font-size:12px}h1{font-size:40px;line-height:1.1;margin:14px 0 12px;font-weight:800;letter-spacing:-.02em}.sub{color:var(--muted);font-size:18px;max-width:70ch}.meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:22px}.tag{background:var(--panel2);border:1px solid var(--line);color:var(--muted);padding:5px 11px;border-radius:999px;font-size:12.5px}.tag b{color:var(--ink);font-weight:600}nav.toc{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin:34px 0;columns:2;column-gap:32px}nav.toc a{display:block;color:var(--muted);text-decoration:none;padding:4px 0;font-size:14.5px;break-inside:avoid}nav.toc a:hover{color:var(--accent)}nav.toc .n{color:var(--line);display:inline-block;width:22px}h2{font-size:26px;margin:56px 0 6px;letter-spacing:-.01em;scroll-margin-top:20px}h2 .hn{color:var(--accent);font-weight:700;margin-right:10px;font-size:20px}h3{font-size:18px;margin:30px 0 6px}.lead{color:var(--muted);margin:6px 0 18px;max-width:74ch}p{max-width:74ch}hr{border:0;border-top:1px solid var(--line);margin:48px 0}code{font-family:ui-monospace,Consolas,monospace;font-size:.86em;background:var(--panel2);border:1px solid var(--line);padding:1px 6px;border-radius:6px;color:#cdd6e4}table{width:100%;border-collapse:collapse;margin:18px 0;font-size:14px;display:block;overflow-x:auto}th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}tbody tr:hover{background:var(--panel)}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:20px 0}.callout{border-left:3px solid var(--accent);background:var(--panel);border-radius:0 12px 12px 0;padding:14px 20px;margin:20px 0;color:var(--muted)}.callout.warn{border-left-color:var(--warn)}.callout b{color:var(--ink)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.metric{font-size:30px;font-weight:750;color:var(--accent2);font-variant-numeric:tabular-nums}.muted{color:var(--muted)}.footnote{color:var(--muted);font-size:13px}ul.clean{list-style:none;padding-left:0}ul.clean li{padding:6px 0 6px 26px;position:relative;color:var(--muted);max-width:74ch}ul.clean li::before{content:"->";position:absolute;left:0;color:var(--accent)}ul.clean li b{color:var(--ink)}@media(max-width:720px){.grid2{grid-template-columns:1fr}nav.toc{columns:1}h1{font-size:32px}}
"""


def pct(value: Any) -> str:
    return "&mdash;" if value is None else f"{100 * value:.2f}%"


def num(value: Any, digits: int = 3) -> str:
    return "&mdash;" if value is None else f"{value:.{digits}f}"


def render_report(evidence: dict[str, Any], path: Path) -> None:
    reports = evidence["reports"]
    device = evidence["requested_device"]
    alignment = reports["alignment"]["Base"]
    segmentation = reports["segmentation"]["Base"]
    timing = reports["timing"]
    perf = evidence["performance"]["alignment_base"]
    generated = evidence["generated_at"][:10]
    diagnostics = html.escape(json.dumps(evidence["adapter_diagnostics"], indent=2))
    document = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tibyan Base {device} benchmark</title><style>{CSS}</style></head><body><div class="wrap">
<header class="hero"><div class="kicker">Benchmark Report &middot; QAB v1</div><h1>Tibyan Base on {device}</h1>
<p class="sub">All 16 recordings, all three leaderboard tasks, with queue-free server runtime and align-v1 adapters.</p>
<div class="meta"><span class="tag"><b>Status:</b> Complete</span><span class="tag"><b>Date:</b> {generated}</span><span class="tag"><b>Audio:</b> {evidence['performance']['audio_seconds']/60:.1f} min</span><span class="tag"><b>Device:</b> {device}</span></div></header>
<nav class="toc"><a href="#scores"><span class="n">1</span>Scores</a><a href="#timing"><span class="n">2</span>Word timing</a><a href="#performance"><span class="n">3</span>Performance</a><a href="#method"><span class="n">4</span>Method</a></nav>
<h2 id="scores"><span class="hn">1</span>Alignment and segmentation</h2><p class="lead">Pooled scores over the immutable v1 corpus.</p>
<table><thead><tr><th>Words F1</th><th>Words found</th><th>Words correct</th><th>Trusted coverage</th><th>Unsafe green</th><th>Clean segments</th><th>Repeats F1</th></tr></thead><tbody><tr><td>{pct(alignment['headline']['words_f1'])}</td><td>{pct(alignment['pooled']['words_found'])}</td><td>{pct(alignment['pooled']['words_correct'])}</td><td>{pct(alignment['headline']['trusted_coverage'])}</td><td>{pct(alignment['headline']['unsafe_green'])}</td><td>{pct(alignment['headline']['clean_segments'])}</td><td>{pct(alignment['headline']['repeats_f1'])}</td></tr></tbody></table>
<table><thead><tr><th>Segments F1</th><th>Segments found</th><th>Segments correct</th><th>Boundaries F1</th></tr></thead><tbody><tr><td>{pct(segmentation['headline']['segments_f1'])}</td><td>{pct(segmentation['headline']['segments_found'])}</td><td>{pct(segmentation['headline']['segments_correct'])}</td><td>{pct(segmentation['headline']['boundaries_f1'])}</td></tr></tbody></table>
<h2 id="timing"><span class="hn">2</span>Word timing</h2><p class="lead">Reviewed clip boundaries and references are supplied to the shared timing service.</p>
<div class="grid2"><div class="card"><div class="muted">Words within 300 ms</div><div class="metric">{pct(timing['headline']['words_timed'])}</div></div><div class="card"><div class="muted">Clean clips at 300 ms</div><div class="metric">{pct(timing['headline']['clean_clips'])}</div></div></div>
<table><thead><tr><th>Tolerance</th><th>Words timed</th><th>Clean clips</th></tr></thead><tbody>{''.join(f'<tr><td>{float(t)*1000:.0f} ms</td><td>{pct(v["words_timed"])}</td><td>{pct(v["clean_clips"])}</td></tr>' for t,v in timing['diagnostics']['tolerances'].items())}</tbody></table>
<h2 id="performance"><span class="hn">3</span>Performance</h2><p class="lead">RTF uses the Space's processing time after subtracting its measured queue wait.</p>
<table><thead><tr><th>Queue-free RTF</th><th>Processing sum</th><th>Queue sum</th><th>Client sum</th><th>Median processing</th><th>Longest processing</th></tr></thead><tbody><tr><td>{num(perf['rtf'])}</td><td>{perf['sum_processing_seconds']/60:.1f} min</td><td>{perf['sum_queue_seconds']/60:.1f} min</td><td>{perf['sum_client_seconds']/60:.1f} min</td><td>{perf['median_processing_seconds']:.1f} s</td><td>{perf['max_processing_seconds']:.1f} s</td></tr></tbody></table>
<h2 id="method"><span class="hn">4</span>Method and adapters</h2><p class="lead">The transformations used to fit the API result to each leaderboard schema.</p>
<ul class="clean"><li><b>Recognition.</b> Sole model <code>hetchyy/tibyan-base-v1</code>; the checkpoint tokenizer and all Quran references, n-grams, specials and matching costs use align-v1.</li><li><b>Alignment.</b> The explicit primary <code>ref_from</code>/<code>ref_to</code> becomes one QAB span. Internal wrap ranges remain unclaimed because the schema has no internal timestamps.</li><li><b>Segmentation.</b> Returned speech intervals are sorted; any overlap is split at its midpoint to satisfy the non-overlap contract.</li><li><b>Timing.</b> Reviewed clips and references are sent to <code>/timestamps</code>. Partial lists are occurrence-mapped in order; missing words remain null.</li><li><b>Runtime.</b> <code>_meta.runtime.processing_seconds</code> is used for alignment and segmentation. Client upload, response transfer and Space queue time are excluded.</li></ul>
<div class="callout"><b>Adapter diagnostics.</b><pre>{diagnostics}</pre></div>
<hr><p class="footnote">Generated from retained raw API responses and the benchmark repository's own scorers. Machine-readable evidence and the submission ZIP are adjacent.</p>
</div></body></html>"""
    path.write_text(document, encoding="utf-8")

def main() -> int:
    global SPACE
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / ".local" / "ras-benchmark" / "run")
    parser.add_argument("--workers", type=int, default=48)
    parser.add_argument("--device", choices=("CPU", "GPU"), default="CPU")
    parser.add_argument("--space", default=SPACE)
    parser.add_argument("--models", nargs="+", choices=tuple(MODELS), default=list(MODELS))
    parser.add_argument("--cases", nargs="+", help="run only these case IDs")
    parser.add_argument(
        "--reuse-timing-from",
        type=Path,
        help="reuse raw timing responses from another completed run directory",
    )
    args = parser.parse_args()
    SPACE = args.space.rstrip("/")
    args.out.mkdir(parents=True, exist_ok=True)
    if args.reuse_timing_from is not None:
        source = args.reuse_timing_from / "raw" / "timing"
        if not source.is_dir():
            raise FileNotFoundError(f"timing source is missing: {source}")
        shutil.copytree(source, args.out / "raw" / "timing", dirs_exist_ok=True)
    rows = load_rows(args.parquet)
    cases = prepare(rows, args.out)
    selected_cases = cases
    if args.cases:
        wanted = set(args.cases)
        selected_cases = [case for case in cases if case.id in wanted]
        missing_cases = wanted - {case.id for case in selected_cases}
        if missing_cases:
            raise ValueError(f"unknown case IDs: {sorted(missing_cases)}")
    token = os.getenv("HF_TOKEN")
    health = request_json("GET", f"{SPACE}/health", token)
    if health.get("status") != "ok":
        raise RuntimeError(f"Space health check failed: {health}")
    batch_ids = {model: create_batch(model, args.device, token) for model in args.models}
    jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for model in args.models:
            for case in selected_cases:
                jobs.append(
                    pool.submit(
                        run_alignment_case,
                        case,
                        model,
                        batch_ids[model],
                        args.out,
                        token,
                        args.device,
                    )
                )
        for case in selected_cases:
            if not (args.out / "raw" / "timing" / f"{case.id}.json").exists():
                jobs.append(pool.submit(run_timing_case, case, args.out, token))
        for job in concurrent.futures.as_completed(jobs):
            job.result()

    records = []
    missing = []
    for model in MODELS:
        for case in cases:
            path = args.out / "raw" / "alignment" / model.lower() / f"{case.id}.json"
            if path.exists():
                records.append(json.loads(path.read_text(encoding="utf-8")))
            else:
                missing.append(f"{model}:{case.id}")
    for case in cases:
        path = args.out / "raw" / "timing" / f"{case.id}.json"
        if path.exists():
            records.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            missing.append(f"timing:{case.id}")
    if missing:
        log(f"incomplete: {len(missing)} responses remain")
        for item in missing:
            log(f"  {item}")
        return 0
    evidence = score_all(cases, records, args.out, args.device)
    atomic_json(args.out / "results.json", evidence)
    render_report(evidence, args.out / "report.html")
    archive = write_submission_zip(cases, records, args.out, args.device)
    log(f"complete in {evidence['performance']['concurrent_wall_seconds'] / 60:.1f} min")
    log(str(args.out / "report.html"))
    log(str(archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
