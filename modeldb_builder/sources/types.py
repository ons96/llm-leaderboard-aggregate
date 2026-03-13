from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceProviderRecord:
    source: str
    provider_name: str | None
    provider_model_id: str
    model_display_name: str | None
    developer: str | None
    release_date: str | None
    context_window_tokens: int | None
    mode: str | None
    input_cost_per_token: float | None
    output_cost_per_token: float | None
    is_free_tier: int | None  # 0/1/NULL best-effort


@dataclass(frozen=True)
class ArtificialAnalysisMetrics:
    provider_name: str | None
    provider_model_id: str | None
    model_display_name: str | None
    avg_tokens_per_second: float | None
    avg_ttft_ms: float | None
    quality_score: float | None

