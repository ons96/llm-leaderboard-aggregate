# scrapers/AGENTS.md

This directory is reserved for scraper-related docs and future helper scripts.

Scraper implementation lives in:

- `modeldb_builder/sources/`

Key conventions:

- Source modules should be fetch/parse/validate split where possible.
- Failures must be non-fatal for optional sources and should be recorded in `data/raw/scrape_manifest.json`.

