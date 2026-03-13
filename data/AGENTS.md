# data/AGENTS.md

`data/raw/` is the raw-cache layer for each upstream source.

File conventions:

- `litellm_current.json`, `litellm_last_full.json`
- `openrouter_current.json`, `openrouter_last_full.json`
- `modelsdev_current.json`, `modelsdev_last_full.json`
- `artificial_analysis_current.html`, `artificial_analysis_last_full.html`
- `scrape_manifest.json`: success/failure, row counts, hashes
- `scrape_state.json`: checkpoint state for resumable scrapes

Invariant:

- `_last_full` is only replaced after the current run finishes and passes validation for that source.

