from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ...util import ValidationResult, coerce_float
from ..http import http_get
from ..types import ModelBenchmarkRow


_URLS = [
    "https://www.swebench.com/verified.html",
    "https://www.swebench.com",
    "https://raw.githubusercontent.com/swe-bench/experiments/main/evaluation/verified/README.md",
    # Best-effort probes for dataset-hosted exports (shape may change over time).
    "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/main/leaderboard.csv",
    "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/main/README.md",
]


def _parse_pct(s: str) -> float | None:
    if not s:
        return None
    s2 = s.strip().replace(",", "")
    m = re.search(r"(\d+(\.\d+)?)\s*%?", s2)
    if not m:
        return None
    v = coerce_float(m.group(1))
    if v is None:
        return None
    # If the source accidentally provides 0-1, scale to 0-100.
    if 0.0 <= v <= 1.0:
        return float(v) * 100.0
    return float(v)


def _rows_from_html(html: str) -> list[dict[str, Any]]:
    # swebench.com renders the leaderboard client-side, but embeds the raw JSON in the page.
    soup = BeautifulSoup(html, "lxml")
    data_script = soup.find("script", {"id": "leaderboard-data"})
    if data_script and data_script.get_text(strip=True):
        try:
            root = json.loads(data_script.get_text())
        except Exception:
            root = None
        if isinstance(root, list):
            # Prefer "bash-only" (LM-only, mini-swe-agent) when present.
            preferred = None
            for d in root:
                if isinstance(d, dict) and str(d.get("name") or "").strip().lower() == "bash-only":
                    preferred = d
                    break
            if preferred is None:
                for d in root:
                    if isinstance(d, dict) and "verified" in str(d.get("name") or "").strip().lower():
                        preferred = d
                        break
            if preferred and isinstance(preferred.get("results"), list):
                rows: list[dict[str, Any]] = []
                for r in preferred["results"]:
                    if not isinstance(r, dict):
                        continue
                    model = str(r.get("name") or "").strip()
                    pct = _parse_pct(str(r.get("resolved") or ""))
                    if not model or pct is None:
                        continue
                    rows.append({"model": model, "swe_bench_verified_pct": float(pct)})
                if rows:
                    return rows

    # Fallback: attempt HTML table parsing if present.
    tables = soup.find_all("table")
    best: list[dict[str, Any]] = []
    for t in tables:
        trs = t.find_all("tr")
        if len(trs) < 2:
            continue
        headers = [
            th.get_text(" ", strip=True) for th in trs[0].find_all(["th", "td"])
        ]
        hnorm = [h.lower() for h in headers]
        model_i = None
        score_i = None
        for i, h in enumerate(hnorm):
            if model_i is None and ("model" in h or "system" in h or "name" == h):
                model_i = i
            if score_i is None and ("verified" in h or "resolved" in h or "pass" in h):
                score_i = i
        if model_i is None or score_i is None:
            continue
        rows: list[dict[str, Any]] = []
        for tr in trs[1:]:
            tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if not tds or model_i >= len(tds) or score_i >= len(tds):
                continue
            model = (tds[model_i] or "").strip()
            pct = _parse_pct(tds[score_i] or "")
            if not model or pct is None:
                continue
            rows.append({"model": model, "swe_bench_verified_pct": float(pct)})
        if len(rows) > len(best):
            best = rows
    return best


def _rows_from_markdown(md: str) -> list[dict[str, Any]]:
    # Parse the first markdown pipe table that has model + verified/resolved columns.
    lines = [ln.rstrip("\n") for ln in md.splitlines()]
    for i in range(len(lines) - 2):
        header = lines[i]
        sep = lines[i + 1]
        if "|" not in header or "|" not in sep:
            continue
        if not re.search(r"^\s*\|?\s*:?-{3,}", sep.replace("|", "").strip()):
            continue
        headers = [h.strip() for h in header.strip().strip("|").split("|")]
        hnorm = [h.lower() for h in headers]
        model_i = None
        score_i = None
        for j, h in enumerate(hnorm):
            if model_i is None and ("model" in h or "system" in h):
                model_i = j
            if score_i is None and ("verified" in h or "resolved" in h):
                score_i = j
        if model_i is None or score_i is None:
            continue

        rows: list[dict[str, Any]] = []
        for ln in lines[i + 2 :]:
            if "|" not in ln:
                break
            parts = [p.strip() for p in ln.strip().strip("|").split("|")]
            if len(parts) < max(model_i, score_i) + 1:
                continue
            model = (parts[model_i] or "").strip()
            pct = _parse_pct(parts[score_i] or "")
            if not model or pct is None:
                continue
            rows.append({"model": model, "swe_bench_verified_pct": float(pct)})
        if rows:
            return rows
    return []


def _rows_from_csv(text: str) -> list[dict[str, Any]]:
    import csv
    import io

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    fn = [f.strip().lower() for f in reader.fieldnames]
    model_col = None
    score_col = None
    for f in reader.fieldnames:
        fl = f.strip().lower()
        if model_col is None and ("model" in fl or "system" in fl or "name" == fl):
            model_col = f
        if score_col is None and ("verified" in fl or "resolved" in fl or "pass" in fl):
            score_col = f
    if model_col is None or score_col is None:
        return []
    rows: list[dict[str, Any]] = []
    for r in reader:
        model = (r.get(model_col) or "").strip()
        pct = _parse_pct(r.get(score_col) or "")
        if not model or pct is None:
            continue
        rows.append({"model": model, "swe_bench_verified_pct": float(pct)})
    return rows


def fetch_swebench_json(timeout_s: int = 30) -> tuple[str | None, bytes]:
    last_err: Exception | None = None
    for url in _URLS:
        try:
            res = http_get(url, timeout_s=timeout_s, retries=1)
            text = res.body.decode("utf-8", "replace")
            rows: list[dict[str, Any]]
            if url.endswith(".md") or "text/markdown" in (res.content_type or "").lower():
                rows = _rows_from_markdown(text)
            elif url.endswith(".csv") or "text/csv" in (res.content_type or "").lower():
                rows = _rows_from_csv(text)
            else:
                rows = _rows_from_html(text)
            if rows:
                return url, json.dumps(rows, sort_keys=True).encode("utf-8")
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("no SWE-bench endpoints tried")


def validate_swebench_json(data: bytes) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")
    if not isinstance(obj, list) or len(obj) < 5:
        return ValidationResult(ok=False, row_count=0, error="unexpected json shape")
    return ValidationResult(ok=True, row_count=len(obj))


def parse_swebench_rows(json_bytes: bytes) -> list[ModelBenchmarkRow]:
    obj = json.loads(json_bytes.decode("utf-8"))
    if not isinstance(obj, list):
        return []
    out: list[ModelBenchmarkRow] = []
    for r in obj:
        if not isinstance(r, dict):
            continue
        model = (r.get("model") or r.get("model_name") or r.get("name") or "").strip()
        pct = coerce_float(r.get("swe_bench_verified_pct"))
        if not model or pct is None:
            continue
        out.append(
            ModelBenchmarkRow(
                source="swebench",
                model_name_raw=model,
                metrics={"swe_bench_verified_pct": float(pct)},
            )
        )
    return out
