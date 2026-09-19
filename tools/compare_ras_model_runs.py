"""Compare retained legacy and Tibyan RAS responses on matched QAB cohorts."""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from qab.report import evaluate  # noqa: E402
from qab.schema import SubmissionMeta  # noqa: E402
from qab.tasks import evaluate_task  # noqa: E402
from run_ras_benchmark import (  # noqa: E402
    alignment_submission,
    load_rows,
    prepare,
    segmentation_submission,
)


@dataclass(frozen=True)
class System:
    key: str
    label: str
    model: str
    run_dir: Path
    repo: str

    def record(self, case_id: str) -> dict | None:
        path = self.run_dir / "raw" / "alignment" / self.model.lower() / f"{case_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def score(system: System, cases: list) -> dict:
    alignment_diag: Counter = Counter()
    segmentation_diag: Counter = Counter()
    records = [system.record(case.id) for case in cases]
    if any(record is None for record in records):
        raise ValueError(f"{system.label} is incomplete for the requested cohort")
    meta = SubmissionMeta(
        system=system.label,
        version=system.repo,
        hardware_class="cpu",
        hardware="Hugging Face Space CPU",
    )
    alignment = [
        alignment_submission(case, record, alignment_diag)
        for case, record in zip(cases, records, strict=True)
    ]
    segmentation = [
        segmentation_submission(case, record, segmentation_diag)
        for case, record in zip(cases, records, strict=True)
    ]
    return {
        "alignment": evaluate(cases, alignment, meta, corpus_version="v1"),
        "segmentation": evaluate_task(
            "segmentation", cases, segmentation, meta, corpus_version="v1"
        ),
    }


def metrics(report: dict) -> dict:
    alignment = report["alignment"]
    segmentation = report["segmentation"]
    return {
        "words_f1": alignment["headline"]["words_f1"],
        "words_found": alignment["pooled"]["words_found"],
        "words_correct": alignment["pooled"]["words_correct"],
        "clean_segments": alignment["headline"]["clean_segments"],
        "repeats_f1": alignment["headline"]["repeats_f1"],
        "segments_f1": segmentation["headline"]["segments_f1"],
        "boundaries_f1": segmentation["headline"]["boundaries_f1"],
    }


def pct(value: float) -> str:
    return f"{100 * value:.2f}%"


def render(evidence: dict, path: Path) -> None:
    cohort_rows = []
    for cohort, values in evidence["cohorts"].items():
        for system, result in values["systems"].items():
            m = result["metrics"]
            cohort_rows.append(
                f"<tr><td>{html.escape(cohort)}</td><td>{values['cases']}</td>"
                f"<td>{html.escape(system)}</td><td>{pct(m['words_f1'])}</td>"
                f"<td>{pct(m['clean_segments'])}</td><td>{pct(m['repeats_f1'])}</td>"
                f"<td>{pct(m['segments_f1'])}</td><td>{pct(m['boundaries_f1'])}</td></tr>"
            )
    noisy_rows = []
    for case_id, systems in evidence["noisy_cases"].items():
        for system, values in systems.items():
            noisy_rows.append(
                f"<tr><td>{html.escape(case_id)}</td><td>{html.escape(system)}</td>"
                f"<td>{pct(values['words_f1'])}</td><td>{pct(values['segments_f1'])}</td>"
                f"<td>{pct(values['boundaries_f1'])}</td></tr>"
            )
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Tibyan align-v1 comparison</title>
<style>:root{{--bg:#0f1115;--panel:#171a21;--ink:#e7ebf0;--muted:#9aa4b2;--line:#2a2f3a;--accent:#7c9cff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:52px 24px 100px}}h1{{font-size:38px;margin:0 0 10px}}h2{{margin-top:44px}}p{{color:var(--muted);max-width:78ch}}.callout{{background:var(--panel);border-left:3px solid var(--accent);padding:14px 18px;margin:22px 0}}table{{width:100%;border-collapse:collapse;display:block;overflow:auto}}th,td{{padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:left}}th{{color:var(--muted);font-size:12px;text-transform:uppercase}}</style></head>
<body><main><h1>Tibyan align-v1 vs R15/R7</h1><p>Direct QAB v1 comparison from retained responses produced by the same dev Space API and the repository scorer.</p>
<div class="callout"><b>Coverage limit:</b> Tibyan Base completed all 16 cases. Tibyan Large completed 12; its isolated Fatir CPU call took 49.4 minutes, so the 24–80 minute remaining recordings cannot be evaluated reliably inside the API's one-hour request lifetime. Large comparisons therefore use the identical 12-case paired cohort. All five noisy cases are complete for all systems.</div>
<div class="callout"><b>Wraparound adapter:</b> QAB accepts one increasing Quran span per timed segment. RAS can return several repeated or wraparound ranges inside one segment without internal time boundaries. Those rows are submitted as null claims; inventing proportional boundaries would change the system output. This materially lowers Tibyan Large on Fatir, where it aligns most of the recording as multi-range rows.</div>
<h2>Cohort metrics</h2><table><thead><tr><th>Cohort</th><th>Cases</th><th>System</th><th>Words F1</th><th>Clean segments</th><th>Repeats F1</th><th>Segments F1</th><th>Boundaries F1</th></tr></thead><tbody>{''.join(cohort_rows)}</tbody></table>
<h2>Noisy recordings</h2><table><thead><tr><th>Case</th><th>System</th><th>Words F1</th><th>Segments F1</th><th>Boundaries F1</th></tr></thead><tbody>{''.join(noisy_rows)}</tbody></table>
<p>Base remains the deployed API policy and may silently retry a low-confidence case with Large. These figures compare deployed modes, matching the legacy run's behavior.</p></main></body></html>"""
    path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--tibyan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cases = prepare(load_rows(args.parquet), args.tibyan)
    systems = [
        System("r15", "R15 Base", "Base", args.baseline, "hetchyy/r15_95m"),
        System("r7", "R7 Large", "Large", args.baseline, "hetchyy/r7"),
        System("tibyan_base", "Tibyan Base", "Base", args.tibyan, "hetchyy/tibyan-base-v1"),
        System(
            "tibyan_large",
            "Tibyan Large",
            "Large",
            args.tibyan,
            "hetchyy/tibyan-large-v1",
        ),
    ]
    by_id = {case.id: case for case in cases}
    paired_ids = [case.id for case in cases if all(system.record(case.id) for system in systems)]
    noisy_ids = [case.id for case in cases if case.noisy and case.id in paired_ids]
    cohorts = {
        "Full Base (16)": ([systems[0], systems[2]], cases),
        "Paired all-model (12)": (systems, [by_id[case_id] for case_id in paired_ids]),
        "Noisy (5)": (systems, [by_id[case_id] for case_id in noisy_ids]),
    }
    evidence = {"cohorts": {}, "noisy_cases": {}}
    reports = {}
    for cohort, (cohort_systems, cohort_cases) in cohorts.items():
        values = {system.label: score(system, cohort_cases) for system in cohort_systems}
        reports[cohort] = values
        evidence["cohorts"][cohort] = {
            "cases": len(cohort_cases),
            "case_ids": [case.id for case in cohort_cases],
            "systems": {
                system: {"metrics": metrics(report)} for system, report in values.items()
            },
        }
    for case_id in noisy_ids:
        evidence["noisy_cases"][case_id] = {}
        for system in systems:
            report = score(system, [by_id[case_id]])
            evidence["noisy_cases"][case_id][system.label] = metrics(report)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "comparison.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    render(evidence, args.out / "comparison.html")
    print(args.out / "comparison.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
