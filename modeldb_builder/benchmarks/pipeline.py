from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..cache import RawCache
from ..config import Paths, default_paths
from ..util import (
    atomic_copy,
    atomic_write_bytes,
    atomic_write_json,
    ensure_dir,
    sha256_bytes,
    utc_now_iso,
)
from .db_update import (
    ensure_model_providers_columns,
    ensure_models_unique_columns,
    load_canonical_models,
    load_model_providers_rows,
    load_models_unique_metrics,
    update_model_providers_metrics,
    update_models_unique_metrics,
)
from .export import now_metadata, write_csv, write_json
from .matching import build_canonical_index, match_benchmark_rows
from .raw_cache import (
    promote_current,
    update_manifest_file,
    validate_nonempty_bytes,
    write_current,
)
from .scoring import (
    compute_model_scores,
    compute_provider_score,
    minmax_0_100,
    minmax_invert_0_100,
)
from .sources.lmarena import fetch_lmarena_json, parse_lmarena_rows, validate_lmarena_json
from .sources.livecodebench import (
    fetch_livecodebench_json,
    parse_livecodebench_rows,
    validate_livecodebench_json,
)
from .sources.llmstats import fetch_llmstats_json, parse_llmstats_rows, validate_llmstats_json
from .sources.livebench import (
    fetch_livebench_json,
    parse_livebench_rows,
    validate_livebench_json,
)
from .sources.swebench import fetch_swebench_json, parse_swebench_rows, validate_swebench_json
from .sources.swerebench import (
    fetch_swerebench_json,
    parse_swerebench_rows,
    validate_swerebench_json,
)
from .sources.valsai import fetch_valsai_json, parse_valsai_rows, validate_valsai_json


def _leaderboards_dir(paths: Paths) -> Path:
    return paths.repo_root / "leaderboards"


def _unmatched_paths(paths: Paths) -> tuple[Path, Path, Path]:
    raw = paths.raw_dir
    current = raw / "unmatched_benchmarks_current.csv"
    last_full = raw / "unmatched_benchmarks_last_full.csv"
    canonical = raw / "unmatched_benchmarks.csv"
    return current, last_full, canonical


def _write_unmatched_csv(
    paths: Paths, unmatched_rows: list[dict[str, Any]], *, promote: bool
) -> None:
    cur, last_full, canonical = _unmatched_paths(paths)
    fieldnames = [
        "source",
        "raw_name",
        "normalized_name",
        "status",
        "suggested_model_id",
        "similarity",
        "reason",
    ]
    lines = []
    lines.append(",".join(fieldnames))
    for r in unmatched_rows:
        lines.append(",".join(_csv_cell(r.get(k)) for k in fieldnames))
    atomic_write_bytes(cur, ("\n".join(lines) + "\n").encode("utf-8"))
    if promote:
        atomic_copy(cur, last_full)
        atomic_copy(cur, canonical)


def _csv_cell(v: Any) -> str:
    if v is None:
        s = ""
    else:
        s = str(v)
    needs_quote = any(ch in s for ch in [",", '"', "\n", "\r"])
    if needs_quote:
        s = s.replace('"', '""')
        return f'"{s}"'
    return s


def _load_manual_overrides(paths: Paths) -> dict[str, dict[str, Any]]:
    p = paths.repo_root / "modeldb_builder" / "benchmarks" / "manual_overrides.json"
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text("utf-8"))
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in obj.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def _apply_manual_overrides_fill_only(
    db_path: Path,
    *,
    overrides: dict[str, dict[str, Any]],
    tracker: dict[str, list[str]] | None = None,
) -> None:
    """Apply manual overrides (fill-only: only fill NULL columns).

    *tracker*, if provided, collects ``{"applied": [...], "unmatched": [...]}``.
    Keys are normalized via ``normalize_model_slug()`` before matching, with an
    85 %-threshold fuzzy fallback when an exact match fails.
    """
    if not overrides or not db_path.exists():
        return
    import sqlite3

    from ..dedup.normalize import normalize_model_slug

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        # Build a lookup of all model_ids in the DB for fuzzy matching.
        all_db_ids: list[str] = [
            r[0] for r in con.execute("select model_id from models_unique").fetchall()
        ]
        db_id_set = set(all_db_ids)

        con.execute("begin")
        for override_key, cols in overrides.items():
            if not cols:
                continue

            # Step 1: normalize the override key.
            norm_key = normalize_model_slug(override_key)

            # Step 2: try exact match (original key, then normalized key).
            matched_id: str | None = None
            if override_key in db_id_set:
                matched_id = override_key
            elif norm_key in db_id_set:
                matched_id = norm_key

            # Step 3: fuzzy fallback at 85% threshold.
            if matched_id is None:
                try:
                    from rapidfuzz import fuzz

                    best_score, best_id = 0.0, None
                    for db_id in all_db_ids:
                        score = fuzz.ratio(norm_key, db_id)
                        if score > best_score:
                            best_score, best_id = score, db_id
                    if best_score >= 85 and best_id is not None:
                        matched_id = best_id
                except ImportError:
                    pass  # rapidfuzz not available; skip fuzzy

            if matched_id is None:
                if tracker is not None:
                    tracker.setdefault("unmatched", []).append(override_key)
                continue

            cur = con.execute(
                "select * from models_unique where model_id=?", [matched_id]
            )
            row = cur.fetchone()
            if row is None:
                if tracker is not None:
                    tracker.setdefault("unmatched", []).append(override_key)
                continue

            updates: dict[str, Any] = {}
            for k, v in cols.items():
                if k == "source":
                    continue
                if k not in row.keys():
                    continue
                if row[k] is None and v is not None:
                    updates[k] = v
            if updates:
                sets = ", ".join(f"{k}=?" for k in updates.keys())
                con.execute(
                    f"update models_unique set {sets} where model_id=?",
                    [*updates.values(), matched_id],
                )
            if tracker is not None:
                tracker.setdefault("applied", []).append(override_key)
        con.commit()
    finally:
        con.close()


def _null_out_trinity_false_positive(db_path: Path) -> None:
    if not db_path.exists():
        return
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("begin")
        con.execute(
            """
            update models_unique
            set avg_agentic_coding_score = NULL,
                avg_reasoning_chat_score = NULL,
                swe_bench_verified_pct = NULL
            where model_id like '%trinity%'
              and avg_agentic_coding_score > 50
            """
        )
        con.commit()
    finally:
        con.close()


def _propagate_canonical_scores_to_providers(
    db_path: Path,
    *,
    canonical_index: Any,
    models_after: dict[str, dict[str, Any]],
) -> int:
    if not db_path.exists():
        return 0
    import sqlite3

    from .matching import match_model_name

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    updated = 0
    try:
        cur = con.execute(
            "select model_id, provider_name, provider_model_id, avg_agentic_coding_score from model_providers"
        )
        rows = cur.fetchall()
        con.execute("begin")
        for r in rows:
            if r["avg_agentic_coding_score"] is not None:
                continue
            raw_mid = str(r["model_id"] or "").strip()
            if not raw_mid:
                continue
            m = match_model_name(
                "livebench",
                raw_mid,
                index=canonical_index,
                auto_threshold=75.0,
                review_threshold=75.0,
            )
            if m.status != "matched" or not m.model_id:
                continue
            canonical_id = m.model_id
            cm = models_after.get(canonical_id)
            if not cm:
                continue
            cols = {
                "model_id": canonical_id,
                "avg_agentic_coding_score": cm.get("avg_agentic_coding_score"),
                "avg_reasoning_chat_score": cm.get("avg_reasoning_chat_score"),
                "swe_bench_verified_pct": cm.get("swe_bench_verified_pct"),
                "livecodebench_pct": cm.get("livecodebench_pct"),
                "benchmark_coverage": cm.get("benchmark_coverage"),
            }
            sets = ", ".join(f"{k}=?" for k in cols.keys())
            con.execute(
                f"""update model_providers
                set {sets}
                where model_id=? and provider_name=? and provider_model_id=?""",
                [
                    *cols.values(),
                    r["model_id"],
                    r["provider_name"],
                    r["provider_model_id"],
                ],
            )
            updated += 1
        con.commit()
    finally:
        con.close()
    return updated


def _recompute_is_free_tier(db_path: Path) -> None:
    if not db_path.exists():
        return
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("begin")
        con.execute(
            """
            update model_providers
            set is_free_tier =
                case
                    when input_cost_per_token = 0 and output_cost_per_token = 0 then 1
                    when input_cost_per_token is not null and output_cost_per_token is not null then 0
                    else null
                end
            """
        )
        con.commit()
    finally:
        con.close()


# Providers whose free tier is effectively unusable for gateway routing.
_RATE_LIMITED_PROVIDERS: dict[str, str] = {
    "github-copilot": (
        "Requires GitHub Copilot subscription ($10-19/mo) for real use. "
        "Free plan has near-zero quota."
    ),
    "github-models": (
        "GitHub Models: 50 req/day hard cap, rate_limited for production use."
    ),
}


def _set_free_tier_quality(db_path: Path) -> None:
    """Classify free-tier rows into quality buckets.

    - 'rate_limited': known rate-limited providers (GitHub Copilot, GitHub Models).
    - 'high': all other truly-free rows (is_free_tier = 1).
    - NULL: not free tier.
    """
    if not db_path.exists():
        return
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("begin")
        # Default: all free rows get 'high'.
        con.execute(
            """
            update model_providers
            set free_tier_quality = case
                when is_free_tier = 1 then 'high'
                else null
            end
            where free_tier_quality is null or free_tier_quality != 'rate_limited'
            """
        )
        # Override specific providers.
        for prov, notes in _RATE_LIMITED_PROVIDERS.items():
            con.execute(
                """
                update model_providers
                set free_tier_quality = 'rate_limited',
                    free_tier_notes = ?
                where provider_name = ?
                """,
                [notes, prov],
            )
        con.commit()
    finally:
        con.close()


def run(paths: Paths | None = None, *, timeout_s: int = 30) -> dict[str, Any]:
    paths = paths or default_paths()
    ensure_dir(paths.raw_dir)
    ensure_dir(_leaderboards_dir(paths))

    raw_cache = RawCache(
        paths.raw_dir, paths.scrape_manifest_path, paths.scrape_state_path
    )

    # Ensure schema columns exist (ALTER TABLE is idempotent via pragma checks).
    for p in (paths.models_unique_db_path, paths.models_db_path):
        if p.exists():
            ensure_models_unique_columns(p)
    if paths.model_providers_db_path.exists():
        ensure_model_providers_columns(paths.model_providers_db_path)

    # Load canonical models from snapshot DB if present; fall back to full DB.
    models_db = (
        paths.models_unique_db_path
        if paths.models_unique_db_path.exists()
        else paths.models_db_path
    )
    canonical_models = load_canonical_models(models_db)
    canonical_index = build_canonical_index(
        [(m.model_id, m.model_name, m.developer, m.model_family) for m in canonical_models]
    )

    run_meta: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "sources": {},
        "counts": {},
    }
    unmatched: list[dict[str, Any]] = []

    # -------------------------
    # LiveBench (model-level)
    # -------------------------
    livebench_rows = []
    livebench_ok = False
    livebench_sha = None
    livebench_url = None
    livebench_err = None
    try:
        livebench_url, livebench_bytes = fetch_livebench_json(timeout_s=timeout_s)
        _, livebench_sha = write_current(
            paths.raw_dir, "livebench", "json", livebench_bytes
        )
        v = validate_livebench_json(livebench_bytes)
        if v.ok:
            livebench_rows = parse_livebench_rows(livebench_bytes)
            promote_current(paths.raw_dir, "livebench", "json")
            raw_cache.update_manifest(
                "livebench",
                ok=True,
                row_count=len(livebench_rows),
                sha256=livebench_sha,
            )
            livebench_ok = True
        else:
            livebench_err = v.error
            raw_cache.update_manifest(
                "livebench",
                ok=False,
                row_count=v.row_count,
                sha256=livebench_sha,
                error=v.error,
            )
    except Exception as e:
        livebench_err = str(e)
        raw_cache.update_manifest(
            "livebench",
            ok=False,
            row_count=0,
            sha256=livebench_sha,
            error=livebench_err,
        )
    run_meta["sources"]["livebench"] = {
        "ok": livebench_ok,
        "url": livebench_url,
        "row_count": len(livebench_rows),
        "error": livebench_err,
    }

    # -------------------------
    # SWE-bench Verified (model-level)
    # -------------------------
    swebench_rows = []
    swebench_ok = False
    swebench_sha = None
    swebench_url = None
    swebench_err = None
    try:
        swebench_url, swebench_bytes = fetch_swebench_json(timeout_s=timeout_s)
        _, swebench_sha = write_current(paths.raw_dir, "swebench", "json", swebench_bytes)
        v = validate_swebench_json(swebench_bytes)
        if v.ok:
            swebench_rows = parse_swebench_rows(swebench_bytes)
            promote_current(paths.raw_dir, "swebench", "json")
            raw_cache.update_manifest("swebench", ok=True, row_count=len(swebench_rows), sha256=swebench_sha)
            swebench_ok = True
        else:
            swebench_err = v.error
            raw_cache.update_manifest("swebench", ok=False, row_count=v.row_count, sha256=swebench_sha, error=v.error)
    except Exception as e:
        swebench_err = str(e)
        raw_cache.update_manifest("swebench", ok=False, row_count=0, sha256=swebench_sha, error=swebench_err)
    run_meta["sources"]["swebench"] = {"ok": swebench_ok, "url": swebench_url, "row_count": len(swebench_rows), "error": swebench_err}

    # -------------------------
    # SWE-rebench (model-level)
    # -------------------------
    swerebench_rows = []
    swerebench_ok = False
    swerebench_sha = None
    swerebench_url = None
    swerebench_err = None
    try:
        swerebench_url, swerebench_bytes = fetch_swerebench_json(timeout_s=timeout_s)
        _, swerebench_sha = write_current(paths.raw_dir, "swerebench", "json", swerebench_bytes)
        v = validate_swerebench_json(swerebench_bytes)
        if v.ok:
            swerebench_rows = parse_swerebench_rows(swerebench_bytes)
            promote_current(paths.raw_dir, "swerebench", "json")
            raw_cache.update_manifest("swerebench", ok=True, row_count=len(swerebench_rows), sha256=swerebench_sha)
            swerebench_ok = True
        else:
            swerebench_err = v.error
            raw_cache.update_manifest("swerebench", ok=False, row_count=v.row_count, sha256=swerebench_sha, error=v.error)
    except Exception as e:
        swerebench_err = str(e)
        raw_cache.update_manifest("swerebench", ok=False, row_count=0, sha256=swerebench_sha, error=swerebench_err)
    run_meta["sources"]["swerebench"] = {"ok": swerebench_ok, "url": swerebench_url, "row_count": len(swerebench_rows), "error": swerebench_err}

    # -------------------------
    # LiveCodeBench (model-level)
    # -------------------------
    livecodebench_rows = []
    livecodebench_ok = False
    livecodebench_sha = None
    livecodebench_url = None
    livecodebench_err = None
    try:
        livecodebench_url, livecodebench_bytes = fetch_livecodebench_json(timeout_s=timeout_s)
        _, livecodebench_sha = write_current(paths.raw_dir, "livecodebench", "json", livecodebench_bytes)
        v = validate_livecodebench_json(livecodebench_bytes)
        if v.ok:
            livecodebench_rows = parse_livecodebench_rows(livecodebench_bytes)
            promote_current(paths.raw_dir, "livecodebench", "json")
            raw_cache.update_manifest("livecodebench", ok=True, row_count=len(livecodebench_rows), sha256=livecodebench_sha)
            livecodebench_ok = True
        else:
            livecodebench_err = v.error
            raw_cache.update_manifest("livecodebench", ok=False, row_count=v.row_count, sha256=livecodebench_sha, error=v.error)
    except Exception as e:
        livecodebench_err = str(e)
        raw_cache.update_manifest("livecodebench", ok=False, row_count=0, sha256=livecodebench_sha, error=livecodebench_err)
    run_meta["sources"]["livecodebench"] = {"ok": livecodebench_ok, "url": livecodebench_url, "row_count": len(livecodebench_rows), "error": livecodebench_err}

    # -------------------------
    # LM Arena (model-level)
    # -------------------------
    arena_rows = []
    arena_ok = False
    arena_sha = None
    arena_url = None
    arena_err = None
    try:
        arena_url, arena_bytes = fetch_lmarena_json(timeout_s=timeout_s)
        _, arena_sha = write_current(paths.raw_dir, "lmarena", "json", arena_bytes)
        v = validate_lmarena_json(arena_bytes)
        if v.ok:
            arena_rows = parse_lmarena_rows(arena_bytes)
            promote_current(paths.raw_dir, "lmarena", "json")
            raw_cache.update_manifest("lmarena", ok=True, row_count=len(arena_rows), sha256=arena_sha)
            arena_ok = True
        else:
            arena_err = v.error
            raw_cache.update_manifest("lmarena", ok=False, row_count=v.row_count, sha256=arena_sha, error=v.error)
    except Exception as e:
        arena_err = str(e)
        raw_cache.update_manifest("lmarena", ok=False, row_count=0, sha256=arena_sha, error=arena_err)
    run_meta["sources"]["lmarena"] = {"ok": arena_ok, "url": arena_url, "row_count": len(arena_rows), "error": arena_err}

    # -------------------------
    # llm-stats.com (model-level)
    # -------------------------
    llmstats_rows = []
    llmstats_ok = False
    llmstats_sha = None
    llmstats_url = None
    llmstats_err = None
    try:
        llmstats_url, llmstats_bytes = fetch_llmstats_json(timeout_s=timeout_s)
        _, llmstats_sha = write_current(paths.raw_dir, "llmstats", "json", llmstats_bytes)
        v = validate_llmstats_json(llmstats_bytes)
        if v.ok:
            llmstats_rows = parse_llmstats_rows(llmstats_bytes)
            promote_current(paths.raw_dir, "llmstats", "json")
            raw_cache.update_manifest("llmstats", ok=True, row_count=len(llmstats_rows), sha256=llmstats_sha)
            llmstats_ok = True
        else:
            llmstats_err = v.error
            raw_cache.update_manifest("llmstats", ok=False, row_count=v.row_count, sha256=llmstats_sha, error=v.error)
    except Exception as e:
        llmstats_err = str(e)
        raw_cache.update_manifest("llmstats", ok=False, row_count=0, sha256=llmstats_sha, error=llmstats_err)
    run_meta["sources"]["llmstats"] = {"ok": llmstats_ok, "url": llmstats_url, "row_count": len(llmstats_rows), "error": llmstats_err}

    # -------------------------
    # vals.ai (best-effort, model-level)
    # -------------------------
    valsai_rows = []
    valsai_ok = False
    valsai_sha = None
    valsai_url = None
    valsai_err = None
    try:
        valsai_url, valsai_bytes = fetch_valsai_json(timeout_s=timeout_s)
        _, valsai_sha = write_current(paths.raw_dir, "valsai", "json", valsai_bytes)
        v = validate_valsai_json(valsai_bytes)
        if v.ok:
            valsai_rows = parse_valsai_rows(valsai_bytes)
            if valsai_rows:
                promote_current(paths.raw_dir, "valsai", "json")
                raw_cache.update_manifest("valsai", ok=True, row_count=len(valsai_rows), sha256=valsai_sha)
                valsai_ok = True
            else:
                valsai_err = "no rows parsed"
                raw_cache.update_manifest("valsai", ok=False, row_count=0, sha256=valsai_sha, error=valsai_err)
        else:
            valsai_err = v.error
            raw_cache.update_manifest("valsai", ok=False, row_count=v.row_count, sha256=valsai_sha, error=v.error)
    except Exception as e:
        valsai_err = str(e)
        raw_cache.update_manifest("valsai", ok=False, row_count=0, sha256=valsai_sha, error=valsai_err)
    run_meta["sources"]["valsai"] = {"ok": valsai_ok, "url": valsai_url, "row_count": len(valsai_rows), "error": valsai_err}

    # -------------------------
    # Match + write raw model metrics
    # -------------------------
    model_updates: dict[str, dict[str, float | int | None]] = {}
    # Precedence: vals.ai fills first, official SWE-bench overwrites when present.
    for rows in (
        valsai_rows,
        swebench_rows,
        swerebench_rows,
        livecodebench_rows,
        arena_rows,
        livebench_rows,
        llmstats_rows,
    ):
        matches = match_benchmark_rows(rows, index=canonical_index)
        for row, match in matches:
            # Only auto-apply true matches; all needs_review rows are logged for manual triage.
            if match.status != "matched" or not match.model_id:
                unmatched.append(
                    {
                        "source": row.source,
                        "raw_name": match.raw_name,
                        "normalized_name": match.normalized_name,
                        "status": match.status,
                        "suggested_model_id": match.model_id,
                        "similarity": match.score,
                        "reason": match.reason,
                    }
                )
                continue
            upd = model_updates.setdefault(match.model_id, {})
            for k, v in row.metrics.items():
                upd[k] = v

    # Write raw benchmark columns into models_unique snapshot (and full DB if present).
    for dbp in (paths.models_unique_db_path, paths.models_db_path):
        if dbp.exists():
            update_models_unique_metrics(dbp, per_model_updates=model_updates)

    # Manual overrides (last, fill-only): scraped data always takes precedence.
    manual_overrides = _load_manual_overrides(paths)
    override_tracker: dict[str, list[str]] = {"applied": [], "unmatched": []}
    if manual_overrides:
        for dbp in (paths.models_unique_db_path, paths.models_db_path):
            _apply_manual_overrides_fill_only(
                dbp, overrides=manual_overrides, tracker=override_tracker
            )

    # -------------------------
    # Compute aggregated model scores + coverage
    # -------------------------
    rows_all = load_models_unique_metrics(models_db)
    arena_overall_values = {r["model_id"]: r.get("arena_elo") for r in rows_all}
    arena_overall_norm = minmax_0_100(
        {
            k: (float(v) if v is not None else None)
            for k, v in arena_overall_values.items()
        }
    )
    arena_coding_values = {r["model_id"]: r.get("arena_elo_coding") for r in rows_all}
    arena_coding_norm = minmax_0_100(
        {
            k: (float(v) if v is not None else None)
            for k, v in arena_coding_values.items()
        }
    )

    score_updates: dict[str, dict[str, float | int | None]] = {}
    for r in rows_all:
        model_id = r["model_id"]
        scores = compute_model_scores(
            swe_bench_verified_pct=r.get("swe_bench_verified_pct"),
            livecodebench_pct=r.get("livecodebench_pct"),
            swerebench_pct=r.get("swerebench_pct"),
            livebench_reasoning=r.get("livebench_reasoning"),
            livebench_overall=r.get("livebench_overall"),
            arena_elo_overall_norm=arena_overall_norm.get(model_id),
            arena_elo_coding_norm=arena_coding_norm.get(model_id),
            arena_elo=r.get("arena_elo"),
            arena_elo_coding=r.get("arena_elo_coding"),
            livebench_coding=r.get("livebench_coding"),
            livebench_agentic_coding=r.get("livebench_agentic_coding"),
            llmstats_composite_score=r.get("llmstats_composite_score"),
            llmstats_coding_score=r.get("llmstats_coding_score"),
        )
        score_updates[model_id] = {
            "avg_agentic_coding_score": scores.avg_agentic_coding_score,
            "avg_agentic_coding_score_arena_only": scores.avg_agentic_coding_score_arena_only,
            "avg_reasoning_chat_score": scores.avg_reasoning_chat_score,
            "benchmark_coverage": scores.benchmark_coverage,
        }

    for dbp in (paths.models_unique_db_path, paths.models_db_path):
        if dbp.exists():
            update_models_unique_metrics(dbp, per_model_updates=score_updates)
            _null_out_trinity_false_positive(dbp)

    # -------------------------
    # Provider performance (Artificial Analysis, best-effort)
    # -------------------------
    provider_rows = (
        load_model_providers_rows(paths.model_providers_db_path)
        if paths.model_providers_db_path.exists()
        else []
    )
    provider_key_rows = {
        (r["model_id"], r["provider_name"], r["provider_model_id"]): r
        for r in provider_rows
    }

    # Artificial Analysis scrape (best-effort, already supported in Phase 1).
    aa_updates: list[tuple[str, str, str, dict[str, float | int | None]]] = []
    aa_ok = False
    aa_err = None
    aa_sha = None
    try:
        try:
            from ..sources.artificial_analysis import (
                fetch_artificial_analysis_html,
                parse_artificial_analysis_metrics,
                validate_artificial_analysis_raw,
            )
        except Exception as e:
            raise RuntimeError(f"artificial_analysis scraper unavailable: {e}") from e

        aa_html = fetch_artificial_analysis_html(timeout_s=timeout_s)
        atomic_write_bytes(paths.raw_dir / "artificial_analysis_current.html", aa_html)
        aa_sha = sha256_bytes(aa_html)
        v = validate_artificial_analysis_raw(aa_html)
        if v.ok:
            atomic_copy(
                paths.raw_dir / "artificial_analysis_current.html",
                paths.raw_dir / "artificial_analysis_last_full.html",
            )
            raw_cache.update_manifest(
                "artificial_analysis", ok=True, row_count=v.row_count, sha256=aa_sha
            )
            metrics = parse_artificial_analysis_metrics(aa_html)
            # Very conservative matching: only apply when provider_name matches exactly and model name fuzzy matches provider_model_id.
            from ..dedup.normalize import normalize_model_slug
            from .matching import _make_ratio

            ratio = _make_ratio()
            by_provider: dict[str, list[tuple[str, str, str]]] = {}
            for mid, pname, pmid in provider_key_rows.keys():
                by_provider.setdefault((pname or "").strip().lower(), []).append(
                    (mid, pname, pmid)
                )
            for m in metrics:
                prov = (m.provider_name or "").strip().lower()
                if not prov or prov not in by_provider:
                    continue
                cand = by_provider[prov]
                mn = normalize_model_slug(m.model_display_name or "")
                if not mn:
                    continue
                best = None
                best_s = -1.0
                for mid, pname, pmid in cand:
                    s = ratio(mn, normalize_model_slug(pmid))
                    if s > best_s:
                        best_s = s
                        best = (mid, pname, pmid)
                if best and best_s >= 85.0:
                    cols: dict[str, float | int | None] = {}
                    if m.avg_tokens_per_second is not None:
                        cols["avg_tokens_per_second"] = float(m.avg_tokens_per_second)
                    if m.avg_ttft_ms is not None:
                        cols["avg_ttft_ms"] = float(m.avg_ttft_ms)
                    if m.quality_score is not None:
                        cols["quality_score_artificial_analysis"] = float(
                            m.quality_score
                        )
                    if cols:
                        aa_updates.append((best[0], best[1], best[2], cols))
            aa_ok = True
        else:
            aa_err = v.error
            raw_cache.update_manifest(
                "artificial_analysis",
                ok=False,
                row_count=v.row_count,
                sha256=aa_sha,
                error=v.error,
            )
    except Exception as e:
        aa_err = str(e)
        raw_cache.update_manifest(
            "artificial_analysis", ok=False, row_count=0, sha256=aa_sha, error=aa_err
        )
    run_meta["sources"]["artificial_analysis"] = {
        "ok": aa_ok,
        "url": "https://artificialanalysis.ai/leaderboards/models",
        "row_count": len(aa_updates),
        "error": aa_err,
    }

    if paths.model_providers_db_path.exists():
        update_model_providers_metrics(
            paths.model_providers_db_path,
            per_provider_updates=[*aa_updates],
        )

    # -------------------------
    # Provider score computation + export
    # -------------------------
    if paths.model_providers_db_path.exists():
        providers_after = load_model_providers_rows(paths.model_providers_db_path)
    else:
        providers_after = []
    models_after = {r["model_id"]: r for r in load_models_unique_metrics(models_db)}

    # Propagate canonical benchmark scores into provider alias rows (fill-only).
    if paths.model_providers_db_path.exists():
        _propagate_canonical_scores_to_providers(
            paths.model_providers_db_path,
            canonical_index=canonical_index,
            models_after=models_after,
        )
        _recompute_is_free_tier(paths.model_providers_db_path)
        _set_free_tier_quality(paths.model_providers_db_path)
        providers_after = load_model_providers_rows(paths.model_providers_db_path)

    # Normalize TPS/TTFT across provider rows (only where present).
    tps_map = {
        (r["model_id"], r["provider_name"], r["provider_model_id"]): r.get(
            "avg_tokens_per_second"
        )
        for r in providers_after
    }
    ttft_map = {
        (r["model_id"], r["provider_name"], r["provider_model_id"]): r.get(
            "avg_ttft_ms"
        )
        for r in providers_after
    }
    tps_norm = minmax_0_100(
        {str(k): (float(v) if v is not None else None) for k, v in tps_map.items()}
    )
    ttft_inv = minmax_invert_0_100(
        {str(k): (float(v) if v is not None else None) for k, v in ttft_map.items()}
    )

    provider_score_updates = []
    gateway_rows: list[dict[str, Any]] = []
    for r in providers_after:
        key = (r["model_id"], r["provider_name"], r["provider_model_id"])
        key_s = str(key)
        model = models_after.get(r["model_id"]) or {}
        ps = compute_provider_score(
            model_agentic_coding_score=model.get("avg_agentic_coding_score"),
            tps_norm=tps_norm.get(key_s),
            ttft_inverted_norm=ttft_inv.get(key_s),
        )
        provider_score_updates.append(
            (
                r["model_id"],
                r["provider_name"],
                r["provider_model_id"],
                {"provider_score": ps},
            )
        )
        gateway_rows.append(
            {
                "model_id": r["model_id"],
                "provider_name": r["provider_name"],
                "provider_model_id": r["provider_model_id"],
                "provider_score": ps,
                "avg_tps": r.get("avg_tokens_per_second"),
                "avg_ttft_ms": r.get("avg_ttft_ms"),
                "input_cost_per_token": r.get("input_cost_per_token"),
                "output_cost_per_token": r.get("output_cost_per_token"),
                "is_free_tier": r.get("is_free_tier"),
                "free_tier_quality": r.get("free_tier_quality"),
                "avg_agentic_coding_score": model.get("avg_agentic_coding_score"),
            }
        )
    if paths.model_providers_db_path.exists():
        update_model_providers_metrics(
            paths.model_providers_db_path, per_provider_updates=provider_score_updates
        )

    # Sort + rank outputs.
    agentic_rows = []
    reasoning_rows = []
    for mid, r in models_after.items():
        agentic_rows.append(
            {
                "model_id": mid,
                "model_name": r.get("model_name"),
                "avg_agentic_coding_score": r.get("avg_agentic_coding_score"),
                "benchmark_coverage": r.get("benchmark_coverage"),
                "swe_bench_verified_pct": r.get("swe_bench_verified_pct"),
                "livecodebench_pct": r.get("livecodebench_pct"),
                "swerebench_pct": r.get("swerebench_pct"),
                "arena_elo_coding": r.get("arena_elo_coding"),
                "arena_elo": r.get("arena_elo"),
            }
        )
        reasoning_rows.append(
            {
                "model_id": mid,
                "model_name": r.get("model_name"),
                "avg_reasoning_chat_score": r.get("avg_reasoning_chat_score"),
                "benchmark_coverage": r.get("benchmark_coverage"),
                "livebench_reasoning": r.get("livebench_reasoning"),
                "livebench_overall": r.get("livebench_overall"),
                "arena_elo": r.get("arena_elo"),
            }
        )

    agentic_rows = sorted(
        agentic_rows,
        key=lambda x: (
            x["avg_agentic_coding_score"] is None,
            -(x["avg_agentic_coding_score"] or 0.0),
        ),
    )
    reasoning_rows = sorted(
        reasoning_rows,
        key=lambda x: (
            x["avg_reasoning_chat_score"] is None,
            -(x["avg_reasoning_chat_score"] or 0.0),
        ),
    )
    for i, r in enumerate(agentic_rows, 1):
        r["rank"] = i
    for i, r in enumerate(reasoning_rows, 1):
        r["rank"] = i

    gateway_rows = sorted(
        gateway_rows,
        key=lambda x: (x["provider_score"] is None, -(x["provider_score"] or 0.0)),
    )
    for i, r in enumerate(gateway_rows, 1):
        r["rank"] = i

    lb = _leaderboards_dir(paths)
    write_csv(
        lb / "agentic_coding_leaderboard.csv",
        agentic_rows,
        fieldnames=[
            "rank",
            "model_id",
            "model_name",
            "avg_agentic_coding_score",
            "benchmark_coverage",
            "swe_bench_verified_pct",
            "livecodebench_pct",
            "swerebench_pct",
            "arena_elo_coding",
            "arena_elo",
        ],
    )
    write_csv(
        lb / "reasoning_chat_leaderboard.csv",
        reasoning_rows,
        fieldnames=[
            "rank",
            "model_id",
            "model_name",
            "avg_reasoning_chat_score",
            "benchmark_coverage",
            "livebench_reasoning",
            "livebench_overall",
            "arena_elo",
        ],
    )
    _gateway_fieldnames = [
        "rank",
        "model_id",
        "provider_name",
        "provider_model_id",
        "provider_score",
        "avg_tps",
        "avg_ttft_ms",
        "input_cost_per_token",
        "output_cost_per_token",
        "is_free_tier",
        "free_tier_quality",
        "avg_agentic_coding_score",
    ]
    write_csv(
        lb / "gateway_fallback_ranking.csv",
        gateway_rows,
        fieldnames=_gateway_fieldnames,
    )

    # Generate variant gateway CSVs
    # Free only: is_free_tier == 1, split into quality blocks.
    free_rows = [dict(r) for r in gateway_rows if r.get("is_free_tier") == 1]

    # Block 1: truly free + scored (high/medium quality).
    block1 = [
        r for r in free_rows
        if r.get("avg_agentic_coding_score") is not None
        and (r.get("free_tier_quality") or "") in ("high", "medium")
    ]
    block1 = sorted(block1, key=lambda x: -(x.get("avg_agentic_coding_score") or 0.0))

    # Block 2: truly free + unscored (high/medium quality).
    block2 = [
        r for r in free_rows
        if r.get("avg_agentic_coding_score") is None
        and (r.get("free_tier_quality") or "") in ("high", "medium")
    ]
    block2 = sorted(
        block2, key=lambda x: ((x.get("provider_name") or "").strip().lower())
    )

    # Block 3: rate_limited (github-copilot, github-models, etc.).
    seen = {id(r) for r in [*block1, *block2]}
    block3 = [r for r in free_rows if id(r) not in seen]
    block3 = sorted(
        block3,
        key=lambda x: (
            x.get("avg_agentic_coding_score") is None,
            -(x.get("avg_agentic_coding_score") or 0.0),
            x.get("model_id") or "",
        ),
    )
    free_rows = [*block1, *block2, *block3]
    for i, r in enumerate(free_rows, 1):
        r["rank"] = i

    # All models: same as gateway_ranking but explicitly sorted
    all_model_rows = sorted(
        [dict(r) for r in gateway_rows],
        key=lambda x: (x["provider_score"] is None, -(x["provider_score"] or 0.0)),
    )
    for i, r in enumerate(all_model_rows, 1):
        r["rank"] = i

    # Write free only
    write_csv(
        lb / "gateway_fallback_free_only.csv",
        free_rows,
        fieldnames=_gateway_fieldnames,
    )

    # Write all models
    write_csv(
        lb / "gateway_fallback_all_models.csv",
        all_model_rows,
        fieldnames=_gateway_fieldnames,
    )

    # Metadata summary.
    coverage_counts = {"full": 0, "partial": 0, "none": 0}
    for r in models_after.values():
        c = r.get("benchmark_coverage")
        if c is None or c == 0:
            coverage_counts["none"] += 1
        elif c >= 3:
            coverage_counts["full"] += 1
        else:
            coverage_counts["partial"] += 1

    # Keep leaderboard metadata readable when a JS-rendered source fails with verbose logs.
    for s in run_meta.get("sources", {}).values():
        if not isinstance(s, dict):
            continue
        err = s.get("error")
        if isinstance(err, str) and len(err) > 800:
            s["error"] = err[:800] + " ... (truncated)"

    meta = now_metadata()
    meta.update(
        {
            "source_status": run_meta["sources"],
            "models": {
                "total": len(models_after),
                "coverage": coverage_counts,
            },
            "manual_overrides": {
                "applied": sorted(set(override_tracker.get("applied", []))),
                "unmatched": sorted(set(override_tracker.get("unmatched", []))),
            },
            "unmatched_rows": len([u for u in unmatched if u["status"] != "matched"]),
        }
    )
    write_json(lb / "leaderboard_metadata.json", meta)

    # Unmatched benchmark rows.
    _write_unmatched_csv(paths, unmatched, promote=True)

    return meta
