"""Public data contracts: ground-truth cases, submissions and submission metadata.

Every model rejects unknown fields and non-finite numbers. Validation is the
benchmark's, not a convenience: a file that fails here is not scorable.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import refs

SCHEMA_VERSION_CASE = 2
SCHEMA_VERSION_SUBMISSION = 1
MAX_OVERLAP_S = 0.5
DURATION_SLACK_S = 1.0

Riwayah = Literal["hafs_an_asim"]   # the scorer's word numbering is Hafs; other readings need their own table
Style = Literal["murattal", "mujawwad", "hadr", "muallim"]
Content = Literal["quran_only", "prayer"]
HardwareClass = Literal["cpu", "gpu"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Interval(Strict):
    start_s: float = Field(ge=0)
    end_s: float

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_s <= self.start_s:
            raise ValueError("interval end must follow its start")
        return self

    @property
    def midpoint(self) -> float:
        return (self.start_s + self.end_s) / 2


class Word(Interval):
    word: str

    @field_validator("word")
    @classmethod
    def _label(cls, value: str) -> str:
        refs.truth_token(value)
        return value

    @property
    def token(self) -> refs.Token:
        return refs.truth_token(self.word)


class ReviewedSegment(Interval):
    first_word: str
    last_word: str


class Case(Strict):
    """One recording's ground truth plus the facets the leaderboard slices on."""

    schema_version: Literal[2] = SCHEMA_VERSION_CASE
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    duration_s: float = Field(gt=0)
    riwayah: Riwayah = "hafs_an_asim"
    reciter: str | None = None
    description: str | None = None
    style: Style
    content: Content
    noisy: bool
    multi_surah: bool
    words: list[Word] = Field(min_length=1)
    segments: list[ReviewedSegment] = Field(default_factory=list)
    non_quran: list[Interval] = Field(default_factory=list)

    @model_validator(mode="after")
    def _timeline(self):
        for left, right in zip(self.words, self.words[1:]):
            if right.start_s < left.start_s:
                raise ValueError(f"words out of order at {right.word}")
            if right.start_s < left.end_s - 1e-6:
                raise ValueError(f"words overlap at {left.word} / {right.word}")
        limit = self.duration_s + DURATION_SLACK_S
        if self.words[-1].end_s > limit:
            raise ValueError("last word ends after the audio")
        nq = sorted(self.non_quran, key=lambda x: x.start_s)
        for left, right in zip(nq, nq[1:]):
            if right.start_s < left.end_s - 1e-6:
                raise ValueError("non_quran intervals overlap")
        for iv in nq:
            if iv.end_s > limit:
                raise ValueError("non_quran interval ends after the audio")
            for w in self.words:
                if w.start_s < iv.end_s - 1e-6 and iv.start_s < w.end_s - 1e-6:
                    raise ValueError(f"non_quran interval overlaps word {w.word}")
        return self

    @property
    def facets(self) -> dict[str, str]:
        return {"riwayah": self.riwayah, "style": self.style, "content": self.content,
                "noisy": str(self.noisy).lower(), "multi_surah": str(self.multi_surah).lower()}


class Segment(Strict):
    start_s: float = Field(ge=0)
    end_s: float
    reference: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("reference")
    @classmethod
    def _reference(cls, value: str | None) -> str | None:
        refs.claim_tokens(value)
        return value

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_s <= self.start_s:
            raise ValueError("segment end must follow its start")
        return self

    @property
    def midpoint(self) -> float:
        return (self.start_s + self.end_s) / 2

    @property
    def may_claim_quran(self) -> bool:
        """A span or `Basmala` can be Quran-claiming once matched (SPEC 4.2); Isti'adha and null cannot."""
        return refs.is_span(self.reference) or self.reference == refs.BASMALA

    @property
    def confidence_eligible(self) -> bool:
        """Only span references participate in consumer-confidence scoring (SPEC 4.9)."""
        return refs.is_span(self.reference)


class Submission(Strict):
    schema_version: Literal[1] = SCHEMA_VERSION_SUBMISSION
    case_id: str
    runtime_seconds: float | None = Field(default=None, gt=0)
    segments: list[Segment]

    @model_validator(mode="after")
    def _invariants(self):
        for i, (left, right) in enumerate(zip(self.segments, self.segments[1:]), 1):
            if right.start_s < left.start_s:
                raise ValueError(f"segments must be ordered by start_s (segment {i})")
            if left.end_s - right.start_s > MAX_OVERLAP_S + 1e-9:
                raise ValueError(f"segments {i - 1} and {i} overlap by more than {MAX_OVERLAP_S} s")
        with_conf = [s.confidence is not None for s in self.segments if s.confidence_eligible]
        if any(with_conf) and not all(with_conf):
            raise ValueError("confidence is all-or-nothing across Quran-span segments")
        return self

    @property
    def confidence_reported(self) -> bool:
        eligible = [s for s in self.segments if s.confidence_eligible]
        return bool(eligible) and all(s.confidence is not None for s in eligible)


class SubmissionMeta(Strict):
    system: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)
    hardware_class: HardwareClass | None = None
    hardware: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=200)
