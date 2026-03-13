"""Generate per-virtual-model YAML fallback lists from leaderboard data.

Reads from the model_providers DB and leaderboard CSVs to produce ranked
fallback lists for each virtual model type, in a format directly consumable
by the LLM-API-Key-Proxy gateway.

Virtual model routing criteria:
  - coding-elite:  best agentic coding score, no speed filter
  - coding-smart:  agentic coding weighted with TPS, min coding score floor
  - coding-fast:   fastest by TPS with a loose min coding score floor
  - chat-elite:    best reasoning/chat score, no speed filter
  - chat-smart:    reasoning/chat score weighted with TPS, min chat score floor
  - chat-fast:     fastest by TPS with a loose min chat score floor
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import Paths, default_paths
from .util import atomic_write_bytes, ensure_dir, utc_now_iso


# Configurable thresholds (can be overridden via a config file in the future).
MIN_CODING_SCORE_SMART = 20.0  # Minimum agentic coding score for coding-smart
MIN_CODING_SCORE_FAST = 10.0  # Loose floor for coding-fast
MIN_CHAT_SCORE_SMART = 20.0  # Minimum reasoning/chat score for chat-smart
MIN_CHAT_SCORE_FAST = 10.0  # Loose floor for chat-fast
SPEED_WEIGHT_SMART = 0.30  # Weight for TPS in "smart" composite
SCORE_WEIGHT_SMART = 0.70  # Weight for score in "smart" composite
MAX_FALLBACK_ENTRIES = 30  # Max entries per virtual model fallback list


def _load_free_provider_data(db_path: Path) -> list[dict[str, Any]]:
    """Load free-tier provider rows with scores and TPS."""
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT model_id, provider_name, provider_model_id,
                   avg_agentic_coding_score, avg_reasoning_chat_score,
                   avg_tokens_per_second, avg_ttft_ms,
                   is_free_tier, free_tier_quality, provider_score,
                   context_window_tokens
            FROM model_providers
            WHERE is_free_tier = 1
              AND free_tier_quality IN ('high', 'medium')
            ORDER BY provider_score DESC NULLS LAST
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _normalize_0_1(values: list[float | None]) -> dict[int, float]:
    """Min-max normalize non-None values to [0, 1]."""
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(present) < 2:
        return {i: 0.5 for i, _ in present}
    vals = [v for _, v in present]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return {i: 0.5 for i, _ in present}
    return {i: (v - lo) / (hi - lo) for i, v in present}


def _rank_coding_elite(rows: list[dict]) -> list[dict]:
    """Best agentic coding score, no speed filter. Free-tier only."""
    scored = [r for r in rows if r.get("avg_agentic_coding_score") is not None]
    return sorted(scored, key=lambda r: -(r["avg_agentic_coding_score"] or 0))


def _rank_coding_smart(rows: list[dict]) -> list[dict]:
    """Agentic coding score weighted with TPS, min score floor."""
    candidates = [
        r for r in rows
        if (r.get("avg_agentic_coding_score") or 0) >= MIN_CODING_SCORE_SMART
    ]
    if not candidates:
        return _rank_coding_elite(rows)  # fallback

    scores = [r.get("avg_agentic_coding_score") for r in candidates]
    tps_vals = [r.get("avg_tokens_per_second") for r in candidates]
    score_norm = _normalize_0_1(scores)
    tps_norm = _normalize_0_1(tps_vals)

    for i, r in enumerate(candidates):
        s = score_norm.get(i, 0.0)
        t = tps_norm.get(i, 0.0)  # 0 if no TPS data
        r["_composite"] = SCORE_WEIGHT_SMART * s + SPEED_WEIGHT_SMART * t

    return sorted(candidates, key=lambda r: -r.get("_composite", 0))


def _rank_coding_fast(rows: list[dict]) -> list[dict]:
    """Fastest by TPS with a loose min coding score floor."""
    candidates = [
        r for r in rows
        if (r.get("avg_agentic_coding_score") or 0) >= MIN_CODING_SCORE_FAST
        and r.get("avg_tokens_per_second") is not None
    ]
    if not candidates:
        # If no TPS data available, fall back to score ranking
        return _rank_coding_elite(rows)
    return sorted(candidates, key=lambda r: -(r.get("avg_tokens_per_second") or 0))


def _rank_chat_elite(rows: list[dict]) -> list[dict]:
    """Best reasoning/chat score, no speed filter."""
    scored = [r for r in rows if r.get("avg_reasoning_chat_score") is not None]
    return sorted(scored, key=lambda r: -(r["avg_reasoning_chat_score"] or 0))


def _rank_chat_smart(rows: list[dict]) -> list[dict]:
    """Reasoning/chat score weighted with TPS, min score floor."""
    candidates = [
        r for r in rows
        if (r.get("avg_reasoning_chat_score") or 0) >= MIN_CHAT_SCORE_SMART
    ]
    if not candidates:
        return _rank_chat_elite(rows)

    scores = [r.get("avg_reasoning_chat_score") for r in candidates]
    tps_vals = [r.get("avg_tokens_per_second") for r in candidates]
    score_norm = _normalize_0_1(scores)
    tps_norm = _normalize_0_1(tps_vals)

    for i, r in enumerate(candidates):
        s = score_norm.get(i, 0.0)
        t = tps_norm.get(i, 0.0)
        r["_composite"] = SCORE_WEIGHT_SMART * s + SPEED_WEIGHT_SMART * t

    return sorted(candidates, key=lambda r: -r.get("_composite", 0))


def _rank_chat_fast(rows: list[dict]) -> list[dict]:
    """Fastest by TPS with a loose min chat score floor."""
    candidates = [
        r for r in rows
        if (r.get("avg_reasoning_chat_score") or 0) >= MIN_CHAT_SCORE_FAST
        and r.get("avg_tokens_per_second") is not None
    ]
    if not candidates:
        return _rank_chat_elite(rows)
    return sorted(candidates, key=lambda r: -(r.get("avg_tokens_per_second") or 0))


VIRTUAL_MODELS = {
    "coding-elite": {
        "description": "Best agentic coding models (benchmark-based)",
        "ranker": _rank_coding_elite,
        "timeout_ms": 180000,
    },
    "coding-smart": {
        "description": "High-quality coding with balanced performance",
        "ranker": _rank_coding_smart,
        "timeout_ms": 120000,
    },
    "coding-fast": {
        "description": "Fastest coding models",
        "ranker": _rank_coding_fast,
        "timeout_ms": 30000,
    },
    "chat-elite": {
        "description": "Most intelligent models",
        "ranker": _rank_chat_elite,
        "timeout_ms": 300000,
    },
    "chat-smart": {
        "description": "Best intelligence-to-speed ratio",
        "ranker": _rank_chat_smart,
        "timeout_ms": 120000,
    },
    "chat-fast": {
        "description": "Fastest chat models",
        "ranker": _rank_chat_fast,
        "timeout_ms": 15000,
    },
}


def _dedup_by_model(ranked: list[dict], max_entries: int) -> list[dict]:
    """Keep only the best provider per model_id, limit total entries."""
    seen_models: set[str] = set()
    result = []
    for r in ranked:
        mid = r.get("model_id", "")
        if mid in seen_models:
            continue
        seen_models.add(mid)
        result.append(r)
        if len(result) >= max_entries:
            break
    return result


def _to_yaml_lines(virtual_name: str, config: dict, ranked: list[dict]) -> list[str]:
    """Generate YAML lines for a single virtual model."""
    lines = []
    lines.append(f"  {virtual_name}:")
    lines.append(f'    description: "{config["description"]}"')
    lines.append("    fallback_chain:")
    for i, r in enumerate(ranked, 1):
        lines.append(f"      - provider: {r['provider_name']}")
        lines.append(f"        model: {r['provider_model_id']}")
        lines.append(f"        priority: {i}")
        score_key = "avg_agentic_coding_score" if "coding" in virtual_name else "avg_reasoning_chat_score"
        score = r.get(score_key)
        tps = r.get("avg_tokens_per_second")
        comment_parts = []
        if score is not None:
            comment_parts.append(f"score={score:.1f}")
        if tps is not None:
            comment_parts.append(f"tps={tps:.0f}")
        if comment_parts:
            lines[-1] += f"  # {', '.join(comment_parts)}"
    lines.append("    settings:")
    lines.append(f"      timeout_ms: {config['timeout_ms']}")
    lines.append("      retry_on_rate_limit: true")
    return lines


def generate_virtual_model_yaml(paths: Paths | None = None) -> dict[str, Any]:
    """Generate YAML fallback files for all virtual models.

    Writes:
      - leaderboards/virtual_models_generated.yaml  (combined, gateway-ready)
      - leaderboards/fallback_{name}.csv             (per-virtual-model CSV)

    Returns metadata about what was generated.
    """
    paths = paths or default_paths()
    lb_dir = paths.repo_root / "leaderboards"
    ensure_dir(lb_dir)

    rows = _load_free_provider_data(paths.model_providers_db_path)
    if not rows:
        return {"generated": False, "reason": "no free provider data available"}

    yaml_lines = [
        f"# Auto-generated virtual model fallback lists",
        f"# Generated: {utc_now_iso()}",
        f"# Source: llm-leaderboard-aggregate benchmark pipeline",
        f"# Do not edit manually - regenerated on each pipeline run.",
        "",
        "virtual_models:",
    ]

    meta: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "virtual_models": {},
        "total_free_endpoints": len(rows),
    }

    for name, config in VIRTUAL_MODELS.items():
        ranker = config["ranker"]
        ranked = ranker(rows)
        deduped = _dedup_by_model(ranked, MAX_FALLBACK_ENTRIES)

        yaml_lines.append("")
        yaml_lines.extend(_to_yaml_lines(name, config, deduped))

        # Also write per-virtual-model CSV.
        score_key = "avg_agentic_coding_score" if "coding" in name else "avg_reasoning_chat_score"
        csv_header = "rank,provider_name,provider_model_id,model_id,score,avg_tps"
        csv_rows = [csv_header]
        for i, r in enumerate(deduped, 1):
            score = r.get(score_key)
            tps = r.get("avg_tokens_per_second")
            csv_rows.append(
                f"{i},{r['provider_name']},{r['provider_model_id']},"
                f"{r['model_id']},{score if score is not None else ''},"
                f"{tps if tps is not None else ''}"
            )
        csv_path = lb_dir / f"fallback_{name.replace('-', '_')}.csv"
        atomic_write_bytes(csv_path, ("\n".join(csv_rows) + "\n").encode("utf-8"))

        meta["virtual_models"][name] = {
            "entries": len(deduped),
            "top_model": deduped[0]["model_id"] if deduped else None,
            "top_provider": deduped[0]["provider_name"] if deduped else None,
        }

    yaml_lines.append("")
    yaml_content = "\n".join(yaml_lines) + "\n"
    yaml_path = lb_dir / "virtual_models_generated.yaml"
    atomic_write_bytes(yaml_path, yaml_content.encode("utf-8"))

    meta["generated"] = True
    return meta
