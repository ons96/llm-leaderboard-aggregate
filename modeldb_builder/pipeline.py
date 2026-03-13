from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .cache import RawCache
from .config import Paths
from .dedup.normalize import base_key_without_versions, normalize_model_slug
from .db.export import export_csv_snapshots, export_split_sqlite_dbs
from .db.schema import init_schema
from .db.writer import ProviderRow, UniqueModelRow, upsert_model_providers, upsert_models_unique
from .sources.artificial_analysis import (
    AA_MODELS_URL,
    fetch_artificial_analysis_html,
    parse_artificial_analysis_metrics,
    validate_artificial_analysis_raw,
)
from .sources.litellm import fetch_litellm_raw, parse_litellm_records, validate_litellm_raw
from .sources.modelsdev import fetch_modelsdev_raw, parse_modelsdev_records, validate_modelsdev_raw
from .sources.openrouter import fetch_openrouter_raw, parse_openrouter_records, validate_openrouter_raw
from .sources.types import ArtificialAnalysisMetrics, SourceProviderRecord
from .util import atomic_copy, atomic_write_bytes, atomic_write_json, ensure_dir, utc_now_iso, uniq_keep_order


def _best_model_name(candidates: list[str | None]) -> str | None:
    for c in candidates:
        if c and str(c).strip():
            return str(c).strip()
    return None


def _infer_model_family(model_id: str, model_name: str | None) -> str | None:
    s = (model_name or model_id or "").lower()
    for fam in ["gpt-4o", "gpt-4", "gpt-3.5", "claude", "gemini", "llama", "mixtral", "qwen", "deepseek"]:
        if fam in s:
            return fam
    return None


def _infer_developer(model_id: str, provider_row_dev: str | None) -> str | None:
    if provider_row_dev:
        return provider_row_dev
    # models.dev model IDs are often <org>/<model>; use org as developer hint.
    if "/" in model_id:
        org = model_id.split("/", 1)[0]
        if org and len(org) <= 40:
            return org
    return None


def _source_key_to_cache_name(source_key: str) -> str:
    return source_key


def _dedup_confidence_for_group(raw_ids: list[str], normalized_slug: str) -> tuple[str, str | None]:
    # High: only differences are provider prefix or trivial separators/case.
    # Medium: includes 'latest' alongside a concrete id.
    # Low: multiple distinct ids that map together but have different base keys.
    raw_norms = {normalize_model_slug(x) for x in raw_ids}
    if len(raw_norms) == 1:
        if any("latest" in (x or "").lower() for x in raw_ids):
            return ("medium", "includes a 'latest' alias; mapping may change over time")
        return ("high", None)
    # If multiple different normalized slugs are being forced together (shouldn't happen here), mark low.
    return ("low", "multiple distinct raw ids collapsed; verify manually")


def run_full_update(paths: Paths) -> dict[str, Any]:
    ensure_dir(paths.raw_dir)
    ensure_dir(paths.db_dir)
    cache = RawCache(paths.raw_dir, paths.scrape_manifest_path, paths.scrape_state_path)

    # ---- Fetch + cache raw sources (Tier-0) ----
    source_records: list[SourceProviderRecord] = []

    # LiteLLM
    try:
        litellm_bytes = fetch_litellm_raw()
        litellm_sha = cache.write_current_json_bytes("litellm", litellm_bytes)
        litellm_val = validate_litellm_raw(litellm_bytes)
        cache.update_manifest(
            "litellm",
            ok=litellm_val.ok,
            row_count=litellm_val.row_count,
            sha256=litellm_sha,
            error=litellm_val.error,
        )
        if litellm_val.ok:
            cache.promote_current_to_last_full("litellm")
            source_records.extend(parse_litellm_records(cache.load_last_full_json("litellm") or {}))
        else:
            fallback = cache.load_last_full_json("litellm")
            if fallback is not None:
                source_records.extend(parse_litellm_records(fallback))
    except Exception as e:
        cache.update_manifest("litellm", ok=False, row_count=0, sha256=None, error=str(e))
        fallback = cache.load_last_full_json("litellm")
        if fallback is not None:
            source_records.extend(parse_litellm_records(fallback))

    # OpenRouter
    try:
        openrouter_bytes = fetch_openrouter_raw()
        openrouter_sha = cache.write_current_json_bytes("openrouter", openrouter_bytes)
        openrouter_val = validate_openrouter_raw(openrouter_bytes)
        cache.update_manifest(
            "openrouter",
            ok=openrouter_val.ok,
            row_count=openrouter_val.row_count,
            sha256=openrouter_sha,
            error=openrouter_val.error,
        )
        if openrouter_val.ok:
            cache.promote_current_to_last_full("openrouter")
            source_records.extend(parse_openrouter_records(cache.load_last_full_json("openrouter") or {}))
        else:
            fallback = cache.load_last_full_json("openrouter")
            if fallback is not None:
                source_records.extend(parse_openrouter_records(fallback))
    except Exception as e:
        cache.update_manifest("openrouter", ok=False, row_count=0, sha256=None, error=str(e))
        fallback = cache.load_last_full_json("openrouter")
        if fallback is not None:
            source_records.extend(parse_openrouter_records(fallback))

    # models.dev
    try:
        modelsdev_bytes = fetch_modelsdev_raw()
        modelsdev_sha = cache.write_current_json_bytes("modelsdev", modelsdev_bytes)
        modelsdev_val = validate_modelsdev_raw(modelsdev_bytes)
        cache.update_manifest(
            "modelsdev",
            ok=modelsdev_val.ok,
            row_count=modelsdev_val.row_count,
            sha256=modelsdev_sha,
            error=modelsdev_val.error,
        )
        if modelsdev_val.ok:
            cache.promote_current_to_last_full("modelsdev")
            source_records.extend(parse_modelsdev_records(cache.load_last_full_json("modelsdev") or {}))
        else:
            fallback = cache.load_last_full_json("modelsdev")
            if fallback is not None:
                source_records.extend(parse_modelsdev_records(fallback))
    except Exception as e:
        cache.update_manifest("modelsdev", ok=False, row_count=0, sha256=None, error=str(e))
        fallback = cache.load_last_full_json("modelsdev")
        if fallback is not None:
            source_records.extend(parse_modelsdev_records(fallback))

    # Artificial Analysis (optional / best-effort)
    aa_metrics: list[ArtificialAnalysisMetrics] = []
    try:
        cache_state = cache.load_state().get("sources", {}).get("artificial_analysis", {})
        completed = set(cache_state.get("completed_urls", []))
        # Single URL today, but keep the structure for future pagination.
        if AA_MODELS_URL not in completed:
            html = fetch_artificial_analysis_html()
            # Cache as bytes (still named *.json for simplicity in our RawCache); we store base64-ish? No: store utf-8.
            # Use a separate extension to avoid confusing json validators.
            aa_current = paths.raw_dir / "artificial_analysis_current.html"
            atomic_write_bytes(aa_current, html)
            aa_val = validate_artificial_analysis_raw(html)
            cache.update_manifest("artificial_analysis", ok=aa_val.ok, row_count=aa_val.row_count, sha256=None, error=aa_val.error)
            if aa_val.ok:
                aa_last = paths.raw_dir / "artificial_analysis_last_full.html"
                atomic_copy(aa_current, aa_last)
                aa_metrics = parse_artificial_analysis_metrics(html)
                completed.add(AA_MODELS_URL)
                cache.update_state("artificial_analysis", {"completed_urls": sorted(completed), "last_ok_at": utc_now_iso()})
        else:
            aa_last = paths.raw_dir / "artificial_analysis_last_full.html"
            if aa_last.exists():
                aa_metrics = parse_artificial_analysis_metrics(aa_last.read_bytes())
    except Exception as e:
        cache.update_manifest("artificial_analysis", ok=False, row_count=0, sha256=None, error=str(e))
        # Continue without AA.

    # ---- Build canonical model_id mapping ----
    # Candidate raw IDs for unique models come from provider_model_id in all sources.
    by_model_id: dict[str, list[SourceProviderRecord]] = defaultdict(list)
    for r in source_records:
        slug = normalize_model_slug(r.provider_model_id)
        if not slug:
            continue
        by_model_id[slug].append(r)

    # Ambiguity detection: if different normalized slugs share the same base key, it might be a family with versions.
    base_groups: dict[str, set[str]] = defaultdict(set)
    for slug in by_model_id.keys():
        base_groups[base_key_without_versions(slug)].add(slug)

    unique_rows: list[UniqueModelRow] = []
    provider_map: dict[tuple[str, str], dict[str, Any]] = {}
    now = utc_now_iso()

    for slug, recs in sorted(by_model_id.items(), key=lambda kv: kv[0]):
        sources = uniq_keep_order([r.source for r in recs])
        sources_found_in = ",".join(sources)

        # Pick best metadata fields across sources.
        model_name = _best_model_name([r.model_display_name for r in recs] + [slug])
        context_window = next((r.context_window_tokens for r in recs if r.context_window_tokens), None)
        mode = next((r.mode for r in recs if r.mode), None)
        release_date = next((r.release_date for r in recs if r.release_date), None)
        developer_hint = next((r.developer for r in recs if r.developer), None)
        developer = _infer_developer(next((r.provider_model_id for r in recs), slug), developer_hint)
        model_family = _infer_model_family(slug, model_name)

        raw_ids = [r.provider_model_id for r in recs]
        conf, note = _dedup_confidence_for_group(raw_ids, slug)

        base_key = base_key_without_versions(slug)
        if len(base_groups.get(base_key, set())) > 1 and conf == "high":
            # There's more than one versioned model in the same base family; keep high but annotate.
            note = (note + "; " if note else "") + "multiple versioned variants exist for this family"

        unique_rows.append(
            UniqueModelRow(
                model_id=slug,
                model_name=model_name,
                model_family=model_family,
                developer=developer,
                release_date=release_date,
                context_window_tokens=context_window,
                mode=mode,
                sources_found_in=sources_found_in,
                canonical_model_id_litellm=next((r.provider_model_id for r in recs if r.source == "litellm"), None),
                canonical_model_id_openrouter=next((r.provider_model_id for r in recs if r.source == "openrouter"), None),
                canonical_model_id_modelsdev=next((r.provider_model_id for r in recs if r.source == "modelsdev"), None),
                dedup_confidence=conf,
                dedup_notes=note,
            )
        )

        for r in recs:
            prov_name = (r.provider_name or "unknown").lower()
            prov_id = str(r.provider_model_id)
            key = (prov_name, prov_id)

            cur = provider_map.get(key)
            if cur is None:
                provider_map[key] = {
                    "model_id": slug,
                    "provider_name": prov_name,
                    "provider_model_id": prov_id,
                    "input_cost_per_token": r.input_cost_per_token,
                    "output_cost_per_token": r.output_cost_per_token,
                    "is_free_tier": r.is_free_tier,
                    "context_window_tokens": r.context_window_tokens,
                    "mode": r.mode,
                    "avg_tokens_per_second": None,
                    "avg_ttft_ms": None,
                    "quality_score_artificial_analysis": None,
                    "data_source": r.source,
                    "last_updated": now,
                }
            else:
                # Merge: prefer existing non-null; fill gaps from this record.
                cur["model_id"] = cur.get("model_id") or slug
                if cur.get("input_cost_per_token") is None and r.input_cost_per_token is not None:
                    cur["input_cost_per_token"] = r.input_cost_per_token
                if cur.get("output_cost_per_token") is None and r.output_cost_per_token is not None:
                    cur["output_cost_per_token"] = r.output_cost_per_token
                if cur.get("context_window_tokens") is None and r.context_window_tokens is not None:
                    cur["context_window_tokens"] = r.context_window_tokens
                if cur.get("mode") is None and r.mode is not None:
                    cur["mode"] = r.mode

                # is_free_tier: any 1 wins.
                if r.is_free_tier == 1:
                    cur["is_free_tier"] = 1

                # data_source: union.
                existing_sources = set(str(cur.get("data_source") or "").split(","))
                existing_sources.discard("")
                existing_sources.add(r.source)
                cur["data_source"] = ",".join(sorted(existing_sources))
                cur["last_updated"] = now

    # Apply Artificial Analysis metrics by enriching existing provider rows (best-effort match by model_display_name).
    # We avoid aggressive fuzzy matching here to prevent corrupt joins.
    if aa_metrics:
        name_to_slug: dict[str, str] = {}
        for slug, recs in by_model_id.items():
            nm = _best_model_name([r.model_display_name for r in recs] + [None])
            if nm:
                name_to_slug[nm.strip().lower()] = slug
        for m in aa_metrics:
            if not m.model_display_name:
                continue
            slug = name_to_slug.get(m.model_display_name.strip().lower())
            if not slug:
                continue
            prov = (m.provider_name or "").strip().lower()
            if not prov:
                continue
            # Enrich all existing rows for this (model_id, provider_name).
            for (pname, pmid), row in list(provider_map.items()):
                if pname != prov:
                    continue
                if row.get("model_id") != slug:
                    continue
                if row.get("avg_tokens_per_second") is None and m.avg_tokens_per_second is not None:
                    row["avg_tokens_per_second"] = m.avg_tokens_per_second
                if row.get("avg_ttft_ms") is None and m.avg_ttft_ms is not None:
                    row["avg_ttft_ms"] = m.avg_ttft_ms
                if row.get("quality_score_artificial_analysis") is None and m.quality_score is not None:
                    row["quality_score_artificial_analysis"] = m.quality_score
                # data_source: union.
                existing_sources = set(str(row.get("data_source") or "").split(","))
                existing_sources.discard("")
                existing_sources.add("artificial_analysis")
                row["data_source"] = ",".join(sorted(existing_sources))
                row["last_updated"] = now

    provider_rows = [ProviderRow(**row) for row in provider_map.values()]

    # ---- Write DB atomically ----
    if paths.models_db_tmp_path.exists():
        paths.models_db_tmp_path.unlink()
    with sqlite3.connect(paths.models_db_tmp_path) as conn:
        init_schema(conn)
        with conn:  # transaction
            upsert_models_unique(conn, unique_rows)
            upsert_model_providers(conn, provider_rows)

    # Replace the live db only after successful build.
    paths.models_db_path.parent.mkdir(parents=True, exist_ok=True)
    paths.models_db_tmp_path.replace(paths.models_db_path)

    # ---- Export CSV ----
    export_csv_snapshots(paths.models_db_path, paths.models_unique_csv_path, paths.model_providers_csv_path)
    export_split_sqlite_dbs(paths.models_db_path, paths.models_unique_db_path, paths.model_providers_db_path)

    summary = {
        "updated_at": now,
        "unique_models": len(unique_rows),
        "provider_rows": len(provider_rows),
        "sources": {
            "litellm_records": sum(1 for r in source_records if r.source == "litellm"),
            "openrouter_records": sum(1 for r in source_records if r.source == "openrouter"),
            "modelsdev_records": sum(1 for r in source_records if r.source == "modelsdev"),
            "artificial_analysis_metrics": len(aa_metrics),
        },
        "paths": {
            "db": str(paths.models_db_path),
            "models_unique_db": str(paths.models_unique_db_path),
            "model_providers_db": str(paths.model_providers_db_path),
            "models_unique_csv": str(paths.models_unique_csv_path),
            "model_providers_csv": str(paths.model_providers_csv_path),
        },
    }
    atomic_write_json(paths.db_dir / "last_run_summary.json", summary)

    # ---- Free provider discovery diff ----
    try:
        from .discovery import run_discovery_diff

        discovery_report = run_discovery_diff(paths)
        summary["discovery"] = {
            "new_free_providers": discovery_report.get("new_count", 0),
            "total_free_endpoints": discovery_report.get("total_free_endpoints", 0),
        }
    except Exception:
        pass  # Discovery diff is best-effort; don't block Phase 1.

    return summary
