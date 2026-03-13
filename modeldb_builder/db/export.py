from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def export_csv_snapshots(db_path: Path, models_unique_csv: Path, model_providers_csv: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        df1 = pd.read_sql_query("SELECT * FROM models_unique ORDER BY model_id;", conn)
        df2 = pd.read_sql_query(
            "SELECT * FROM model_providers ORDER BY provider_name, provider_model_id;", conn
        )
    models_unique_csv.parent.mkdir(parents=True, exist_ok=True)
    df1.to_csv(models_unique_csv, index=False)
    df2.to_csv(model_providers_csv, index=False)


def export_split_sqlite_dbs(db_path: Path, models_unique_db: Path, model_providers_db: Path) -> None:
    """Create per-tier SQLite snapshots (no cross-db FK constraints)."""
    models_unique_db.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as src:
        src.row_factory = sqlite3.Row

        rows_unique = src.execute("SELECT * FROM models_unique;").fetchall()
        rows_prov = src.execute("SELECT * FROM model_providers;").fetchall()

        # models_unique.db
        if models_unique_db.exists():
            models_unique_db.unlink()
        with sqlite3.connect(models_unique_db) as dst:
            cols = src.execute("PRAGMA table_info(models_unique);").fetchall()
            col_defs = ", ".join([f"{c[1]} {c[2]}" for c in cols])
            dst.execute(f"CREATE TABLE models_unique ({col_defs});")
            if rows_unique:
                names = [c[1] for c in cols]
                placeholders = ", ".join(["?"] * len(names))
                dst.executemany(
                    f"INSERT INTO models_unique ({', '.join(names)}) VALUES ({placeholders});",
                    [[r[n] for n in names] for r in rows_unique],
                )

        # model_providers.db
        if model_providers_db.exists():
            model_providers_db.unlink()
        with sqlite3.connect(model_providers_db) as dst:
            cols = src.execute("PRAGMA table_info(model_providers);").fetchall()
            col_defs = ", ".join([f"{c[1]} {c[2]}" for c in cols])
            dst.execute(f"CREATE TABLE model_providers ({col_defs});")
            if rows_prov:
                names = [c[1] for c in cols]
                placeholders = ", ".join(["?"] * len(names))
                dst.executemany(
                    f"INSERT INTO model_providers ({', '.join(names)}) VALUES ({placeholders});",
                    [[r[n] for n in names] for r in rows_prov],
                )
