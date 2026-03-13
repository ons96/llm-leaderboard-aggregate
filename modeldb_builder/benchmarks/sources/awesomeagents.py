from __future__ import annotations

import re

from ...util import ValidationResult, coerce_float
from ..html_table import parse_first_html_table
from ..http import http_get
from ..types import ProviderPerfRow


AWESOMEAGENTS_URL = "https://awesomeagents.ai/leaderboards/ai-speed-latency-leaderboard/"


def fetch_awesomeagents_html(timeout_s: int = 30) -> bytes:
    return http_get(AWESOMEAGENTS_URL, timeout_s=timeout_s).body


def validate_awesomeagents_html(data: bytes) -> ValidationResult:
    if not data or len(data) < 10_000:
        return ValidationResult(ok=False, row_count=0, error="html too small or empty")
    if b"<table" not in data.lower():
        return ValidationResult(ok=False, row_count=0, error="no <table> found")
    return ValidationResult(ok=True, row_count=1)


def _col_idx(headers: list[str], *needles: str) -> int | None:
    for i, h in enumerate(headers):
        s = h.strip().lower()
        if all(n in s for n in needles):
            return i
    return None


def _parse_number(s: str) -> float | None:
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d+(\.\d+)?)", s.replace(",", ""))
    if not m:
        return None
    return coerce_float(m.group(1))


def parse_awesomeagents_speed_rows(html_bytes: bytes) -> list[ProviderPerfRow]:
    rows = parse_first_html_table(html_bytes.decode("utf-8", "replace"))
    if len(rows) < 2:
        return []
    headers = rows[0]
    provider_i = _col_idx(headers, "provider") or 0
    model_i = _col_idx(headers, "model") or 1
    tps_i = _col_idx(headers, "token", "sec") or _col_idx(headers, "tokens", "sec")
    ttft_i = _col_idx(headers, "ttft") or _col_idx(headers, "first", "token")

    out: list[ProviderPerfRow] = []
    for r in rows[1:]:
        if not r:
            continue
        if provider_i >= len(r) or model_i >= len(r):
            continue
        provider = r[provider_i].strip()
        model = r[model_i].strip()
        if not provider or not model:
            continue
        metrics: dict[str, float] = {}
        if tps_i is not None and tps_i < len(r):
            v = _parse_number(r[tps_i])
            if v is not None:
                metrics["avg_tokens_per_second"] = v
        if ttft_i is not None and ttft_i < len(r):
            v = _parse_number(r[ttft_i])
            if v is not None:
                # Sometimes seconds; assume ms if it says ms.
                if "ms" in r[ttft_i].lower():
                    metrics["avg_ttft_ms"] = v
                else:
                    metrics["avg_ttft_ms"] = v * 1000.0 if v < 60 else v
        if not metrics:
            continue
        out.append(
            ProviderPerfRow(source="awesomeagents", provider_name_raw=provider, model_name_raw=model, metrics=metrics)
        )
    return out

