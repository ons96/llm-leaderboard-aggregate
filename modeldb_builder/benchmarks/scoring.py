from __future__ import annotations

import math
from dataclasses import dataclass


def minmax_0_100(values: dict[str, float | None]) -> dict[str, float | None]:
    present = [v for v in values.values() if v is not None]
    if len(present) < 2:
        return {k: None if v is None else 50.0 for k, v in values.items()}
    lo = min(present)
    hi = max(present)
    if hi == lo:
        return {k: None if v is None else 50.0 for k, v in values.items()}
    out: dict[str, float | None] = {}
    for k, v in values.items():
        if v is None:
            out[k] = None
        else:
            out[k] = (float(v) - lo) * 100.0 / (hi - lo)
    return out


def minmax_invert_0_100(values: dict[str, float | None]) -> dict[str, float | None]:
    present = [v for v in values.values() if v is not None]
    if len(present) < 2:
        return {k: None if v is None else 50.0 for k, v in values.items()}
    lo = min(present)
    hi = max(present)
    if hi == lo:
        return {k: None if v is None else 50.0 for k, v in values.items()}
    out: dict[str, float | None] = {}
    for k, v in values.items():
        if v is None:
            out[k] = None
        else:
            out[k] = (hi - float(v)) * 100.0 / (hi - lo)
    return out


def weighted_geometric_mean_0_100(values_and_weights: list[tuple[float, float]]) -> float | None:
    items = [(v, w) for v, w in values_and_weights if v is not None and w > 0]
    if not items:
        return None
    # Clamp away from 0 to keep log finite.
    eps = 1e-6
    num = 0.0
    den = 0.0
    for v, w in items:
        v2 = max(eps, min(100.0, float(v)))
        num += w * math.log(v2)
        den += w
    if den <= 0:
        return None
    return float(math.exp(num / den))


def _coverage_confidence(present_count: int, total_count: int) -> float:
    """Discount factor for scores based on benchmark coverage.

    Models with minimal benchmark data get a reduced score so they don't
    appear artificially high-ranked alongside thoroughly-benchmarked models.
    """
    if total_count <= 0 or present_count >= total_count:
        return 1.0
    # Lookup tables: discount factor by (present, total) ratio.
    # designed so 1-of-4 benchmarks => 0.60, 2-of-4 => 0.80, etc.
    ratio = present_count / total_count
    if ratio >= 1.0:
        return 1.0
    if ratio >= 0.75:
        return 0.92
    if ratio >= 0.5:
        return 0.80
    if ratio >= 0.25:
        return 0.65
    return 0.50


@dataclass(frozen=True)
class ModelScores:
    avg_agentic_coding_score: float | None
    avg_agentic_coding_score_arena_only: float | None
    avg_reasoning_chat_score: float | None
    benchmark_coverage: int


def compute_model_scores(
    *,
    swe_bench_verified_pct: float | None,
    livecodebench_pct: float | None,
    swerebench_pct: float | None,
    livebench_reasoning: float | None,
    livebench_overall: float | None,
    arena_elo_overall_norm: float | None,
    arena_elo_coding_norm: float | None,
    arena_elo: float | None,
    arena_elo_coding: float | None,
    livebench_coding: float | None,
    livebench_agentic_coding: float | None,
    llmstats_composite_score: float | None,
    llmstats_coding_score: float | None,
) -> ModelScores:
    agentic_raw = weighted_geometric_mean_0_100(
        [
            (swe_bench_verified_pct, 0.40),
            (livecodebench_pct, 0.25),
            (swerebench_pct, 0.20),
            (arena_elo_coding_norm, 0.15),
        ]
    )
    reasoning = weighted_geometric_mean_0_100(
        [
            (livebench_reasoning, 0.35),
            (livebench_overall, 0.30),
            (arena_elo_overall_norm, 0.35),
        ]
    )
    has_swebench = swe_bench_verified_pct is not None
    has_swerebench = swerebench_pct is not None
    has_livecodebench = livecodebench_pct is not None
    has_lmarena = (arena_elo is not None) or (arena_elo_coding is not None)
    has_livebench = any(
        v is not None
        for v in (
            livebench_coding,
            livebench_agentic_coding,
            livebench_reasoning,
            livebench_overall,
        )
    )
    has_llmstats = (llmstats_composite_score is not None) or (
        llmstats_coding_score is not None
    )
    coverage = int(
        sum(
            1
            for b in (
                has_swebench,
                has_livecodebench,
                has_swerebench,
                has_lmarena,
                has_livebench,
                has_llmstats,
            )
            if b
        )
    )

    # Gate: a model needs at least one actual coding benchmark to receive
    # an agentic coding score.  Arena ELO alone is not sufficient.
    has_coding_benchmark = any([
        has_swebench,
        has_livecodebench,
        has_swerebench,
        livebench_agentic_coding is not None,
    ])

    # Count how many of the 4 agentic coding components are present.
    agentic_components_present = sum(1 for v in (
        swe_bench_verified_pct,
        livecodebench_pct,
        swerebench_pct,
        arena_elo_coding_norm,
    ) if v is not None)

    # Count how many of the 3 reasoning components are present.
    reasoning_components_present = sum(1 for v in (
        livebench_reasoning,
        livebench_overall,
        arena_elo_overall_norm,
    ) if v is not None)

    # Apply coverage confidence discount: models with sparse benchmark data
    # get a reduced score to prevent minimal data from inflating rankings.
    if has_coding_benchmark:
        if agentic_raw is not None:
            discount = _coverage_confidence(agentic_components_present, 4)
            agentic = agentic_raw * discount
        else:
            agentic = None
        agentic_arena_only = None
    else:
        # Preserve the arena-derived score separately so it's not lost.
        agentic_arena_only = agentic_raw
        agentic = None

    if reasoning is not None:
        reasoning_discount = _coverage_confidence(reasoning_components_present, 3)
        reasoning = reasoning * reasoning_discount

    return ModelScores(
        avg_agentic_coding_score=agentic,
        avg_agentic_coding_score_arena_only=agentic_arena_only,
        avg_reasoning_chat_score=reasoning,
        benchmark_coverage=coverage,
    )


def compute_provider_score(
    *,
    model_agentic_coding_score: float | None,
    tps_norm: float | None,
    ttft_inverted_norm: float | None,
) -> float | None:
    # Phase 2: gateway fallback ranks primarily by agentic coding score. Provider-level
    # TPS/TTFT components are intentionally disabled until a reliable provider benchmark
    # source is reintroduced.
    return None if model_agentic_coding_score is None else float(model_agentic_coding_score)
