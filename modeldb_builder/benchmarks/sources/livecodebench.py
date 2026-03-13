from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ...util import ValidationResult, coerce_float
from ..http import http_get
from ..types import ModelBenchmarkRow


LIVECODEBENCH_URL = "https://pricepertoken.com/leaderboards/benchmark/livecodebench"


def _parse_pct(s: str) -> float | None:
    if not s:
        return None
    s2 = s.strip().replace(",", "")
    m = re.search(r"(\d+(\.\d+)?)", s2)
    if not m:
        return None
    v = coerce_float(m.group(1))
    if v is None:
        return None
    if 0.0 <= v <= 1.0:
        return float(v) * 100.0
    return float(v)


def _rows_from_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    best: list[dict[str, Any]] = []
    for t in tables:
        trs = t.find_all("tr")
        if len(trs) < 5:
            continue
        headers = [th.get_text(" ", strip=True) for th in trs[0].find_all(["th", "td"])]
        hnorm = [h.lower() for h in headers]

        model_i = None
        score_i = None
        for i, h in enumerate(hnorm):
            if model_i is None and ("model" in h or "name" in h):
                model_i = i
            if score_i is None and (
                "livecodebench" in h
                or ("score" in h and "live" in " ".join(hnorm))
                or ("pass" in h and ("@" in h or "rate" in h or "%" in h))
                or ("pct" in h or "%" in h)
            ):
                score_i = i
        if model_i is None:
            continue
        if score_i is None:
            # Fallback: pick the last numeric-ish column.
            score_i = len(headers) - 1

        rows: list[dict[str, Any]] = []
        for tr in trs[1:]:
            tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if not tds or model_i >= len(tds) or score_i >= len(tds):
                continue
            model = (tds[model_i] or "").strip()
            pct = _parse_pct(tds[score_i] or "")
            if not model or pct is None:
                continue
            rows.append({"model": model, "livecodebench_pct": float(pct)})
        if len(rows) > len(best):
            best = rows
    return best


def fetch_livecodebench_json(timeout_s: int = 30) -> tuple[str | None, bytes]:
    res = http_get(LIVECODEBENCH_URL, timeout_s=timeout_s, retries=1)
    rows = _rows_from_html(res.body.decode("utf-8", "replace"))
    if not rows:
        raise RuntimeError("no LiveCodeBench rows parsed from HTML")
    return res.url, json.dumps(rows, sort_keys=True).encode("utf-8")


def validate_livecodebench_json(data: bytes) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")
    if not isinstance(obj, list) or len(obj) < 25:
        return ValidationResult(ok=False, row_count=0, error="unexpected json shape/too few rows")
    return ValidationResult(ok=True, row_count=len(obj))


def parse_livecodebench_rows(json_bytes: bytes) -> list[ModelBenchmarkRow]:
    obj = json.loads(json_bytes.decode("utf-8"))
    if not isinstance(obj, list):
        return []
    out: list[ModelBenchmarkRow] = []
    for r in obj:
        if not isinstance(r, dict):
            continue
        model = (r.get("model") or r.get("model_name") or r.get("name") or "").strip()
        pct = coerce_float(r.get("livecodebench_pct"))
        if not model or pct is None:
            continue
        out.append(
            ModelBenchmarkRow(
                source="livecodebench",
                model_name_raw=model,
                metrics={"livecodebench_pct": float(pct)},
            )
        )
    return out

