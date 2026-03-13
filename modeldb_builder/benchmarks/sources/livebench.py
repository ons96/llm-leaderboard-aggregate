from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

import pandas as pd
import requests

from ...util import ValidationResult, coerce_float
from ..http import http_get
from ..types import ModelBenchmarkRow


HF_DATASET_URL = "https://huggingface.co/datasets/livebench/model_judgment/resolve/main/data/leaderboard-00000-of-00001.parquet"
GITHUB_LEADERBOARD_URL = "https://raw.githubusercontent.com/LiveBench/LiveBench/main/livebench/data/leaderboard.json"
LIVEBENCH_SITE_LEADERBOARD_URL = "https://livebench.ai/leaderboard.json"


def _candidate_release_urls(today: _dt.date) -> list[str]:
    # LiveBench publishes monthly releases. We try a small set of plausible filenames.
    urls: list[str] = []
    # Known/previously documented locations (may 404 depending on repo refactors).
    urls.extend(
        [
            LIVEBENCH_SITE_LEADERBOARD_URL,
            GITHUB_LEADERBOARD_URL,
            "https://raw.githubusercontent.com/LiveBench/LiveBench/main/livebench/data/results/latest.json",
            "https://raw.githubusercontent.com/LiveBench/LiveBench/main/livebench/data/results/latest_full.json",
            "https://raw.githubusercontent.com/LiveBench/LiveBench/main/livebench/data/results/latest_results.json",
        ]
    )

    # Probe recent months (YYYYMMDD and YYYYMM patterns).
    # Keep the probe small to avoid long runs when URLs are missing.
    for months_back in range(0, 8):
        y = today.year
        m = today.month - months_back
        while m <= 0:
            y -= 1
            m += 12
        # Use day 01 (common for monthly releases).
        for d in (1,):
            try:
                dt = _dt.date(y, m, d)
            except ValueError:
                continue
            ymd = dt.strftime("%Y%m%d")
            ym = dt.strftime("%Y%m")
            urls.append(f"https://livebench.ai/livebench_release_{ymd}.json")
            urls.append(f"https://livebench.ai/livebench_release_{ym}.json")
    # Dedup while keeping order.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _fetch_from_huggingface(timeout_s: int = 60) -> bytes:
    """Fetch LiveBench data from HuggingFace dataset (primary source)."""
    from io import BytesIO

    resp = requests.get(HF_DATASET_URL, timeout=timeout_s)
    resp.raise_for_status()
    df = pd.read_parquet(BytesIO(resp.content))

    # Aggregate by model and category - compute mean score (pass rate)
    agg = df.groupby(["model", "category"])["score"].mean().reset_index()

    # Pivot to get columns for each category
    coding_scores = agg[agg["category"] == "coding"][["model", "score"]].rename(
        columns={"score": "livebench_coding"}
    )
    reasoning_scores = agg[agg["category"] == "instruction_following"][
        ["model", "score"]
    ].rename(columns={"score": "livebench_reasoning"})
    language_scores = agg[agg["category"] == "language"][["model", "score"]].rename(
        columns={"score": "livebench_overall"}
    )

    # Merge
    result = coding_scores.merge(reasoning_scores, on="model", how="outer")
    result = result.merge(language_scores, on="model", how="outer")

    # Convert to JSON lines format for compatibility with existing parser
    rows = result.to_dict(orient="records")
    return json.dumps(rows).encode("utf-8")


def fetch_livebench_json(timeout_s: int = 30) -> tuple[str | None, bytes]:
    # Prefer the actively maintained JSON leaderboard (includes sortable categories like agentic coding).
    try:
        for url in (LIVEBENCH_SITE_LEADERBOARD_URL, GITHUB_LEADERBOARD_URL):
            res = http_get(url, timeout_s=min(timeout_s, 20), retries=0)
            if res.body and res.body[:1] in (b"{", b"["):
                return url, res.body
    except Exception:
        pass

    # Then try HuggingFace dataset (category aggregates).
    try:
        data = _fetch_from_huggingface(timeout_s=timeout_s)
        return HF_DATASET_URL, data
    except Exception:
        pass  # Fall back to legacy URLs

    # Fall back to legacy URLs
    today = _dt.date.today()
    last_err: Exception | None = None
    per_try_timeout = min(8, max(3, int(timeout_s / 4)))
    for url in _candidate_release_urls(today):
        try:
            res = http_get(url, timeout_s=per_try_timeout, retries=0)
            # Quick sanity: ensure it looks like JSON.
            if not res.body or res.body[:1] not in (b"{", b"["):
                continue
            return url, res.body
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("no candidate LiveBench URLs tried")


def validate_livebench_json(data: bytes) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")
    # Expect a list or dict with non-trivial size.
    if isinstance(obj, list) and len(obj) >= 10:
        return ValidationResult(ok=True, row_count=len(obj))
    if isinstance(obj, dict) and len(obj) >= 10:
        return ValidationResult(ok=True, row_count=len(obj))
    return ValidationResult(
        ok=False, row_count=0, error=f"unexpected json shape: {type(obj).__name__}"
    )


def _as_rows(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        # common containers
        for k in ("results", "data", "leaderboard", "rows"):
            v = obj.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        # fallback: if values are dicts, treat as dict-of-rows
        if all(isinstance(v, dict) for v in obj.values()):
            return list(obj.values())
    return []


def _pick_metric(d: dict[str, Any], *needles: str) -> float | None:
    for k, v in d.items():
        ks = str(k).lower().replace(" ", "_")
        if all(n in ks for n in needles):
            fv = coerce_float(v)
            if fv is None:
                continue
            # Scale 0-1 to 0-100 if needed.
            if 0.0 <= fv <= 1.5:
                return fv * 100.0
            return fv
    return None


def parse_livebench_rows(json_bytes: bytes) -> list[ModelBenchmarkRow]:
    obj = json.loads(json_bytes.decode("utf-8"))
    rows = _as_rows(obj)
    out: list[ModelBenchmarkRow] = []
    for r in rows:
        model = (
            r.get("model")
            or r.get("model_name")
            or r.get("name")
            or r.get("Model")
            or r.get("Model Name")
            or ""
        ).strip()
        if not model:
            continue
        coding = _pick_metric(r, "coding")
        agentic_coding = _pick_metric(r, "agentic", "coding") or _pick_metric(
            r, "agentic_coding"
        )
        reasoning = _pick_metric(r, "reasoning")
        overall = _pick_metric(r, "overall") or _pick_metric(r, "score")
        math = _pick_metric(r, "math")

        metrics: dict[str, float] = {}
        if coding is not None:
            metrics["livebench_coding"] = float(coding)
        if agentic_coding is not None:
            metrics["livebench_agentic_coding"] = float(agentic_coding)
        if reasoning is not None:
            metrics["livebench_reasoning"] = float(reasoning)
        if overall is not None:
            metrics["livebench_overall"] = float(overall)
        if math is not None:
            metrics["livebench_math"] = float(math)
        if not metrics:
            continue
        out.append(
            ModelBenchmarkRow(source="livebench", model_name_raw=model, metrics=metrics)
        )
    return out
