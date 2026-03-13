from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..util import utc_now_iso


def _existing_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"pragma table_info({table})")
    return {r[1] for r in cur.fetchall()}


def ensure_models_unique_columns(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        cols = _existing_columns(con, "models_unique")
        want: list[tuple[str, str]] = [
            ("benchmark_coverage", "INTEGER"),
            ("swe_bench_verified_pct", "REAL"),
            ("swerebench_pct", "REAL"),
            ("livecodebench_pct", "REAL"),
            ("livebench_coding", "REAL"),
            ("livebench_agentic_coding", "REAL"),
            ("livebench_reasoning", "REAL"),
            ("livebench_overall", "REAL"),
            ("livebench_math", "REAL"),
            ("llmstats_composite_score", "REAL"),
            ("llmstats_coding_score", "REAL"),
            ("aider_polyglot_pct", "REAL"),
            ("arena_elo", "REAL"),
            ("arena_elo_coding", "REAL"),
            ("avg_agentic_coding_score_arena_only", "REAL"),
        ]
        for name, typ in want:
            if name in cols:
                continue
            con.execute(f"alter table models_unique add column {name} {typ}")
        con.commit()
    finally:
        con.close()


def ensure_model_providers_columns(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        cols = _existing_columns(con, "model_providers")
        want: list[tuple[str, str]] = [
            ("provider_score", "REAL"),
            ("avg_agentic_coding_score", "REAL"),
            ("avg_reasoning_chat_score", "REAL"),
            ("benchmark_coverage", "INTEGER"),
            ("swe_bench_verified_pct", "REAL"),
            ("livecodebench_pct", "REAL"),
            ("free_tier_quality", "TEXT"),
            ("free_tier_notes", "TEXT"),
        ]
        for name, typ in want:
            if name in cols:
                continue
            con.execute(f"alter table model_providers add column {name} {typ}")
        con.commit()
    finally:
        con.close()


@dataclass(frozen=True)
class ModelRow:
    model_id: str
    model_name: str | None
    developer: str | None = None
    model_family: str | None = None


def load_canonical_models(db_path: Path) -> list[ModelRow]:
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            "select model_id, model_name, developer, model_family from models_unique"
        )
        return [
            ModelRow(model_id=r[0], model_name=r[1], developer=r[2], model_family=r[3])
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def update_models_unique_metrics(
    db_path: Path,
    *,
    per_model_updates: dict[str, dict[str, float | int | None]],
) -> None:
    if not per_model_updates:
        return
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("begin")
        for model_id, cols in per_model_updates.items():
            keys = list(cols.keys())
            sets = ", ".join(f"{k}=?" for k in keys)
            vals = [cols[k] for k in keys]
            con.execute(f"update models_unique set {sets} where model_id=?", [*vals, model_id])
        con.commit()
    finally:
        con.close()


def load_models_unique_metrics(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("select * from models_unique")
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def load_model_providers_rows(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("select * from model_providers")
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def update_model_providers_metrics(
    db_path: Path,
    *,
    per_provider_updates: Iterable[tuple[str, str, str, dict[str, float | int | None]]],
) -> None:
    updates = list(per_provider_updates)
    if not updates:
        return
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("begin")
        for model_id, provider_name, provider_model_id, cols in updates:
            keys = list(cols.keys())
            sets = ", ".join(f"{k}=?" for k in keys)
            vals = [cols[k] for k in keys]
            con.execute(
                f"""update model_providers
                set {sets}
                where model_id=? and provider_name=? and provider_model_id=?""",
                [*vals, model_id, provider_name, provider_model_id],
            )
        con.commit()
    finally:
        con.close()
