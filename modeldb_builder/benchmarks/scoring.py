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
    if has_coding_benchmark:
        agentic = agentic_raw
        agentic_arena_only = None
    else:
        # Preserve the arena-derived score separately so it's not lost.
        agentic_arena_only = agentic_raw
        agentic = None

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
