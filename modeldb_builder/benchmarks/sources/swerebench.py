from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ...util import ValidationResult, coerce_float
from ..http import http_get
from ..types import ModelBenchmarkRow


_ROOT = "https://swe-rebench.com"
_CANDIDATE_JSON = [
    f"{_ROOT}/api/results",
    f"{_ROOT}/api/result",
    f"{_ROOT}/api/leaderboard",
    f"{_ROOT}/data.json",
    f"{_ROOT}/results.json",
]


def _coerce_pct(v: Any) -> float | None:
    fv = coerce_float(v)
    if fv is None:
        if isinstance(v, str):
            m = re.search(r"(\d+(\.\d+)?)", v.replace(",", ""))
            if m:
                fv = coerce_float(m.group(1))
    if fv is None:
        return None
    if 0.0 <= fv <= 1.0:
        return float(fv) * 100.0
    return float(fv)


def _rows_from_json_obj(obj: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[Any] = []
    if isinstance(obj, list):
        candidates = obj
    elif isinstance(obj, dict):
        for k in ("results", "data", "leaderboard", "rows"):
            v = obj.get(k)
            if isinstance(v, list):
                candidates = v
                break
        if not candidates and all(isinstance(v, dict) for v in obj.values()):
            candidates = list(obj.values())

    for r in candidates:
        if not isinstance(r, dict):
            continue
        model = (
            r.get("model")
            or r.get("model_name")
            or r.get("name")
            or r.get("Model")
            or r.get("Model Name")
            or ""
        )
        if isinstance(model, dict):
            model = model.get("name") or model.get("id") or ""
        model_s = str(model).strip()
        if not model_s:
            continue
        pct = (
            r.get("swerebench_pct")
            or r.get("resolution_rate")
            or r.get("resolve_rate")
            or r.get("resolved_pct")
            or r.get("resolved")
            or r.get("score")
        )
        pv = _coerce_pct(pct)
        if pv is None:
            continue
        rows.append({"model": model_s, "swerebench_pct": float(pv)})
    return rows


def _rows_from_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    best: list[dict[str, Any]] = []
    for t in tables:
        trs = t.find_all("tr")
        if len(trs) < 2:
            continue
        headers = [th.get_text(" ", strip=True) for th in trs[0].find_all(["th", "td"])]
        hnorm = [h.lower() for h in headers]
        model_i = None
        score_i = None
        for i, h in enumerate(hnorm):
            if model_i is None and ("model" in h or "system" in h):
                model_i = i
            if score_i is None and (
                ("resolve" in h and ("%" in h or "rate" in h))
                or ("resolution" in h and ("%" in h or "rate" in h))
                or ("resolved" in h and ("%" in h or "rate" in h))
            ):
                score_i = i
        if model_i is None:
            continue
        if score_i is None:
            for i, h in enumerate(hnorm):
                if "resolve" in h or "resolved" in h or "resolution" in h or "score" in h:
                    score_i = i
                    break
        if score_i is None:
            continue
        rows: list[dict[str, Any]] = []
        for tr in trs[1:]:
            tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if not tds:
                continue
            if model_i >= len(tds) or score_i >= len(tds):
                continue
            model = (tds[model_i] or "").strip()
            pv = _coerce_pct(tds[score_i] or "")
            if not model or pv is None:
                continue
            rows.append({"model": model, "swerebench_pct": float(pv)})
        if len(rows) > len(best):
            best = rows
    return best


def _playwright_render_html(timeout_s: int = 30) -> str:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"playwright not available: {e}") from e

    with sync_playwright() as p:  # pragma: no cover
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(_ROOT, wait_until="networkidle", timeout=timeout_s * 1000)
            return page.content()
        finally:
            browser.close()


def fetch_swerebench_json(timeout_s: int = 30) -> tuple[str | None, bytes]:
    last_err: Exception | None = None

    # Try JSON endpoints first.
    for url in _CANDIDATE_JSON:
        try:
            res = http_get(url, timeout_s=timeout_s, retries=0)
            if not res.body or res.body[:1] not in (b"{", b"["):
                continue
            obj = json.loads(res.body.decode("utf-8"))
            rows = _rows_from_json_obj(obj)
            if rows:
                return url, json.dumps(rows, sort_keys=True).encode("utf-8")
        except Exception as e:
            last_err = e
            continue

    # Then try HTML scraping.
    try:
        res = http_get(_ROOT, timeout_s=timeout_s, retries=1)
        rows = _rows_from_html(res.body.decode("utf-8", "replace"))
        if rows:
            return _ROOT, json.dumps(rows, sort_keys=True).encode("utf-8")
    except Exception as e:
        last_err = e

    # Finally, Playwright best-effort for JS-rendered pages.
    try:
        html = _playwright_render_html(timeout_s=timeout_s)
        rows = _rows_from_html(html)
        if rows:
            return _ROOT, json.dumps(rows, sort_keys=True).encode("utf-8")
    except Exception as e:
        last_err = e

    if last_err:
        raise last_err
    raise RuntimeError("no SWE-rebench endpoints tried")


def validate_swerebench_json(data: bytes) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")
    if not isinstance(obj, list) or len(obj) < 5:
        return ValidationResult(ok=False, row_count=0, error="unexpected json shape")
    return ValidationResult(ok=True, row_count=len(obj))


def parse_swerebench_rows(json_bytes: bytes) -> list[ModelBenchmarkRow]:
    obj = json.loads(json_bytes.decode("utf-8"))
    if not isinstance(obj, list):
        return []
    out: list[ModelBenchmarkRow] = []
    for r in obj:
        if not isinstance(r, dict):
            continue
        model = (r.get("model") or r.get("model_name") or r.get("name") or "").strip()
        pv = _coerce_pct(r.get("swerebench_pct"))
        if not model or pv is None:
            continue
        out.append(
            ModelBenchmarkRow(
                source="swerebench",
                model_name_raw=model,
                metrics={"swerebench_pct": float(pv)},
            )
        )
    return out

