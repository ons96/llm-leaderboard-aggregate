from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ...util import ValidationResult, coerce_float
from ..http import http_get
from ..types import ModelBenchmarkRow


HF_LEADERBOARD_CSV = (
    "https://huggingface.co/datasets/lmarena-ai/chatbot-arena-leaderboard/resolve/main/leaderboard.csv"
)
HF_SPACE = "https://huggingface.co/spaces/lmarena-ai/arena-leaderboard"
OPENLM_MIRROR = "https://openlm.ai/chatbot-arena/"


def _guess_cols(fieldnames: list[str]) -> tuple[str | None, str | None, str | None]:
    if not fieldnames:
        return None, None, None
    norm = {f: f.strip().lower().replace(" ", "_") for f in fieldnames}
    model_col = None
    overall_col = None
    coding_col = None
    for f, n in norm.items():
        if model_col is None and (n in ("model", "model_name", "name") or "model" in n):
            model_col = f
    for f, n in norm.items():
        if "elo" not in n:
            continue
        if coding_col is None and ("coding" in n or "code" in n):
            coding_col = f
        if overall_col is None and ("coding" not in n and "code" not in n):
            overall_col = f
    return model_col, overall_col, coding_col


def _rows_from_csv(csv_bytes: bytes) -> list[dict[str, Any]]:
    text = csv_bytes.decode("utf-8", "replace")
    reader = csv.DictReader(io.StringIO(text))
    model_col, overall_col, coding_col = _guess_cols(reader.fieldnames or [])
    if not model_col:
        return []
    out: list[dict[str, Any]] = []
    for r in reader:
        model = (r.get(model_col) or "").strip()
        if not model:
            continue
        metrics: dict[str, float] = {}
        if overall_col:
            v = coerce_float(r.get(overall_col))
            if v is not None:
                metrics["arena_elo"] = float(v)
        if coding_col:
            v = coerce_float(r.get(coding_col))
            if v is not None:
                metrics["arena_elo_coding"] = float(v)
        if not metrics:
            continue
        out.append({"model": model, **metrics})
    return out


def _parse_number(s: str) -> float | None:
    if not s:
        return None
    m = re.search(r"(\d+(\.\d+)?)", s.replace(",", ""))
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
        overall_i = None
        coding_i = None
        for i, h in enumerate(hnorm):
            if model_i is None and ("model" in h or "name" in h):
                model_i = i
            if overall_i is None and "elo" in h and "coding" not in h and "code" not in h:
                overall_i = i
            if coding_i is None and ("coding" in h or h.strip() == "coding" or "code" == h.strip()):
                coding_i = i
        if model_i is None:
            continue
        if overall_i is None and coding_i is None:
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
            if overall_i is not None and overall_i < len(tds):
                v = _parse_number(tds[overall_i])
                if v is not None:
                    metrics["arena_elo"] = float(v)
            if coding_i is not None and coding_i < len(tds):
                v = _parse_number(tds[coding_i])
                if v is not None:
                    metrics["arena_elo_coding"] = float(v)
            if not metrics:
                continue
            rows.append({"model": model, **metrics})
        if len(rows) > len(best):
            best = rows
    return best


def fetch_lmarena_json(timeout_s: int = 30) -> tuple[str | None, bytes]:
    # a) HF dataset CSV (preferred)
    try:
        res = http_get(HF_LEADERBOARD_CSV, timeout_s=timeout_s, retries=1)
        rows = _rows_from_csv(res.body)
        if rows:
            return res.url, json.dumps(rows, sort_keys=True).encode("utf-8")
    except Exception:
        pass

    # b) HF space (try to find downloadable CSVs, otherwise parse HTML tables)
    try:
        res = http_get(HF_SPACE, timeout_s=timeout_s, retries=1)
        html = res.body.decode("utf-8", "replace")
        rows = _rows_from_html(html)
        if rows:
            return res.url, json.dumps(rows, sort_keys=True).encode("utf-8")
    except Exception:
        pass

    # c) OpenLM mirror
    res = http_get(OPENLM_MIRROR, timeout_s=timeout_s, retries=1)
    rows = _rows_from_html(res.body.decode("utf-8", "replace"))
    if not rows:
        raise RuntimeError("no LM Arena rows parsed from any endpoint")
    return res.url, json.dumps(rows, sort_keys=True).encode("utf-8")


def validate_lmarena_json(data: bytes) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")
    if not isinstance(obj, list) or len(obj) < 25:
        return ValidationResult(ok=False, row_count=0, error="unexpected json shape/too few rows")
    return ValidationResult(ok=True, row_count=len(obj))


def parse_lmarena_rows(json_bytes: bytes) -> list[ModelBenchmarkRow]:
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
        v = coerce_float(r.get("arena_elo"))
        if v is not None:
            metrics["arena_elo"] = float(v)
        v = coerce_float(r.get("arena_elo_coding"))
        if v is not None:
            metrics["arena_elo_coding"] = float(v)
        if not metrics:
            continue
        out.append(ModelBenchmarkRow(source="lmarena", model_name_raw=model, metrics=metrics))
    return out
