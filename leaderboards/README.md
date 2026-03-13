# Leaderboard Files

This directory contains ranked CSV exports from the benchmark aggregation pipeline.

## Files

| File | Description |
|------|-------------|
| `agentic_coding_leaderboard.csv` | Models ranked by agentic coding capability |
| `reasoning_chat_leaderboard.csv` | Models ranked by reasoning chat capability |
| `gateway_fallback_ranking.csv` | Provider endpoints ranked by composite score |
| `gateway_fallback_free_only.csv` | Free-tier provider endpoints only |
| `gateway_fallback_all_models.csv` | All provider endpoints (free + paid) |
| `leaderboard_metadata.json` | Run metadata and source status |

## Column Definitions

### Model Leaderboards

| Column | Description |
|--------|-------------|
| `rank` | Position in ranking (1 = best) |
| `model_id` | Canonical model identifier |
| `model_name` | Display name |
| `avg_agentic_coding_score` | Weighted agentic coding score (0-100) |
| `avg_reasoning_chat_score` | Weighted reasoning score (0-100) |
| `benchmark_coverage` | Number of benchmarks with data |
| `aider_polyglot_pct` | Aider Polyglot benchmark pass rate |
| `livebench_coding` | LiveBench coding score |
| `arena_elo` | LM Arena ELO rating |

### Gateway Fallback CSVs

| Column | Description |
|--------|-------------|
| `rank` | Position in ranking |
| `model_id` | Canonical model ID |
| `provider_name` | Provider name (e.g., openai, google) |
| `provider_model_id` | Provider-specific model ID |
| `provider_score` | Composite score (0-100) |
| `avg_tps` | Average tokens per second |
| `avg_ttft_ms` | Average time to first token (ms) |
| `input_cost_per_token` | Input cost (USD) |
| `output_cost_per_token` | Output cost (USD) |
| `is_free_tier` | 1 = free, 0 = paid, NULL = unknown |
| `avg_agentic_coding_score` | Model's agentic coding score |

## Score Formulas

### avg_agentic_coding_score

```
avg_agentic_coding_score = weighted_geometric_mean(
  aider_polyglot_pct (35%),
  livebench_coding (35%),
  arena_elo_normalized (30%)
)
```

### avg_reasoning_chat_score

```
avg_reasoning_chat_score = weighted_geometric_mean(
  livebench_reasoning (40%),
  livebench_overall (30%),
  arena_elo_normalized (30%)
)
```

### provider_score

```
provider_score = (agentic_coding_score * 0.5)
              + (normalized_tps * 0.3)
              + (normalized_ttft_inverted * 0.2)
```

Where:
- `normalized_tps` is min-max normalized to 0-100
- `normalized_ttft_inverted` is TTFT normalized then inverted (lower is better)

## Using gateway_fallback_free_only.csv

This file is designed for LLM gateways with $0 budget. Example LiteLLM router config:

```yaml
model_list:
  - model_name: gpt-5-free
    litellm_params:
      model: github-copilot/gpt-5
      api_key: os.environ/GITHUB_TOKEN
  - model_name: o3-free
    litellm_params:
      model: github-models/openai/o3
      api_key: os.environ/GITHUB_TOKEN
  - model_name: grok-3-free
    litellm_params:
      model: github-models/xai/grok-3
      api_key: os.environ/GITHUB_TOKEN
```

## Manual Refresh

To trigger a full refresh via GitHub Actions:

1. Go to https://github.com/your-org/llm-leaderboard-aggregate/actions
2. Click "Run workflow"
3. Select "Update All" for full refresh, or individual workflows for partial

Or use GitHub CLI:
```bash
gh workflow run update_all.yml
```
