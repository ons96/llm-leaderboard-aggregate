# Phase 2: Benchmark aggregation pipeline

This repo includes an additive Phase 2 pipeline that enriches the Phase 1 model catalog with benchmark and provider performance signals.

## Run

```bash
.venv/bin/python -m modeldb_builder.benchmarks --json
```

## Inputs

- Canonical models: `db/models_unique.db` (fallback: `db/models.db`)
- Provider rows: `db/model_providers.db`

## Outputs

- Model-level scores/columns are written back into `models_unique` tables (via `ALTER TABLE` if needed):
  - Raw: `swe_bench_verified_pct`, `swerebench_pct`, `livecodebench_pct`, `livebench_*`, `arena_elo`, `arena_elo_coding`, `llmstats_*`
  - Aggregates: `avg_agentic_coding_score`, `avg_reasoning_chat_score`, `benchmark_coverage`
- Provider-level performance and `provider_score` are written into `db/model_providers.db`.
- Ranked CSVs are exported to `leaderboards/` (see `leaderboards/AGENTS.md`).

## Matching

Benchmark model names are joined to canonical `model_id` using `modeldb_builder.dedup.normalize_model_slug()` then fuzzy similarity:

- ≥ 75: auto-match
- < 75: logged as `needs_review` / `unmatched` (no auto-apply)

All non-matched rows are written to `data/raw/unmatched_benchmarks.csv` (and `_current/_last_full`).
