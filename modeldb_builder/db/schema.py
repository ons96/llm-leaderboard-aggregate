from __future__ import annotations

import sqlite3


def init_schema(conn: sqlite3.Connection) -> None:
    # Keep schema intentionally extensible: new benchmark columns can be added later.
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS models_unique (
          model_id TEXT PRIMARY KEY,
          model_name TEXT,
          model_family TEXT,
          developer TEXT,
          release_date TEXT,
          context_window_tokens INTEGER,
          mode TEXT,
          sources_found_in TEXT,
          canonical_model_id_litellm TEXT,
          canonical_model_id_openrouter TEXT,
          canonical_model_id_modelsdev TEXT,
          dedup_confidence TEXT,
          dedup_notes TEXT,
          avg_agentic_coding_score REAL,
          avg_reasoning_chat_score REAL
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_providers (
          model_id TEXT NOT NULL,
          provider_name TEXT NOT NULL,
          provider_model_id TEXT NOT NULL,
          input_cost_per_token REAL,
          output_cost_per_token REAL,
          is_free_tier INTEGER,
          context_window_tokens INTEGER,
          mode TEXT,
          avg_tokens_per_second REAL,
          avg_ttft_ms REAL,
          quality_score_artificial_analysis REAL,
          data_source TEXT NOT NULL,
          last_updated TEXT NOT NULL,
          PRIMARY KEY (provider_name, provider_model_id),
          FOREIGN KEY (model_id) REFERENCES models_unique(model_id)
        );
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_providers_model_id ON model_providers(model_id);")

