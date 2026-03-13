from __future__ import annotations

import re
from typing import Iterable

from ...util import ValidationResult, coerce_float
from ..html_table import parse_first_html_table
from ..http import http_get
from ..types import ModelBenchmarkRow


AIDER_URL = "https://aider.chat/docs/leaderboards/"


def fetch_aider_leaderboards_html(timeout_s: int = 30) -> bytes:
    return http_get(AIDER_URL, timeout_s=timeout_s).body


def validate_aider_html(data: bytes) -> ValidationResult:
    # Any non-trivial HTML with at least one table.
    if not data or len(data) < 50_000:
        return ValidationResult(ok=False, row_count=0, error="html too small or empty")
    if b"<table" not in data.lower():
        return ValidationResult(ok=False, row_count=0, error="no <table> found")
    return ValidationResult(ok=True, row_count=1)


def _find_polyglot_col(headers: list[str]) -> int | None:
    for i, h in enumerate(headers):
        s = h.strip().lower()
        # Aider's page is itself the polyglot leaderboard; the main column is usually "Percent correct".
        if "percent" in s and "correct" in s:
            return i
    return None


def _find_model_col(headers: list[str]) -> int | None:
    for i, h in enumerate(headers):
        if h.strip().lower() == "model":
            return i
    for i, h in enumerate(headers):
        if "model" in h.strip().lower():
            return i
    return None


def _parse_pct(s: str) -> float | None:
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d+(\.\d+)?)", s.replace(",", ""))
    if not m:
        return None
    v = coerce_float(m.group(1))
    if v is None:
        return None
    # If it looks like 0-1, scale to percent.
    if 0.0 <= v <= 1.5 and "%" not in s:
        return v * 100.0
    return v


def parse_aider_polyglot_rows(html_bytes: bytes) -> list[ModelBenchmarkRow]:
    rows = parse_first_html_table(html_bytes.decode("utf-8", "replace"))
    if len(rows) < 2:
        return []
    headers = rows[0]
    model_i = _find_model_col(headers)
    pct_i = _find_polyglot_col(headers)
    if model_i is None or pct_i is None:
        return []

    out: list[ModelBenchmarkRow] = []
    for r in rows[1:]:
        if not r or len(r) <= max(model_i, pct_i):
            continue
        model_name = r[model_i].strip()
        pct = _parse_pct(r[pct_i])
        if not model_name or pct is None:
            continue
        out.append(ModelBenchmarkRow(source="aider", model_name_raw=model_name, metrics={"aider_polyglot_pct": pct}))
    return out


def aider_rows_row_count(rows: Iterable[ModelBenchmarkRow]) -> int:
    return sum(1 for _ in rows)
