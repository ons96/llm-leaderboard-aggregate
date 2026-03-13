from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class UniqueModelRow:
    model_id: str
    model_name: str | None
    model_family: str | None
    developer: str | None
    release_date: str | None
    context_window_tokens: int | None
    mode: str | None
    sources_found_in: str
    canonical_model_id_litellm: str | None
    canonical_model_id_openrouter: str | None
    canonical_model_id_modelsdev: str | None
    dedup_confidence: str
    dedup_notes: str | None


@dataclass(frozen=True)
class ProviderRow:
    model_id: str
    provider_name: str
    provider_model_id: str
    input_cost_per_token: float | None
    output_cost_per_token: float | None
    is_free_tier: int | None
    context_window_tokens: int | None
    mode: str | None
    avg_tokens_per_second: float | None
    avg_ttft_ms: float | None
    quality_score_artificial_analysis: float | None
    data_source: str
    last_updated: str


def upsert_models_unique(conn: sqlite3.Connection, rows: Iterable[UniqueModelRow]) -> None:
    conn.executemany(
        """
        INSERT INTO models_unique (
          model_id, model_name, model_family, developer, release_date,
          context_window_tokens, mode, sources_found_in,
          canonical_model_id_litellm, canonical_model_id_openrouter, canonical_model_id_modelsdev,
          dedup_confidence, dedup_notes,
          avg_agentic_coding_score, avg_reasoning_chat_score
        ) VALUES (
          :model_id, :model_name, :model_family, :developer, :release_date,
          :context_window_tokens, :mode, :sources_found_in,
          :canonical_model_id_litellm, :canonical_model_id_openrouter, :canonical_model_id_modelsdev,
          :dedup_confidence, :dedup_notes,
          NULL, NULL
        )
        ON CONFLICT(model_id) DO UPDATE SET
          model_name=excluded.model_name,
          model_family=excluded.model_family,
          developer=excluded.developer,
          release_date=excluded.release_date,
          context_window_tokens=excluded.context_window_tokens,
          mode=excluded.mode,
          sources_found_in=excluded.sources_found_in,
          canonical_model_id_litellm=excluded.canonical_model_id_litellm,
          canonical_model_id_openrouter=excluded.canonical_model_id_openrouter,
          canonical_model_id_modelsdev=excluded.canonical_model_id_modelsdev,
          dedup_confidence=excluded.dedup_confidence,
          dedup_notes=excluded.dedup_notes
        ;
        """,
        [r.__dict__ for r in rows],
    )


def upsert_model_providers(conn: sqlite3.Connection, rows: Iterable[ProviderRow]) -> None:
    conn.executemany(
        """
        INSERT INTO model_providers (
          model_id, provider_name, provider_model_id,
          input_cost_per_token, output_cost_per_token, is_free_tier,
          context_window_tokens, mode,
          avg_tokens_per_second, avg_ttft_ms, quality_score_artificial_analysis,
          data_source, last_updated
        ) VALUES (
          :model_id, :provider_name, :provider_model_id,
          :input_cost_per_token, :output_cost_per_token, :is_free_tier,
          :context_window_tokens, :mode,
          :avg_tokens_per_second, :avg_ttft_ms, :quality_score_artificial_analysis,
          :data_source, :last_updated
        )
        ON CONFLICT(provider_name, provider_model_id) DO UPDATE SET
          model_id=excluded.model_id,
          input_cost_per_token=excluded.input_cost_per_token,
          output_cost_per_token=excluded.output_cost_per_token,
          is_free_tier=excluded.is_free_tier,
          context_window_tokens=excluded.context_window_tokens,
          mode=excluded.mode,
          avg_tokens_per_second=excluded.avg_tokens_per_second,
          avg_ttft_ms=excluded.avg_ttft_ms,
          quality_score_artificial_analysis=excluded.quality_score_artificial_analysis,
          data_source=excluded.data_source,
          last_updated=excluded.last_updated
        ;
        """,
        [r.__dict__ for r in rows],
    )

