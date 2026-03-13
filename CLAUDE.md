# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM Model Database Builder — aggregates LLM model metadata from multiple public sources into a two-tier SQLite database with CSV exports, then enriches it with benchmark scores and leaderboard rankings. Runs on a schedule via GitHub Actions.

## Commands

```bash
# Setup
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
# Optional Phase 2 deps (rapidfuzz, playwright, pyarrow):
.venv/bin/python -m pip install -r requirements-benchmarks-optional.txt

# Phase 1: Model database (fetches sources, dedup, writes db/models.db)
python -m modeldb_builder --json

# Phase 2: Benchmark enrichment (reads db/models_unique.db, writes leaderboards/)
python -m modeldb_builder.benchmarks --json
```

There is no test suite, linter config, or build system. The project uses direct module invocation.

## Architecture

### Two-Phase Pipeline

**Phase 1** (`modeldb_builder/pipeline.py` → `run_full_update()`):
```
Sources (LiteLLM, OpenRouter, models.dev, Artificial Analysis)
  → Fetch & cache to data/raw/{source}_{current,last_full}.*
  → Parse into SourceProviderRecord[]
  → Normalize via dedup/normalize.py:normalize_model_slug()
  → Deduplicate into two tiers
  → Atomic write to db/models.db
  → Export CSVs + split DBs
```

**Phase 2** (`modeldb_builder/benchmarks/pipeline.py` → `run()`):
```
Benchmark sources (LiveBench, LM Arena, SWE-bench, Aider, etc.)
  → Fetch & cache to data/raw/
  → Parse into ModelBenchmarkRow[]
  → Fuzzy-match to canonical model_id (≥75 similarity auto-match)
  → Compute weighted geometric means (agentic_coding, reasoning_chat)
  → ALTER TABLE + UPDATE existing models_unique/model_providers
  → Export leaderboard CSVs to leaderboards/
```

Phase 2 does NOT rebuild the database — it enriches existing rows in-place.

### Two-Tier Database (`db/models.db`)

- **`models_unique`** (Tier 1): One row per canonical model. Key: `model_id` (normalized slug). Contains metadata + benchmark score aggregates.
- **`model_providers`** (Tier 2): One row per (provider_name × provider_model_id). FK to `models_unique.model_id`. Contains pricing + performance metrics.

### Safe Caching Strategy

Each source uses a two-file pattern in `data/raw/`:
- `{source}_current.*` — written first (transient)
- `{source}_last_full.*` — promoted only after validation succeeds (stable fallback)
- `scrape_manifest.json` — tracks per-source status, row counts, sha256 hashes

### Key Patterns

- **Atomic file writes**: All DB and cache writes go through `util.py:atomic_write_*()` (temp file → fsync → rename)
- **Graceful degradation**: Optional sources (Artificial Analysis, individual benchmarks) fail silently, logged in manifest
- **Canonical identity**: All model matching flows through `dedup/normalize.py:normalize_model_slug()` — lowercase, strip provider prefix, normalize separators, preserve versions
- **Path config**: All paths derived from `config.py:Paths` dataclass (repo-root-relative)

### Scoring Formulas

**avg_agentic_coding_score** (weighted geometric mean):
- SWE-bench Verified % (40%), LiveCodeBench % (25%), SWE-rebench % (20%), Arena ELO coding normalized (15%)

**avg_reasoning_chat_score** (weighted geometric mean):
- LiveBench reasoning (35%), LiveBench overall (30%), Arena ELO normalized (35%)

### Adding a New Source

Phase 1: Add parser in `modeldb_builder/sources/` returning `list[SourceProviderRecord]`, wire into `pipeline.py`.
Phase 2: Add scraper in `modeldb_builder/benchmarks/sources/` returning `list[ModelBenchmarkRow]`, wire into `benchmarks/pipeline.py`.

### GitHub Actions

| Workflow | Schedule | What it runs |
|---|---|---|
| `update_models.yml` | Daily 02:00 UTC | Phase 1, validates >1000 unique models |
| `update_benchmarks.yml` | Daily 03:00 UTC | Phase 2, validates leaderboard rows >50 |
| `update_all.yml` | Weekly Sunday 01:00 UTC | Full refresh (both phases) |

Workflows auto-commit to `db/`, `data/raw/`, and `leaderboards/` on success.
