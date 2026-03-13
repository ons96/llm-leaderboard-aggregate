from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


SourceKey = Literal[
    "livebench",
    "lmarena",
    "swebench",
    "swerebench",
    "livecodebench",
    "llmstats",
    "valsai",
    "aider",
    "artificial_analysis",
    "awesomeagents",
]


@dataclass(frozen=True)
class FetchMeta:
    source: SourceKey
    fetched_at: str
    url: str | None
    ok: bool
    row_count: int
    sha256: str | None
    error: str | None = None


@dataclass(frozen=True)
class ModelBenchmarkRow:
    source: SourceKey
    model_name_raw: str
    metrics: dict[str, float]
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderPerfRow:
    source: SourceKey
    provider_name_raw: str
    model_name_raw: str
    metrics: dict[str, float]
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class MatchResult:
    source: SourceKey
    raw_name: str
    normalized_name: str
    status: Literal["matched", "needs_review", "unmatched"]
    model_id: str | None
    score: float | None
    reason: str | None = None
