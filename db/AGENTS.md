# db/AGENTS.md

Primary outputs:

- `models.db`: SQLite database containing both `models_unique` and `model_providers`
- `models_unique.db`: SQLite snapshot containing only `models_unique`
- `model_providers.db`: SQLite snapshot containing only `model_providers`
- `models_unique.csv`: snapshot export of `models_unique`
- `model_providers.csv`: snapshot export of `model_providers`
- `last_run_summary.json`: counters and output paths for the most recent run

Write strategy:

- The pipeline builds into `db/models.db.tmp` first, then atomically replaces `db/models.db` only on success.
