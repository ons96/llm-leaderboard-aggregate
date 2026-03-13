from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ...util import ValidationResult, coerce_float
from ..http import http_get
from ..types import ModelBenchmarkRow


LLMSTATS_URL = "https://llm-stats.com"


def _parse_num(s: str) -> float | None:
    if s is None:
        return None
    s2 = str(s).strip().replace(",", "")
    if not s2:
        return None
    m = re.search(r"(-?\d+(\.\d+)?)", s2)
    if not m:
        return None
    return coerce_float(m.group(1))


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
        composite_i = None
        coding_i = None
        for i, h in enumerate(hnorm):
            if model_i is None and ("model" in h or "name" in h):
                model_i = i
            if composite_i is None and ("composite" in h or ("overall" in h and "score" in h)):
                composite_i = i
            if coding_i is None and ("coding" in h and "score" in h or h.strip() == "coding"):
                coding_i = i
        if model_i is None:
            continue

        rows: list[dict[str, Any]] = []
        for tr in trs[1:]:
            tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if not tds or model_i >= len(tds):
                continue
            model = (tds[model_i] or "").strip()
            if not model:
                continue
            metrics: dict[str, float] = {}
            if composite_i is not None and composite_i < len(tds):
                v = _parse_num(tds[composite_i])
                if v is not None:
                    metrics["llmstats_composite_score"] = float(v)
            if coding_i is not None and coding_i < len(tds):
                v = _parse_num(tds[coding_i])
                if v is not None:
                    metrics["llmstats_coding_score"] = float(v)
            if not metrics:
                continue
            rows.append({"model": model, **metrics})
        if len(rows) > len(best):
            best = rows
    return best


def fetch_llmstats_json(timeout_s: int = 30) -> tuple[str | None, bytes]:
    res = http_get(LLMSTATS_URL, timeout_s=timeout_s, retries=1)
    rows = _rows_from_html(res.body.decode("utf-8", "replace"))
    if not rows:
        raise RuntimeError("no llm-stats rows parsed")
    return res.url, json.dumps(rows, sort_keys=True).encode("utf-8")


def validate_llmstats_json(data: bytes) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")
    if not isinstance(obj, list) or len(obj) < 10:
        return ValidationResult(ok=False, row_count=0, error="unexpected json shape")
    return ValidationResult(ok=True, row_count=len(obj))


def parse_llmstats_rows(json_bytes: bytes) -> list[ModelBenchmarkRow]:
    obj = json.loads(json_bytes.decode("utf-8"))
    if not isinstance(obj, list):
        return []
    out: list[ModelBenchmarkRow] = []
    for r in obj:
        if not isinstance(r, dict):
            continue
        model = (r.get("model") or r.get("model_name") or r.get("name") or "").strip()
        if not model:
            continue
        metrics: dict[str, float] = {}
        cs = coerce_float(r.get("llmstats_composite_score"))
        if cs is not None:
            metrics["llmstats_composite_score"] = float(cs)
        cod = coerce_float(r.get("llmstats_coding_score"))
        if cod is not None:
            metrics["llmstats_coding_score"] = float(cod)
        if not metrics:
            continue
        out.append(ModelBenchmarkRow(source="llmstats", model_name_raw=model, metrics=metrics))
    return out

