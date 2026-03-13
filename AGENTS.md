# AGENTS.md

This repo builds a continuously-updatable LLM model catalog by aggregating multiple public sources into a two-tier SQLite database and exporting CSV snapshots for inspection and downstream use.

## Repo Structure

- `modeldb_builder/`: Python package (pipeline, source ingestors, dedup logic, SQLite schema/writer)
- `data/raw/`: raw source caches + run manifest/state
- `db/`: `models.db` + CSV snapshots + last-run summary JSON
- `scrapers/`: documentation placeholder for scraping conventions (actual code is in `modeldb_builder/sources/`)
- `.github/workflows/update_models.yml`: scheduled GitHub Actions updater

## Data Sources

- LiteLLM (direct JSON): `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`
- OpenRouter models list (no auth): `https://openrouter.ai/api/v1/models`
- models.dev (public JSON): `https://models.dev/api.json`
- Artificial Analysis (optional, best-effort): `https://artificialanalysis.ai/leaderboards/models`

## Two-Tier Database Design

Primary store is a single SQLite DB at `db/models.db` containing both tiers to preserve FK integrity.

Tier 1 table: `models_unique`

- One row per canonical model (`model_id`)
- Stores normalized identity + aggregated metadata
- Includes stubs for future benchmark columns (e.g., `avg_agentic_coding_score`)

Tier 2 table: `model_providers`

- One row per (provider_name × provider_model_id)
- Stores provider-specific pricing and future performance metrics
- `model_id` is a FK to `models_unique.model_id`

SQLite outputs:

- `db/models.db` (canonical DB, both tables)
- `db/models_unique.db` (single-table snapshot)
- `db/model_providers.db` (single-table snapshot)

CSV snapshots are exported to:

- `db/models_unique.csv`
- `db/model_providers.csv`

## Dedup / Identity Rules

Canonical `model_id` is derived via `modeldb_builder.dedup.normalize_model_slug()`:

- Lowercase
- Strip a single known provider/platform prefix (best-effort)
- Drop call-variant suffixes like `:free`
- Normalize separators (`_ . space /` to `-`)
- Preserve version identifiers (dates, numeric versions, sizes) as literal tokens

Ambiguity handling:

- `models_unique.dedup_confidence` is `high|medium|low`
- `models_unique.dedup_notes` explains why a row may be ambiguous (for example, presence of `latest` aliases)

## Raw Caching and Integrity

Raw caches live in `data/raw/` and use a two-file strategy per source:

- `{source}_current.*`: written first for the current run
- `{source}_last_full.*`: only overwritten after validation succeeds

Run metadata:

- `data/raw/scrape_manifest.json`: per-source ok/fail, row counts, and hashes
- `data/raw/scrape_state.json`: checkpoint state (used for resumable/multi-step scrapes)

## How To Run

Local:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m modeldb_builder --json
```

Automation:

- GitHub Actions workflow commits updated `db/` and `data/raw/` on schedule.

## Phase 3: GitHub Actions & Benchmark Pipeline

### Workflows

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `update_models.yml` | Daily 02:00 UTC | Refresh model DB (LiteLLM, OpenRouter, models.dev) |
| `update_benchmarks.yml` | Daily 03:00 UTC | Refresh benchmarks (LiveBench, Aider, LM Arena) |
| `update_all.yml` | Weekly Sunday 01:00 UTC | Full refresh (models + benchmarks) |

All workflows include validation steps that abort and don't commit if:
- Required output files are missing
- Row counts are below thresholds
- Fewer than 2 benchmark sources succeed

### Benchmark Sources

| Source | Status | URL |
|--------|--------|-----|
| LiveBench | ✅ Working | HuggingFace dataset |
| Aider | ✅ Working | aider.chat |
| LM Arena | ✅ Working | HuggingFace |
| AwesomeAgents | ✅ Working | awesomeagents.ai |
| Artificial Analysis | ⚠️ Best-effort | JS-rendered (requires Playwright) |

### Gateway Fallback Files

Three CSV variants are generated:

- `gateway_fallback_ranking.csv` - Default ranking (provider_score)
- `gateway_fallback_free_only.csv` - Free-tier only (is_free_tier=1)
- `gateway_fallback_all_models.csv` - All models (free + paid)

### is_free_tier Logic

A provider row is marked free when:
- `input_cost_per_token = 0` AND `output_cost_per_token = 0`

Values:
- `1` = free tier
- `0` = not free (known cost)
- `NULL` = unknown (cost data unavailable)
