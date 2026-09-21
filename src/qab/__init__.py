"""Quran recitation alignment benchmark: schemas, scorer, corpus access.

    from qab import load_cases, load_submissions, evaluate
    report = evaluate(load_cases("v1"), *load_submissions("submissions/"), corpus_version="v1")
"""
__version__ = "0.2.0"

from .corpus import fetch, load_cases, load_submissions  # noqa: E402
from .report import evaluate  # noqa: E402
from .schema import Case, Segment, Submission, SubmissionMeta  # noqa: E402
from .scoring import score_case  # noqa: E402

__all__ = ["__version__", "Case", "Segment", "Submission", "SubmissionMeta", "evaluate", "fetch",
           "load_cases", "load_submissions", "score_case"]
