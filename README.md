# LLM Model Database Builder

Builds a continuously-updatable LLM model database (SQLite + CSV snapshots) by aggregating:

- LiteLLM `model_prices_and_context_window.json`
- OpenRouter `GET /api/v1/models`
- models.dev `api.json`
- (Optional, best-effort) Artificial Analysis leaderboard scrape

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m modeldb_builder --json
```

Outputs:

- `db/models.db` (SQLite with both tier tables)
- `db/models_unique.db` (single-table snapshot)
- `db/model_providers.db` (single-table snapshot)
- `db/models_unique.csv`
- `db/model_providers.csv`
- `data/raw/*_{current,last_full}.*` (raw caches) + `data/raw/scrape_manifest.json`

## Schema

This repo keeps both tiers inside a single SQLite DB (`db/models.db`) to preserve FK integrity.

### Tier 1: `models_unique`

One row per canonical (deduplicated) model.

Key columns:

- `model_id` (canonical slug, derived by normalization)
- `model_name`, `model_family`, `developer`, `release_date`
- `context_window_tokens`, `mode`
- `sources_found_in` (comma-separated)
- `canonical_model_id_litellm`, `canonical_model_id_openrouter`, `canonical_model_id_modelsdev`
- `dedup_confidence` (`high|medium|low`) + `dedup_notes`
- `avg_agentic_coding_score`, `avg_reasoning_chat_score` (stubs for later benchmark ingestion)

### Tier 2: `model_providers`

One row per (provider_name × provider_model_id) with pricing + performance stubs.

Key columns:

- `model_id` (FK to `models_unique.model_id`)
- `provider_name`, `provider_model_id`
- `input_cost_per_token`, `output_cost_per_token`
- `is_free_tier` (best-effort)
- `context_window_tokens`, `mode`
- `avg_tokens_per_second`, `avg_ttft_ms`, `quality_score_artificial_analysis` (stubs / best-effort)
- `data_source`, `last_updated`

## Safe Raw Caching

Each source writes to `data/raw/{source}_current.*` first, then only overwrites
`data/raw/{source}_last_full.*` after validation succeeds. `data/raw/scrape_manifest.json`
tracks success/failure, row counts, and content hashes (when applicable).

## Notes / Known Limitations

- Artificial Analysis is implemented as best-effort scraping. If the site changes or blocks scraping,
  the pipeline logs the failure in `data/raw/scrape_manifest.json` and continues.
- `is_free_tier` is heuristic. For models.dev, `$0` does not necessarily mean free-to-use without a subscription.
