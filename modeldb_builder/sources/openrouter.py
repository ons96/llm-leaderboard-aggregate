from __future__ import annotations

import json
from typing import Any

import requests

from ..util import ValidationResult, coerce_float, coerce_int
from .types import SourceProviderRecord


OPENROUTER_URL = "https://openrouter.ai/api/v1/models"


def fetch_openrouter_raw(timeout_s: int = 30) -> bytes:
    resp = requests.get(OPENROUTER_URL, timeout=timeout_s)
    resp.raise_for_status()
    return resp.content


def validate_openrouter_raw(data: bytes) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")

    if not isinstance(obj, dict) or not isinstance(obj.get("data"), list):
        return ValidationResult(ok=False, row_count=0, error="unexpected openrouter schema (expected {data: []})")

    n = len(obj["data"])
    if n < 50:
        return ValidationResult(ok=False, row_count=n, error=f"row count sanity check failed: {n} < 50")
    return ValidationResult(ok=True, row_count=n)


def parse_openrouter_records(raw_json: Any) -> list[SourceProviderRecord]:
    if not isinstance(raw_json, dict):
        return []
    models = raw_json.get("data")
    if not isinstance(models, list):
        return []

    out: list[SourceProviderRecord] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not mid:
            continue

        pricing = m.get("pricing") or {}
        # OpenRouter pricing is commonly a string dollars per token.
        in_cost = coerce_float(pricing.get("prompt"))
        out_cost = coerce_float(pricing.get("completion"))
        is_free = None
        if in_cost == 0.0 and out_cost == 0.0:
            is_free = 1

        out.append(
            SourceProviderRecord(
                source="openrouter",
                provider_name="openrouter",
                provider_model_id=str(mid),
                model_display_name=m.get("name") or None,
                developer=m.get("developer") if isinstance(m.get("developer"), str) else None,
                release_date=None,
                context_window_tokens=coerce_int(m.get("context_length") or m.get("max_context_length")),
                mode="chat",
                input_cost_per_token=in_cost,
                output_cost_per_token=out_cost,
                is_free_tier=is_free,
            )
        )

    return out
