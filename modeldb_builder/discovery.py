"""New free-tier provider discovery diff.

Compares the current set of free-tier provider endpoints (from model_providers DB)
against a known_free_providers.json manifest. Newly discovered free providers are
flagged in new_free_providers_report.json for manual review or automated gateway
config updates.

Designed to run as a post-Phase-1 step.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import Paths, default_paths
from .util import atomic_write_json, utc_now_iso


def _known_providers_path(paths: Paths) -> Path:
    return paths.data_dir / "known_free_providers.json"


def _report_path(paths: Paths) -> Path:
    return paths.data_dir / "new_free_providers_report.json"


def _load_known(path: Path) -> set[str]:
    """Load known free provider keys as a set of 'provider_name::provider_model_id'."""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text("utf-8"))
        if isinstance(data, dict):
            return set(data.get("providers", []))
        return set()
    except Exception:
        return set()


def _current_free_providers(db_path: Path) -> list[dict[str, Any]]:
    """Query model_providers DB for all free-tier endpoints."""
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT model_id, provider_name, provider_model_id,
                   context_window_tokens, data_source
            FROM model_providers
            WHERE is_free_tier = 1
            ORDER BY provider_name, provider_model_id
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _make_key(provider_name: str, provider_model_id: str) -> str:
    return f"{provider_name}::{provider_model_id}"


def run_discovery_diff(paths: Paths | None = None) -> dict[str, Any]:
    """Compare current free providers against known manifest and report new ones.

    Returns a report dict with:
    - new_providers: list of newly discovered free provider endpoints
    - total_free: current count of free endpoints
    - previously_known: count from manifest
    - timestamp: when this report was generated
    """
    paths = paths or default_paths()
    known_path = _known_providers_path(paths)
    report_path = _report_path(paths)

    known = _load_known(known_path)
    current_rows = _current_free_providers(paths.model_providers_db_path)

    current_keys = set()
    current_by_key: dict[str, dict[str, Any]] = {}
    for row in current_rows:
        key = _make_key(row["provider_name"], row["provider_model_id"])
        current_keys.add(key)
        current_by_key[key] = row

    new_keys = current_keys - known
    removed_keys = known - current_keys

    new_providers = []
    for key in sorted(new_keys):
        row = current_by_key.get(key, {})
        new_providers.append({
            "provider_name": row.get("provider_name", ""),
            "provider_model_id": row.get("provider_model_id", ""),
            "model_id": row.get("model_id", ""),
            "context_window_tokens": row.get("context_window_tokens"),
            "data_source": row.get("data_source", ""),
            "needs_review": True,
        })

    report = {
        "generated_at": utc_now_iso(),
        "total_free_endpoints": len(current_keys),
        "previously_known": len(known),
        "new_count": len(new_providers),
        "removed_count": len(removed_keys),
        "new_providers": new_providers,
        "removed_keys": sorted(removed_keys) if removed_keys else [],
    }

    # Write the report.
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_path, report)

    # Update the known manifest (add new, keep removed for tracking).
    updated_known = {
        "last_updated": utc_now_iso(),
        "providers": sorted(current_keys),
    }
    atomic_write_json(known_path, updated_known)

    return report
